"""Evaluation runner with real/synthetic split and confident error tracking."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor import parse_invoice, parse_items  # noqa: E402

REAL_FIXTURES = Path(__file__).parent / 'fixtures' / 'real' / 'invoice_ua_real.json'
SYNTHETIC_FIXTURES = Path(__file__).parent / 'fixtures' / 'synthetic' / 'invoice_ua_synthetic.json'


def matches(got, expected) -> bool:
    if isinstance(expected, float) and isinstance(got, (int, float)):
        return abs(float(got) - expected) <= 0.01
    return got == expected


def run_evaluation(fixture_path: Path, label: str) -> dict:
    cases = json.loads(fixture_path.read_text(encoding='utf-8'))
    per_field: dict[str, list[int]] = {}
    confident_errors = 0
    failures = 0
    total_cases = len(cases)

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
                    f'FAIL [{label}:{case["name"]}] {key}: got={values.get(key)!r} expected={expected!r} conf={confidence}'
                )

    total_ok = sum(ok for ok, _ in per_field.values())
    total = sum(n for _, n in per_field.values())

    return {
        'label': label,
        'total_cases': total_cases,
        'per_field': {k: f'{ok}/{n}' for k, (ok, n) in per_field.items()},
        'overall': f'{total_ok}/{total} ({round(100 * total_ok / total, 1)}%)',
        'confident_errors': confident_errors,
        'failures': failures,
    }


def main() -> int:
    print('=' * 60)
    print('REAL FIXTURES')
    print('=' * 60)
    real_results = run_evaluation(REAL_FIXTURES, 'real')
    print(f'\nOverall: {real_results["overall"]}')
    print(f'Confident errors: {real_results["confident_errors"]}')

    print('\n' + '=' * 60)
    print('SYNTHETIC FIXTURES')
    print('=' * 60)
    synthetic_results = run_evaluation(SYNTHETIC_FIXTURES, 'synthetic')
    print(f'\nOverall: {synthetic_results["overall"]}')
    print(f'Confident errors: {synthetic_results["confident_errors"]}')

    print('\n' + '=' * 60)
    print('SUMMARY')
    print('=' * 60)
    print(
        f'Real:     {real_results["overall"]}  | confident errors: {real_results["confident_errors"]}'
    )
    print(
        f'Synthetic: {synthetic_results["overall"]}  | confident errors: {synthetic_results["confident_errors"]}'
    )

    return 1 if (real_results['failures'] + synthetic_results['failures']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
