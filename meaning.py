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

Поля: number, date, supplier, tax_id, total, vat, currency.
Правила:
- "text" копіюй дослівно з вказаного рядка, без перетворень.
- date — дата як надруковано; total/vat — рядок із сумою; tax_id — лише цифри;
  supplier — лише назва, без адреси, телефону, IBAN і податкового номера.
- Якщо поля немає — null.

Рядки:
{lines}
"""


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


def _ask(text: str) -> dict:
    """One request to an OpenAI-compatible endpoint; empty dict on any failure."""
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
        'messages': [{'role': 'user', 'content': PROMPT.format(lines=numbered)}],
        'temperature': 0,
        'max_tokens': 700,
    }
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=30)
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
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning('meaning layer: unparsable JSON from %s', url)
        return {}


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


def resolve(plan: dict, text: str) -> dict[str, Resolved]:
    """Keep only pointers that point at text that is really there."""
    lines = text.splitlines()
    out: dict[str, Resolved] = {}
    for name in FIELDS:
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


def apply(parsed: dict, text: str, shadow: bool = False) -> dict[str, Resolved]:
    """Merge resolved pointers into `parsed`, keeping the parser value as fallback.

    In shadow mode nothing is merged: the result is returned for the caller to
    record, which is how the layer can be tried on live documents without
    changing an answer.
    """
    resolved = resolve(_ask(text), text)
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
