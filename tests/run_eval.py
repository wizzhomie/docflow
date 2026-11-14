"""Fast local regression check for the Ukrainian invoice parser.

No model, no network, no heavy runs. Compares extracted fields against a small
set of hand-labelled cases that encode the invariant tree (synonyms, block
order, OCR noise, currency, Russian fallback).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor import parse_invoice, parse_items  # noqa: E402

CASES = Path(__file__).parent / 'document_cases.json'


def matches(got, expected) -> bool:
    if isinstance(expected, float) and isinstance(got, (int, float)):
        return abs(float(got) - expected) <= 0.01
    return got == expected


def main() -> int:
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    per_field: dict[str, list[int]] = {}
    confident_errors = 0
    failures = 0

    for case in cases:
        text = case['text']
        parsed = parse_invoice(text, items=parse_items(text))
        values = {key: field['value'] for key, field in parsed['fields'].items()}
        for key, expected in case['expected'].items():
            per_field.setdefault(key, [0, 0])
            per_field[key][1] += 1
            if matches(values.get(key), expected):
                per_field[key][0] += 1
            else:
                failures += 1
                confidence = parsed['fields'][key]['confidence']
                if values.get(key) is not None and confidence >= 0.5:
                    confident_errors += 1
                print(
                    f'FAIL [{case["name"]}] {key}: got={values.get(key)!r} expected={expected!r} conf={confidence}'
                )

    total_ok = sum(ok for ok, _ in per_field.values())
    total = sum(n for _, n in per_field.values())
    print('\nper-field accuracy:')
    for key in sorted(per_field):
        ok, n = per_field[key]
        print(f'  {key:10s} {ok}/{n}')
    print(f'\noverall: {total_ok}/{total} ({round(100 * total_ok / total, 1)}%)')
    print(f'confident errors: {confident_errors}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
