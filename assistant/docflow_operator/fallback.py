"""Rules-only planner: the deterministic baseline for the same scenarios.

Two jobs:
1. It is the baseline the model is measured against in the evaluation.
2. It is the safe fallback when the model is unreachable, returns broken JSON or
   loops, so the demo never ends with a blank screen.
"""
from __future__ import annotations

import re

from .tools import Toolbox, ToolError

DOC_ID_RE = re.compile(r'\b[0-9a-f]{8,12}\b', re.IGNORECASE)
CYRILLIC_RE = re.compile(r'[а-яіїєґ]', re.IGNORECASE)

MONEY_WORDS = ('оплат', 'плат', 'pay', 'payment', 'invoice', 'рахун', 'счет', 'счёт')
DUPLICATE_WORDS = ('дубл', 'duplicate', 'повтор', 'already booked', 'уже был')
LETTER_WORDS = ('письм', 'letter', 'напомин', 'remind', 'reply', 'відповід', 'ответ')
APPROVAL_WORDS = ('соглас', 'approv', 'погодж', 'подтверд', 'confirm')
SUMMARY_WORDS = ('сводк', 'summar', 'скільки', 'сколько', 'report', 'итог', 'підсум', 'огляд')
DETAIL_WORDS = ('детал', 'detail', 'покажи', 'show', 'что с', 'що з')
NEWEST_WORDS = ('newest', 'latest', 'first document', 'перш', 'последн', 'свеж', 'остан')


def language_of(text: str) -> str:
    return 'ru' if len(CYRILLIC_RE.findall(text)) >= 4 else 'en'


def plan(task: str) -> list[tuple[str, dict]]:
    """A small, explicit plan for one user task. No model involved."""
    text = task.lower()
    ids = DOC_ID_RE.findall(text)
    steps: list[tuple[str, dict]] = []
    if not ids and any(word in text for word in NEWEST_WORDS):
        # "the newest document" without an id: the tools resolve "latest" themselves.
        ids = ['latest']
    if ids:
        doc_id = ids[0]
        if any(word in text for word in DUPLICATE_WORDS):
            steps.append(('check_duplicate', {'doc_id': doc_id}))
        if any(word in text for word in LETTER_WORDS):
            if any(word in text for word in MONEY_WORDS):
                kind = 'payment_request'
            elif any(word in text for word in APPROVAL_WORDS):
                kind = 'approval_request'
            else:
                kind = 'supplier_reply'
            steps.append(('draft_letter', {'doc_id': doc_id, 'kind': kind}))
        if any(word in text for word in APPROVAL_WORDS):
            steps.append(('propose_confirm', {'doc_id': doc_id}))
        if not steps:
            steps.append(('get_document', {'doc_id': doc_id}))
        return steps
    if any(word in text for word in SUMMARY_WORDS):
        period = 'today' if any(w in text for w in ('сегодня', 'today', 'за день')) else 'week'
        return [('summarise', {'period': period})]
    return [('list_queue', {'limit': 5})]


def answer_from(steps: list[dict], task: str) -> str:
    """Compose a short final answer from tool results, in the user's language."""
    lang = language_of(task)
    if not steps:
        return 'Не удалось разобрать задачу.' if lang == 'ru' else 'Could not parse the task.'
    lines: list[str] = []
    for step in steps:
        if not step.get('ok', True):
            continue  # failed or refused calls are shown in the trace, not in the answer
        result = step.get('result') or {}
        tool = step.get('tool')
        if tool == 'list_queue':
            documents = result.get('documents') or []
            if lang == 'ru':
                lines.append(f'В очереди {result.get("count", 0)} документ(ов).')
            else:
                lines.append(f'{result.get("count", 0)} document(s) waiting for review.')
            for doc in documents[:5]:
                total = doc.get('total')
                amount = f'{total:,.2f} {doc.get("currency") or ""}'.strip() if isinstance(total, (int, float)) else '—'
                lines.append(f'- {doc.get("type") or "document"} №{doc.get("number") or "?"} '
                             f'от {doc.get("supplier") or "?"}: {amount} → {doc.get("route") or "?"}')
            approval = result.get('needs_approval') or []
            payments = result.get('to_pay') or []
            booking = result.get('to_book') or []
            if lang == 'ru':
                if approval:
                    lines.append('На согласование: ' + ', '.join(approval) + '.')
                if payments:
                    lines.append('На оплату: ' + ', '.join(payments) + '.')
                if booking:
                    lines.append('В учёт: ' + ', '.join(booking) + '.')
            else:
                if approval:
                    lines.append('Approval: ' + ', '.join(approval) + '.')
                if payments:
                    lines.append('Payments: ' + ', '.join(payments) + '.')
                if booking:
                    lines.append('Accounting: ' + ', '.join(booking) + '.')
        elif tool == 'summarise':
            waiting = result.get('waiting') or {}
            if lang == 'ru':
                lines.append(f"Ждут проверки: {waiting.get('count', 0)} на сумму {waiting.get('amount', 0):,.2f}.")
                lines.append(f"Из них: оплата — {waiting.get('to_pay', 0)}, согласование — "
                             f"{waiting.get('needs_approval', 0)}, учёт — {waiting.get('to_book', 0)}.")
            else:
                lines.append(f"{waiting.get('count', 0)} waiting for review, total {waiting.get('amount', 0):,.2f}.")
                lines.append(f"To pay: {waiting.get('to_pay', 0)}, to approve: "
                             f"{waiting.get('needs_approval', 0)}, to book: {waiting.get('to_book', 0)}.")
        elif tool == 'get_document':
            doc = result.get('document') or {}
            total = doc.get('total')
            amount = f'{total:,.2f} {doc.get("currency") or ""}'.strip() if isinstance(total, (int, float)) else '—'
            if lang == 'ru':
                lines.append(f'Документ {doc.get("type") or ""} №{doc.get("number") or "?"} от '
                             f'{doc.get("supplier") or "?"}, сумма {amount}, маршрут {doc.get("route") or "?"}.')
            else:
                lines.append(f'{doc.get("type") or "Document"} #{doc.get("number") or "?"} from '
                             f'{doc.get("supplier") or "?"}, {amount}, route {doc.get("route") or "?"}.')
            issues = doc.get('issues') or []
            if issues:
                lines.append(('Замечания: ' if lang == 'ru' else 'Issues: ') + '; '.join(map(str, issues)))
        elif tool == 'check_duplicate':
            if result.get('duplicate'):
                matches = ', '.join(str(m.get('id')) for m in result.get('matches') or [])
                lines.append((f'Найден дубль: {matches}.' if lang == 'ru' else f'Duplicate found: {matches}.'))
            else:
                lines.append('Дублей не найдено.' if lang == 'ru' else 'No duplicate found.')
        elif tool == 'draft_letter':
            lines.append((f'Черновик письма сохранён: {result.get("path")}. Отправка — только человеком.'
                          if lang == 'ru' else
                          f'Draft letter saved: {result.get("path")}. Sending stays with a human.'))
        elif tool == 'propose_confirm':
            lines.append((result.get('proposal') or '') +
                         (' Подтвердить может только человек в DocFlow.' if lang == 'ru'
                          else ' Only a human can confirm it in DocFlow.'))
    return '\n'.join(lines)


def run(task: str, toolbox: Toolbox) -> tuple[list[dict], str]:
    """Execute the rule plan and return (steps, answer)."""
    planned = plan(task)
    steps: list[dict] = []
    for index, (tool, arguments) in enumerate(planned, start=1):
        try:
            result = toolbox.run(tool, arguments)
            steps.append({'n': index, 'tool': tool, 'arguments': arguments, 'result': result, 'ok': True})
        except (ToolError, Exception) as error:  # noqa: BLE001 - baseline must never crash the UI
            steps.append({'n': index, 'tool': tool, 'arguments': arguments,
                          'result': {'error': str(error)}, 'ok': False})
    return steps, answer_from(steps, task)
