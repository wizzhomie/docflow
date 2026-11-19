"""Can the model point at where a value is printed, and let the code copy it?

Run: python3 tools/meaning_plan_experiment.py [--provider groq|local] [--limit N]

The question behind this script is the FormaCV lesson: the code must not decide
what a line means, and the model must not produce the value. The model is given
numbered lines and answers with a pointer per field — line number and the exact
substring — and the code copies that substring, parses the format and writes the
value. A pointer that does not resolve is not a value: the field falls back to the
deterministic parser and is marked as such.

This measures the idea before it touches the pipeline: how often the pointer
resolves, how often the copied value matches the printed value, and whether the
model ever points at the wrong line. The report goes to `memory/`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import extractor  # noqa: E402

DEFAULT_CORPUS = BASE / 'data' / 'synthetic-corpus'
LOCAL_URL = 'http://127.0.0.1:8080/v1/chat/completions'
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
KEY_FILE = Path.home() / '.config' / 'docflow' / 'models.env'
FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')

PROMPT = """Тобі дано текст українського документа, рядки пронумеровані.
Твоє завдання — вказати, ДЕ надруковано кожне поле, а не вигадати значення.

Поверни ЛИШЕ JSON:
{{"fields": {{"поле": {{"line": <номер рядка>, "text": "<точна підстрока з цього рядка>"}} або null}}}}

Поля: number, date, supplier, tax_id, total, vat, currency.
Правила:
- "text" копіюй ДОСЛІВНО з вказаного рядка, без перетворень і без додавання слів.
- Для date достатньо дати як надруковано («17 березня 2026» або «17.03.2026»).
- Для total/vat — рядок, де надрукована сума (разом з підписом, якщо він у тому ж рядку).
- Для tax_id — лише цифри, як надруковано.
- Для supplier — лише назва, без адреси, телефону, IBAN і податкового номера.
- Якщо поля немає — null. Не вгадуй і не переноси значення з іншого поля.

Рядки:
{lines}
"""


def _api_key() -> str:
    text = KEY_FILE.read_text(encoding='utf-8')
    match = re.search(r'GROQ_API_KEY=(\S+)', text)
    if not match:
        raise SystemExit(f'no GROQ_API_KEY in {KEY_FILE}')
    return match.group(1)


def ask(client: httpx.Client, text: str, url: str, model: str, key: str = '') -> dict:
    numbered = '\n'.join(f'{index}\t{line}' for index, line in enumerate(
        text.splitlines()[:100], start=1))
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': PROMPT.format(lines=numbered)}],
        'temperature': 0,
        'max_tokens': 700,
    }
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    for attempt in range(6):
        response = client.post(url, headers=headers, json=payload, timeout=90)
        if response.status_code == 429 and attempt < 5:
            time.sleep(max(1.0, float(response.headers.get('retry-after', 2 ** attempt))))
            continue
        response.raise_for_status()
        break
    content = response.json()['choices'][0]['message']['content']
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


ISO_MONTHS = {
    'січня': 1, 'лютого': 2, 'березня': 3, 'квітня': 4, 'травня': 5, 'червня': 6,
    'липня': 7, 'серпня': 8, 'вересня': 9, 'жовтня': 10, 'листопада': 11, 'грудня': 12,
}


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
        month = ISO_MONTHS.get(month_name.lower())
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
    # Longest first: `\d{8}|\d{10}` matched the first eight digits of a ten-digit
    # РНОКПП and produced a plausible but wrong tax id — a confident error made by
    # the copier, not by the model.
    match = re.search(r'(?<!\d)(?:\d{12}|\d{10}|\d{8})(?!\d)', text)
    return match.group(0) if match else None


def resolve(plan: dict, text: str) -> dict:
    """Turn pointers into values; anything that does not resolve stays absent."""
    lines = text.splitlines()
    resolved: dict[str, dict] = {}
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
        line = lines[number - 1]
        if span.casefold() not in line.casefold():
            continue
        if name == 'date':
            value = parse_date(span)
        elif name in ('total', 'vat'):
            value = parse_money(span)
        elif name == 'tax_id':
            value = parse_tax(span)
        elif name == 'currency':
            value = 'UAH' if re.search(r'грн|₴', span, re.I) else None
        elif name == 'number':
            value = re.sub(r'^.*?(?:№|Ne|Nº|М»|Ме|No)\s*', '', span).strip()
        else:
            value = span
        if value is not None:
            resolved[name] = {'value': value, 'line': number, 'text': span}
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--provider', choices=('groq', 'local'), default='groq')
    parser.add_argument('--model', default='')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--delay', type=float, default=1.0)
    args = parser.parse_args()
    url = GROQ_URL if args.provider == 'groq' else LOCAL_URL
    model = args.model or ('qwen/qwen3.8-27b' if args.provider == 'groq' else
                           'qwen2.5-1.5b-instruct-q4_k_m')
    key = _api_key() if args.provider == 'groq' else ''

    manifest = json.loads((args.corpus / 'manifest.json').read_text(encoding='utf-8'))
    if args.limit:
        manifest = manifest[:args.limit]

    rows = []
    latencies = []
    with httpx.Client() as client:
        for entry in manifest:
            time.sleep(args.delay)
            path = args.corpus / entry['file']
            text = extractor.extract_text(path)
            started = time.monotonic()
            plan = ask(client, text, url, model, key)
            latencies.append(time.monotonic() - started)
            values = resolve(plan, text)
            expected = entry['expected']
            checks = {}
            for name in FIELDS:
                want = expected.get(name)
                got = values.get(name, {}).get('value')
                if name == 'date':
                    ok = got == want if got else None
                elif name in ('total', 'vat'):
                    ok = (abs(float(got) - float(want)) <= 0.05) if got is not None and want is not None else (
                        got is None and want is None)
                elif name == 'tax_id':
                    ok = re.sub(r'\D', '', str(got or '')) == re.sub(r'\D', '', str(want or '')) if got else None
                else:
                    ok = str(got).casefold() == str(want).casefold() if got else None
                checks[name] = ok
            rows.append({'file': entry['file'], 'checks': checks, 'values': values,
                         'unresolved': [n for n in FIELDS if n not in values]})

    summary = {}
    for name in FIELDS:
        resolved = sum(1 for row in rows if row['checks'][name] is not None)
        correct = sum(1 for row in rows if row['checks'][name] is True)
        wrong = sum(1 for row in rows if row['checks'][name] is False)
        summary[name] = {'resolved': resolved, 'correct': correct, 'wrong': wrong,
                         'unresolved': len(rows) - resolved}
    print(f'documents: {len(rows)}; median latency: {statistics.median(latencies):.1f}s')
    for name, bucket in summary.items():
        print(f"  {name:9} resolved {bucket['resolved']:2}  correct {bucket['correct']:2}  "
              f"wrong {bucket['wrong']:2}  unresolved {bucket['unresolved']:2}")

    report = BASE / 'memory' / f'meaning-plan-{args.provider}.md'
    lines = [
        '# Модель указывает, код переносит: прогон',
        '',
        f'Корпус: `{args.corpus.relative_to(BASE)}`, документов: {len(rows)}. Модель: '
        f'{model} ({url}), temperature 0. Медиана задержки: '
        f'{statistics.median(latencies):.1f} с.',
        '',
        'Модель не возвращает значение: она возвращает номер строки и точную подстроку, '
        'код копирует её и разбирает формат. «Не разрешилось» — указатель не нашёлся в '
        'строке, значение не подставлено.',
        '',
        '| поле | указателей разрешилось | верно | неверно | не разрешилось |',
        '| --- | --- | --- | --- | --- |',
    ]
    for name, bucket in summary.items():
        lines.append(f"| {name} | {bucket['resolved']} | {bucket['correct']} | "
                     f"{bucket['wrong']} | {bucket['unresolved']} |")
    lines += ['', '## Неверные и неразрешённые', '']
    for row in rows:
        for name in FIELDS:
            if row['checks'][name] is False:
                lines.append(f"- `{row['file']}` {name}: указатель {row['values'][name]} "
                             f"не совпал с напечатанным")
        if row['unresolved']:
            lines.append(f"- `{row['file']}`: не разрешилось {', '.join(row['unresolved'])}")
    report.write_text('\n'.join(lines), encoding='utf-8')
    print(f'report: {report}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
