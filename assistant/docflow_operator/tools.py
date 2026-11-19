"""Typed read-only tools over DocFlow, plus local draft files.

The model is only allowed to pick a tool name and fill in a small argument object.
This module owns the meaning of every argument: it validates the call against a JSON
schema and rejects anything that does not fit instead of guessing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .docflow import DocFlowClient, DocFlowError


class ToolError(RuntimeError):
    """A tool could not do its job; the message is safe to show to the model."""


class ArgumentError(ToolError):
    """The model produced arguments that do not match the tool schema."""


# --- tool catalogue ---------------------------------------------------------
# Kept declarative: the same structure drives the prompt, the constrained decoding
# schema and argument validation, so they cannot drift apart.

TOOLS: list[dict] = [
    {
        'name': 'list_queue',
        'description': 'List documents waiting for human review, newest first.',
        'args': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50,
                          'description': 'how many documents to show'},
            },
            'required': ['limit'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'get_document',
        'description': 'Show the parsed fields, route and issues of one document.',
        'args': {
            'type': 'object',
            'properties': {'doc_id': {'type': 'string', 'description': "document id from the queue, or 'latest' for the newest one"}},
            'required': ['doc_id'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'check_duplicate',
        'description': 'Check whether a pending document is already booked in DocFlow history.',
        'args': {
            'type': 'object',
            'properties': {'doc_id': {'type': 'string', 'description': "document id from the queue, or 'latest' for the newest one"}},
            'required': ['doc_id'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'draft_letter',
        'description': ('Write a local draft letter for a document. It is saved on disk and '
                        'never sent: a human copies or sends it.'),
        'args': {
            'type': 'object',
            'properties': {
                'doc_id': {'type': 'string', 'description': "document id from the queue, or 'latest' for the newest one"},
                'kind': {'type': 'string', 'enum': ['payment_request', 'approval_request', 'supplier_reply']},
            },
            'required': ['doc_id', 'kind'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'summarise',
        'description': 'Summary of the review queue and recent history by destination and amount.',
        'args': {
            'type': 'object',
            'properties': {'period': {'type': 'string', 'enum': ['today', 'week', 'all']}},
            'required': ['period'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'propose_confirm',
        'description': ('Propose a document for confirmation. This only writes a proposal; '
                        'a human confirms it in DocFlow.'),
        'args': {
            'type': 'object',
            'properties': {'doc_id': {'type': 'string', 'description': "document id from the queue, or 'latest' for the newest one"}},
            'required': ['doc_id'],
            'additionalProperties': False,
        },
    },
]

FINISH_TOOL = {
    'name': 'finish',
    'description': 'Final answer to the user. Use it when the task is done.',
    'args': {
        'type': 'object',
        'properties': {'answer': {'type': 'string'}},
        'required': ['answer'],
        'additionalProperties': False,
    },
}


def tools_by_name() -> dict[str, dict]:
    return {tool['name']: tool for tool in TOOLS}


def step_schema() -> dict:
    """JSON schema for one planner step: exactly one tool with its arguments."""
    variants = []
    for tool in [*TOOLS, FINISH_TOOL]:
        args = tool['args']
        variants.append({
            'type': 'object',
            'properties': {'tool': {'const': tool['name']}, **args['properties']},
            'required': ['tool', *args.get('required', [])],
            'additionalProperties': False,
        })
    return {'oneOf': variants}


def tool_prompt() -> str:
    lines = []
    for tool in TOOLS:
        arguments = ', '.join(tool['args'].get('properties', {})) or 'no arguments'
        lines.append(f"- {tool['name']}({arguments}): {tool['description']}")
    lines.append(f"- {FINISH_TOOL['name']}(answer): {FINISH_TOOL['description']}")
    return '\n'.join(lines)


# --- argument validation ----------------------------------------------------
_TYPE_CHECKS = {
    'string': lambda value: isinstance(value, str),
    'integer': lambda value: isinstance(value, int) and not isinstance(value, bool),
    'number': lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    'boolean': lambda value: isinstance(value, bool),
    'object': lambda value: isinstance(value, dict),
}


def validate_arguments(tool_name: str, arguments: dict) -> None:
    """Raise ArgumentError when the call does not match the tool schema."""
    tool = tools_by_name().get(tool_name)
    if tool is None:
        raise ArgumentError(f'unknown tool {tool_name!r}')
    if not isinstance(arguments, dict):
        raise ArgumentError('arguments must be a JSON object')
    schema = tool['args']
    properties = schema.get('properties', {})
    for name in schema.get('required', []):
        if name not in arguments:
            raise ArgumentError(f'missing argument {name!r} for {tool_name}')
    extra = set(arguments) - set(properties)
    if extra and not schema.get('additionalProperties', False):
        raise ArgumentError(f'unexpected argument(s) {", ".join(sorted(extra))} for {tool_name}')
    for name, value in arguments.items():
        spec = properties[name]
        expected_type = spec.get('type')
        if expected_type and not _TYPE_CHECKS[expected_type](value):
            raise ArgumentError(f'argument {name!r} must be {expected_type}')
        if 'enum' in spec and value not in spec['enum']:
            raise ArgumentError(f'argument {name!r} must be one of {spec["enum"]}')
        if expected_type == 'integer' and 'minimum' in spec and value < spec['minimum']:
            raise ArgumentError(f'argument {name!r} must be >= {spec["minimum"]}')
        if expected_type == 'integer' and 'maximum' in spec and value > spec['maximum']:
            raise ArgumentError(f'argument {name!r} must be <= {spec["maximum"]}')
        if expected_type == 'string' and not value.strip():
            raise ArgumentError(f'argument {name!r} must not be empty')


# --- helpers ----------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _routes(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        name = (item.get('route') or {}).get('destination') or 'Unrouted'
        counts[name] = counts.get(name, 0) + 1
    return counts


def _money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return 'n/a'
    suffix = f' {currency}' if currency else ''
    return f'{value:,.2f}{suffix}'


def _field(item: dict, name: str):
    fields = item.get('fields') or {}
    value = fields.get(name)
    if isinstance(value, dict):  # /api/documents/{id} wraps values with confidence
        return value.get('value')
    return value


def _document_brief(item: dict, full: bool = False) -> dict:
    brief = {
        'id': item.get('id'),
        'type': item.get('doc_type') or item.get('type'),
        'number': _field(item, 'number'),
        'supplier': _field(item, 'supplier'),
        'total': _field(item, 'total'),
        'currency': _field(item, 'currency'),
        'route': (item.get('route') or {}).get('destination'),
    }
    if full:
        brief.update({
            'date': _field(item, 'date'),
            'confidence': item.get('confidence'),
            'issues': list(item.get('issues') or [])[:3],
        })
    return brief


@dataclass
class Toolbox:
    docflow: DocFlowClient
    drafts_dir: Path

    # -- lookup --------------------------------------------------------------
    def _pending(self, doc_id: str) -> dict:
        items = self.docflow.inbox(limit=50)
        if doc_id.strip().lower() in ('latest', 'first', 'newest', 'next'):
            if items:
                return items[0]
            raise ToolError('the review queue is empty')
        for item in items:
            if item.get('id') == doc_id:
                return item
        raise ToolError(f'document {doc_id!r} is not in the review queue (use list_queue or doc_id "latest")')

    def _confirmed(self, limit: int = 200) -> list[dict]:
        return self.docflow.documents(limit=limit)

    def _any(self, doc_id: str) -> dict:
        try:
            return self._pending(doc_id)
        except ToolError:
            for item in self._confirmed():
                if item.get('id') == doc_id:
                    return item
            try:
                return self.docflow.document(doc_id)
            except DocFlowError as error:
                raise ToolError(f'unknown document {doc_id!r}') from error

    # -- tools ---------------------------------------------------------------
    def list_queue(self, limit: int) -> dict:
        items = self.docflow.inbox(limit=limit)
        # The count is the whole requested queue, not the five briefs sent to the model:
        # a model answering "how many documents" used to under-report a longer queue.
        documents = [_document_brief(item) for item in items[:5]]
        by_code = lambda code: [item.get('id') for item in items  # noqa: E731
                                if (item.get('route') or {}).get('destination_code') == code]
        return {
            'count': len(items),
            'documents': documents,
            'routes': _routes(items),
            'needs_approval': by_code('approval'),
            'to_pay': by_code('payments'),
            'to_book': by_code('accounting'),
            'summary': f'{len(items)} document(s) waiting for review',
        }

    def get_document(self, doc_id: str) -> dict:
        item = self._any(doc_id)
        brief = _document_brief(item, full=True)
        brief['source'] = item.get('source')
        brief['uploaded_at'] = item.get('uploaded_at')
        brief['route_reason'] = (item.get('route') or {}).get('reason')
        return {'document': brief, 'summary': f'{doc_id}: {brief.get("type") or "document"} from {brief.get("supplier") or "unknown supplier"}'}

    def check_duplicate(self, doc_id: str) -> dict:
        pending = self._pending(doc_id)
        number = _field(pending, 'number')
        supplier = (_field(pending, 'supplier') or '').strip().casefold()
        date = _field(pending, 'date')
        total = _field(pending, 'total')
        tax_id = _field(pending, 'tax_id')
        history = self._confirmed()
        matches = []
        for row in history:
            fields = row.get('fields') or {}
            same_number = number and fields.get('number') == number
            same_supplier = supplier and (fields.get('supplier') or '').strip().casefold() == supplier
            same_date = date and fields.get('date') == date
            same_total = total is not None and fields.get('total') is not None and abs(float(fields['total']) - float(total)) < 0.01
            same_tax = tax_id and fields.get('tax_id') == tax_id
            if (same_supplier and (same_number or same_total or same_date)) or (same_tax and same_number):
                matches.append({
                    'id': row.get('id'),
                    'number': fields.get('number'),
                    'supplier': fields.get('supplier'),
                    'date': fields.get('date'),
                    'total': fields.get('total'),
                    'destination': row.get('destination'),
                    'confirmed_at': row.get('confirmed_at'),
                    'reason': 'same supplier and number' if same_number else 'same supplier and date/amount',
                })
        duplicate = bool(matches)
        return {
            'doc_id': doc_id,
            'duplicate': duplicate,
            'matches': matches,
            'checked_against': len(history),
            'summary': ('duplicate: ' + ', '.join(m['id'] or '?' for m in matches)) if duplicate else 'no duplicate found in history',
        }

    def summarise(self, period: str) -> dict:
        if period not in ('today', 'week', 'all'):
            raise ArgumentError(f'unknown period {period!r}')
        since = None
        now = _now()
        if period == 'today':
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'week':
            since = now - timedelta(days=7)

        # "Waiting" is the current queue: a document uploaded yesterday is still waiting
        # today. Only the confirmed history is filtered by the requested period.
        featured = self.docflow.inbox(limit=50)
        history = [row for row in self._confirmed() if since is None or (_parse_ts(row.get('confirmed_at')) or now) >= since]

        def totals(items: list[dict]) -> float:
            result = 0.0
            for item in items:
                value = _field(item, 'total')
                if isinstance(value, (int, float)):
                    result += float(value)
            return result

        destinations = _routes(featured)
        approval = [item for item in featured if (item.get('route') or {}).get('destination_code') == 'approval']
        payments = [item for item in featured if (item.get('route') or {}).get('destination_code') == 'payments']
        accounting = [item for item in featured if (item.get('route') or {}).get('destination_code') == 'accounting']
        return {
            'period': period,
            'waiting': {
                'count': len(featured),
                'amount': round(totals(featured), 2),
                'by_route': destinations,
                'needs_approval': len(approval),
                'to_pay': len(payments),
                'to_book': len(accounting),
            },
            'confirmed': {'count': len(history), 'amount': round(totals(history), 2)},
            'summary': f'{len(featured)} waiting ({destinations}); {len(history)} confirmed',
        }

    def draft_letter(self, doc_id: str, kind: str) -> dict:
        if kind not in ('payment_request', 'approval_request', 'supplier_reply'):
            raise ArgumentError(f'unknown letter kind {kind!r}')
        item = self._any(doc_id)
        doc_id = item.get('id') or doc_id
        brief = _document_brief(item)
        currency = brief.get('currency') or 'UAH'
        number = brief.get('number') or doc_id
        supplier = brief.get('supplier') or 'supplier'
        total = _money(brief.get('total'), currency)
        date = brief.get('date') or 'the document date'
        subjects = {
            'payment_request': f'Payment request for document {number}',
            'approval_request': f'Approval request for document {number}',
            'supplier_reply': f'Reply to {supplier} about document {number}',
        }
        bodies = {
            'payment_request': (
                f'Please prepare a payment for document {number} dated {date} '
                f'for {total}. The document is waiting for human review in DocFlow.\n'
                'Payment itself is approved and executed by a person, not by this assistant.'
            ),
            'approval_request': (
                f'Document {number} dated {date} from {supplier} for {total} needs approval.\n'
                'The assistant only proposes; the decision and the confirmation stay with a person.'
            ),
            'supplier_reply': (
                f'We received your document {number} dated {date} for {total}.\n'
                'It is in review; we will come back with the next step shortly.'
            ),
        }
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        path = self.drafts_dir / f'{doc_id}-{kind}.md'
        text = f'# {subjects[kind]}\n\n{bodies[kind]}\n'
        path.write_text(text, encoding='utf-8')
        return {
            'doc_id': doc_id,
            'kind': kind,
            'path': str(path),
            'letter': text,
            'summary': f'draft {kind} for document {number} saved locally (not sent)',
        }

    def propose_confirm(self, doc_id: str) -> dict:
        item = self._any(doc_id)
        doc_id = item.get('id') or doc_id
        brief = _document_brief(item)
        route = brief.get('route') or 'human review'
        return {
            'doc_id': doc_id,
            'proposal': f'Confirm {brief.get("type") or "document"} {brief.get("number") or doc_id} '
                        f'from {brief.get("supplier") or "unknown supplier"} for '
                        f'{_money(brief.get("total"), brief.get("currency"))} -> {route}.',
            'human_action': 'A person opens DocFlow, reviews the fields and presses Confirm. The assistant cannot confirm.',
            'confirmed': False,
            'summary': 'proposal only: human confirms in DocFlow',
        }

    # -- dispatch ------------------------------------------------------------
    def run(self, tool_name: str, arguments: dict) -> dict:
        # validate_arguments also rejects unknown names, so only real tools get here.
        validate_arguments(tool_name, arguments)
        return getattr(self, tool_name)(**arguments)


def result_text(result: dict, max_chars: int = 700) -> str:
    """Compact JSON for the model.

    Prompt processing is the slow part on this CPU: a full 20-document payload added
    ~600 tokens per step and turned one plan into minutes. Results are therefore
    flattened and capped; the UI still shows the full object from the trace.
    """
    compact = {key: value for key, value in result.items() if key != 'letter'}
    if 'letter' in result:
        compact['letter_preview'] = result['letter'][:100]
    text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + '...'
    return text


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
