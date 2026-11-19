"""Compare code and the local model on the fields code is worst at.

Run: python3 tools/model_fields_experiment.py [--corpus data/synthetic-corpus] [--limit N]

The question is narrow on purpose: on the synthetic corpus the deterministic
pipeline gets the number, the total and the VAT right, and misses word dates, some
tax-id labels and a few supplier names. Those three fields are what a model could
reason about; everything else stays code. This script sends the extracted text to
the local llama.cpp model (`http://127.0.0.1:8080`, qwen2.5-1.5b) and compares
model-only and hybrid (model value, code fallback) with the code baseline.

It writes nothing into the product: no endpoint, no pipeline change. The output is
a report in `memory/`, because a decision to put a model in the path needs numbers,
not an opinion.
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
import intake  # noqa: E402

DEFAULT_CORPUS = BASE / 'data' / 'synthetic-corpus'
LOCAL_URL = 'http://127.0.0.1:8080/v1/chat/completions'
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
KEY_FILE = Path.home() / '.config' / 'docflow' / 'models.env'
FIELDS = ('date', 'tax_id', 'supplier')

PROMPT = """Ти витягуєш три поля з українського документа (рахунок, акт, накладна).
Поверни ЛИШЕ JSON-об'єкт з ключами:
- "date": дата документа у форматі YYYY-MM-DD (розумій дати словами на кшталт
  «17 березня 2026»);
- "tax_id": податковий номер (ЄДРПОУ, ІПН або РНОКПП), лише цифри;
- "supplier": назва постачальника точно як надруковано, без адреси, телефону,
  IBAN та без податкового номера.
Якщо значення немає або ти не впевнений — постав порожній рядок. Не вгадуй.
Відповідь — тільки JSON, без пояснень.

Текст документа:
{text}
"""


def _api_key() -> str:
    text = KEY_FILE.read_text(encoding='utf-8')
    match = re.search(r'GROQ_API_KEY=(\S+)', text)
    if not match:
        raise SystemExit(f'no GROQ_API_KEY in {KEY_FILE}')
    return match.group(1)


def ask_model(client: httpx.Client, text: str, url: str, model: str, key: str = '') -> dict:
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': PROMPT.format(text=text[:4000])}],
        'temperature': 0,
        'max_tokens': 300,
    }
    # The free tier answers 429 with a Retry-After when the minute is spent; a
    # batch of 45 documents hits that every time without a wait between calls.
    for attempt in range(6):
        response = client.post(url, headers=headers, json=payload, timeout=90)
        if response.status_code == 429 and attempt < 5:
            wait = float(response.headers.get('retry-after', 2 ** attempt))
            time.sleep(max(1.0, wait))
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


def norm_date(value: str) -> str:
    text = (value or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return text
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', text):
        day, month, year = text.split('.')
        return f'{year}-{month}-{day}'
    return text


def norm_text(value: str) -> str:
    text = (value or '').replace('«', '"').replace('»', '"').replace('“', '"')
    return re.sub(r'\s+', ' ', text).strip().casefold()


def digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def score(expected: dict, got: dict) -> dict:
    """Per field: `match`, `wrong` (a confident error) or `abstain` (empty)."""
    result = {}
    for name in FIELDS:
        want = expected.get(name)
        raw = got.get(name) or ''
        if name == 'date':
            value = norm_date(raw)
            ok = bool(want) and value == want
        elif name == 'tax_id':
            value = digits(raw)
            ok = bool(want) and value == digits(want)
        else:
            value = norm_text(raw)
            ok = bool(want) and value == norm_text(want)
        if not raw or not value:
            result[name] = 'abstain'
        else:
            result[name] = 'match' if ok else 'wrong'
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--provider', choices=('local', 'groq'), default='local')
    parser.add_argument('--model', default='')
    parser.add_argument('--delay', type=float, default=1.0)
    args = parser.parse_args()
    if args.provider == 'groq':
        url = GROQ_URL
        model = args.model or 'qwen/qwen3.8-27b'
        key = _api_key()
    else:
        url = LOCAL_URL
        model = args.model or 'qwen2.5-1.5b-instruct-q4_k_m'
        key = ""
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
            code = intake._process(entry['file'], entry['file'], path, persist=False)
            started = time.monotonic()
            parsed_model = ask_model(client, text, url, model, key)
            latencies.append(time.monotonic() - started)
            expected = entry['expected']
            code_values = {
                name: (code.get('parsed') or {}).get('fields', {}).get(name, {}).get('value')
                for name in FIELDS
            }
            hybrid = {
                name: (parsed_model.get(name) or '').strip() or code_values[name]
                for name in FIELDS
            }
            rows.append({
                'file': entry['file'],
                'scan': entry.get('scan'),
                'expected': {name: expected.get(name) for name in FIELDS},
                'code': code_values,
                'model': parsed_model,
                'hybrid': hybrid,
                'code_score': score(expected, code_values),
                'model_score': score(expected, parsed_model),
                'hybrid_score': score(expected, hybrid),
            })

    summary = {}
    for mode in ('code', 'model', 'hybrid'):
        summary[mode] = {}
        for name in FIELDS:
            counter = {'match': 0, 'wrong': 0, 'abstain': 0}
            for row in rows:
                counter[row[f'{mode}_score'][name]] += 1
            summary[mode][name] = counter

    print(f'documents: {len(rows)}; median latency: {statistics.median(latencies):.1f}s')
    for mode in ('code', 'model', 'hybrid'):
        print(mode)
        for name in FIELDS:
            counter = summary[mode][name]
            print(f'  {name:9} match {counter["match"]:2}  wrong {counter["wrong"]:2}  '
                  f'abstain {counter["abstain"]:2}')

    report = BASE / 'memory' / f'model-fields-experiment-{args.provider}.md'
    lines = [
        '# Модель на трёх полях: сравнение с кодом',
        '',
        f'Корпус: `{args.corpus.relative_to(BASE)}`, документов: {len(rows)}. Модель: '
        f'{model} ({url}), temperature 0, тот же '
        'извлечённый текст, что видит код. Медиана задержки: '
        f'{statistics.median(latencies):.1f} с/документ.',
        '',
        'Поле считается `match`, если значение совпало с напечатанным; `wrong` — если '
        'модель/код вернули неверное значение (уверенная ошибка); `abstain` — если '
        'значение пустое.',
        '',
        '| режим | поле | совпало | неверно | пусто |',
        '| --- | --- | --- | --- | --- |',
    ]
    for mode in ('code', 'model', 'hybrid'):
        for name in FIELDS:
            counter = summary[mode][name]
            lines.append(f'| {mode} | {name} | {counter["match"]} | {counter["wrong"]} | '
                         f'{counter["abstain"]} |')
    lines += ['', '## Где модель ошиблась и где спасла', '']
    for row in rows:
        for name in FIELDS:
            if row['model_score'][name] == 'wrong':
                lines.append(
                    f"- `{row['file']}` {name}: напечатано `{row['expected'][name]}`, "
                    f"модель `{row['model'].get(name)}`, код `{row['code'][name]}`"
                )
            elif row['model_score'][name] == 'match' and row['code_score'][name] != 'match':
                lines.append(
                    f"- `{row['file']}` {name}: модель спасла — "
                    f"`{row['expected'][name]}` (код: `{row['code'][name]}`)"
                )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines), encoding='utf-8')
    print(f'report: {report}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
