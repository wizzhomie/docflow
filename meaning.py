"""The model says where a value is printed; the code copies it.

The lesson this implements comes from FormaCV: when code decides what a line
means, every wording variant becomes a regex; when a model produces the value
itself, a confident wrong number is one prompt away. The split that survived is
narrower:

- the model is given numbered lines and answers with a pointer — line number and
  the exact substring as printed — for each field;
- the code checks that the substring really is on that line, copies it, parses
  the format and writes the value;
- a pointer that does not resolve is dropped, not guessed: the deterministic
  parser keeps its value and the field is marked as coming from the parser.

So the model cannot invent an amount, and the code never interprets meaning. The
record of where the value came from is kept with the field, which is the
provenance the review has asked for since the first architecture pass.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger('docflow')

FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')
DEFAULT_URL = 'http://127.0.0.1:8080/v1/chat/completions'
KEY_FILE = Path.home() / '.config' / 'docflow' / 'models.env'
# A long text PDF can carry hundreds of lines; the fields of an invoice are in the
# first pages, and the prompt has a budget. The limit is documented, not silent:
# the prompt says how many lines were shown.
MAX_LINES = int(os.environ.get('DOCFLOW_MEANING_LINES', '200'))
# A remote endpoint answers in a second; a model on the server's own two cores needs
# tens of seconds, and the first request after a restart needs minutes because the
# weights are read cold. Too short a timeout does not fail loudly — it falls back to
# the rules and looks exactly like "the model had nothing to say".
TIMEOUT = float(os.environ.get('DOCFLOW_MEANING_TIMEOUT', '30'))
# Which fields are worth asking about. A field the rules answered and no invariant
# refused does not need the model: asking costs answer tokens — the answer is the
# slow part of the request on a CPU — and gives the model a chance to overwrite a
# value the code already approved. Below this weight a field is treated as unknown.
GAP_CONFIDENCE = float(os.environ.get('DOCFLOW_MEANING_GAP', '0.6'))
# The two fields no deterministic check can confirm on a text extraction. Measured on
# the twelve real ProZorro invoices: the rules are right about `total` in 4 cases and
# about `vat` in 2, and every wrong one arrives with full confidence — a total copied
# from the subtotal line and a VAT of zero on an invoice that prints VAT are both
# well-formed values. So they are asked about always; the model is the only reader
# that can tell the total from the line above it.
ALWAYS_ASK = tuple(
    name.strip()
    for name in os.environ.get('DOCFLOW_MEANING_ALWAYS', 'total,vat').split(',')
    if name.strip()
)

MONTHS = {
    'січня': 1, 'лютого': 2, 'березня': 3, 'квітня': 4, 'травня': 5, 'червня': 6,
    'липня': 7, 'серпня': 8, 'вересня': 9, 'жовтня': 10, 'листопада': 11, 'грудня': 12,
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
}

PROMPT = """Тобі дано текст українського документа, рядки пронумеровані.
Вкажи, ДЕ надруковано кожне поле. Не вигадуй значення і не переноси його з іншого поля.

Поверни ЛИШЕ JSON:
{{"fields": {{"поле": {{"line": <номер рядка>, "text": "<точна підстрока з цього рядка>"}} або null}}}}

Поля: {fields}.
Правила:
- "text" копіюй дослівно з вказаного рядка, без перетворень.
- date — дата як надруковано; total/vat — рядок із сумою; tax_id — лише цифри;
  supplier — лише назва, без адреси, телефону, IBAN і податкового номера.
- Якщо поля немає — null.

Рядки:
{lines}
"""


def gaps(parsed: dict) -> list[str]:
    """The fields the rules could not vouch for, in the layer's own field order.

    The model exists for these and only these: a field with a value, no refused
    invariant and a weight above `GAP_CONFIDENCE` was answered by deterministic code,
    and asking again would spend the slowest part of the request on a value that is
    already recorded — with a chance of replacing a good value by a worse one.
    """
    fields = parsed.get('fields') or {}
    refused = {issue.split(':')[0] for issue in parsed.get('issues') or []}
    out: list[str] = []
    for name in FIELDS:
        field = fields.get(name) or {}
        value = field.get('value')
        if name in ALWAYS_ASK:
            out.append(name)
        elif value in (None, ''):
            out.append(name)
        elif name in refused:
            out.append(name)
        elif float(field.get('confidence') or 0.0) < GAP_CONFIDENCE:
            out.append(name)
    return out


@dataclass
class Resolved:
    """One field the model pointed at, after the code checked the pointer."""

    value: object
    line: int
    text: str


def _api_key() -> str:
    key = os.environ.get('DOCFLOW_MEANING_KEY', '').strip()
    if key:
        return key
    if KEY_FILE.exists():
        match = re.search(r'GROQ_API_KEY=(\S+)', KEY_FILE.read_text(encoding='utf-8'))
        if match:
            return match.group(1)
    return ''


def _escape_inner_quotes(raw: str) -> str:
    """Escape a quote that belongs to the text, not to the JSON around it.

    The model copies a name verbatim, so a supplier printed as
    `ТОВ "РЕГІОН ДСТ"` comes back inside a JSON string unescaped. A string ends
    only at a quote followed by `,`, `}`, `]` or `:`; every other quote inside a
    string is part of the value and must be escaped.
    """
    result = []
    index = 0
    in_string = False
    while index < len(raw):
        char = raw[index]
        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
            index += 1
            continue
        if char == '\\' and index + 1 < len(raw):
            result.append(raw[index:index + 2])
            index += 2
            continue
        if char == '"':
            ahead = index + 1
            while ahead < len(raw) and raw[ahead].isspace():
                ahead += 1
            following = raw[ahead] if ahead < len(raw) else ''
            if following and following not in ',}]:':
                result.append('\\"')
                index += 1
                continue
            in_string = False
        result.append(char)
        index += 1
    return ''.join(result)


def _loads(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _escape_inner_quotes(raw)
        if repaired == raw:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _schema(asked: list[str]) -> dict:
    """The exact answer shape, for endpoints that constrain decoding to it.

    Without the constraint a small model occasionally answers in prose instead of JSON,
    and on a CPU that costs the whole `max_tokens` budget before the answer is dropped:
    measured 145 and 178 seconds on documents that a constrained answer handles in ten.
    """
    pointer = {
        'type': 'object',
        'properties': {'line': {'type': 'integer'}, 'text': {'type': 'string'}},
        'required': ['line', 'text'],
        'additionalProperties': False,
    }
    return {
        'type': 'object',
        'properties': {
            'fields': {
                'type': 'object',
                'properties': {name: {'oneOf': [pointer, {'type': 'null'}]} for name in asked},
                'required': list(asked),
                'additionalProperties': False,
            },
        },
        'required': ['fields'],
        'additionalProperties': False,
    }


def _ask(text: str, fields: list[str] | None = None) -> dict:
    """One request to an OpenAI-compatible endpoint; empty dict on any failure."""
    asked = list(fields) if fields else list(FIELDS)
    url = os.environ.get('DOCFLOW_MEANING_URL', DEFAULT_URL)
    model = os.environ.get('DOCFLOW_MEANING_MODEL', 'qwen/qwen3.8-27b')
    key = _api_key()
    lines = text.splitlines()
    shown = lines[:MAX_LINES]
    numbered = '\n'.join(
        f'{index}\t{line}' for index, line in enumerate(shown, start=1)
    )
    if len(lines) > len(shown):
        numbered += f'\n…(показано {len(shown)} рядків із {len(lines)})'
    payload = {
        'model': model,
        'messages': [{'role': 'user',
                      'content': PROMPT.format(fields=', '.join(asked), lines=numbered)}],
        'temperature': 0,
        'max_tokens': 700,
    }
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    constrained = {
        **payload,
        'response_format': {
            'type': 'json_schema',
            'json_schema': {'name': 'pointers', 'strict': True, 'schema': _schema(asked)},
        },
    }
    try:
        response = httpx.post(url, headers=headers, json=constrained, timeout=TIMEOUT)
        if response.status_code >= 400:
            # Not every OpenAI-compatible endpoint accepts a schema; the plain request
            # still works, it is only less reliable. Losing the layer silently would be
            # worse than losing the constraint.
            logger.warning(
                'meaning layer: %s refused the answer schema (%s), asking without it',
                url, response.status_code)
            response = httpx.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
    except Exception as error:  # noqa: BLE001 - the model is an optional helper, never a gate
        # Falling back to the rules is the design, but it must not be invisible:
        # a throttled or unreachable model looks exactly like an answer with no
        # pointers unless the log says which one happened.
        logger.warning('meaning layer: no answer from %s (%s)', url, error)
        return {}
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        logger.warning('meaning layer: no JSON in the answer from %s', url)
        return {}
    try:
        answer = _loads(match.group(0))
    except Exception:  # noqa: BLE001 - repair is best-effort
        answer = None
    if answer is None:
        logger.warning('meaning layer: unparsable JSON from %s', url)
        return {}
    return answer


def parse_date(text: str) -> str | None:
    match = re.search(r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})', text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = '20' + year
        return f'{year}-{int(month):02d}-{int(day):02d}'
    match = re.search(r'(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})', text, re.I)
    if match:
        day, month_name, year = match.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            return f'{year}-{month:02d}-{int(day):02d}'
    return None


def parse_money(text: str) -> float | None:
    numbers = re.findall(r'\d[\d\s\u00a0]*[.,]\d{2}', text)
    if not numbers:
        return None
    raw = numbers[-1].replace('\u00a0', '').replace(' ', '').replace(',', '.')
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def parse_tax(text: str) -> str | None:
    # Longest first, with digit boundaries: an eight-digit pattern would match the
    # first digits of a ten-digit РНОКПП and produce a plausible wrong number.
    match = re.search(r'(?<!\d)(?:\d{12}|\d{10}|\d{8})(?!\d)', text)
    return match.group(0) if match else None


def _value_for(name: str, span: str):
    if name == 'date':
        return parse_date(span)
    if name in ('total', 'vat'):
        return parse_money(span)
    if name == 'tax_id':
        return parse_tax(span)
    if name == 'currency':
        return 'UAH' if re.search(r'грн|₴', span, re.I) else None
    if name == 'number':
        return re.sub(r'^.*?(?:№|Ne|Nº|М»|Ме|No)\s*', '', span).strip()
    return span


def resolve(plan: dict, text: str, fields: list[str] | None = None) -> dict[str, Resolved]:
    """Keep only pointers that point at text that is really there."""
    lines = text.splitlines()
    out: dict[str, Resolved] = {}
    for name in (fields if fields is not None else FIELDS):
        pointer = (plan.get('fields') or {}).get(name)
        if not isinstance(pointer, dict):
            continue
        try:
            number = int(pointer.get('line'))
        except (TypeError, ValueError):
            continue
        span = str(pointer.get('text') or '').strip()
        if not (1 <= number <= len(lines)) or not span:
            continue
        # A tax id must sit in the supplier block. In a buyer-first layout the
        # buyer's tax id is printed before the supplier's, and a model can point
        # at the first number it sees; the supplier anchor is already resolved,
        # so an out-of-block tax pointer is dropped rather than copied.
        if name == 'tax_id' and 'supplier' in out:
            anchor = out['supplier'].line
            if not (anchor <= number <= anchor + 2):
                continue
        if span.casefold() not in lines[number - 1].casefold():
            continue
        value = _value_for(name, span)
        if value is None or value == '':
            continue
        out[name] = Resolved(value=value, line=number, text=span)
    return out


def _agrees(name: str, left, right) -> bool:
    """Whether the parser and the pointer produced the same value."""
    if left is None or right is None:
        return False
    if name in ('total', 'vat'):
        try:
            return abs(float(left) - float(right)) <= 0.05
        except (TypeError, ValueError):
            return False
    if name == 'tax_id':
        return re.sub(r'\D', '', str(left)) == re.sub(r'\D', '', str(right))
    if name == 'date':
        return str(left).strip() == str(right).strip()
    return str(left).strip().casefold() == str(right).strip().casefold()


def apply(parsed: dict, text: str, shadow: bool = False,
          fields: list[str] | None = None) -> dict[str, Resolved]:
    """Merge resolved pointers into `parsed`, keeping the parser value as fallback.

    In shadow mode nothing is merged: the result is returned for the caller to
    record, which is how the layer can be tried on live documents without
    changing an answer. `fields` names what to ask about; by default it is the gap
    the rules left behind, and an empty gap means no request is made at all.
    """
    asked = list(fields) if fields is not None else gaps(parsed)
    if not asked:
        return {}
    resolved = resolve(_ask(text, asked), text, asked)
    if shadow:
        return resolved
    fields = parsed.setdefault('fields', {})
    for name, item in resolved.items():
        existing = fields.get(name) or {}
        provenance = {'line': item.line, 'text': item.text, 'by': 'model-pointer'}
        if _agrees(name, existing.get('value'), item.value):
            # Two independent readers agree: keep the parser's weight and only
            # record where the value can be found.
            existing['provenance'] = provenance
            continue
        # The model chose a different value. It is not a measured probability that
        # this one is right, so it gets the weight of the weakest anchor and is
        # marked as not verified by the rules; `revise` then runs the invariants
        # over it like over any other field.
        fields[name] = {
            'value': item.value,
            'source': f'model pointer line {item.line}',
            'confidence': 0.6,
            'base_confidence': 0.6,
            'verified': False,
            'provenance': provenance,
        }
    return resolved
