"""Score the parser against the synthetic corpus.

Run: python3 tools/eval_synthetic_corpus.py [--corpus data/synthetic-corpus]

The corpus is built by `tools/make_synthetic_corpus.py`, which knows every value
it printed. That is the point: the expected number, date, supplier, tax id, total
and VAT come from the generator, not from a person reading the PDF afterwards, so
a score cannot be massaged by a tired reviewer.

What this measures and what it does not: it measures whether the parser survives
layout, wording, number-format and scan variation it was not written against. It
does not measure real-world accuracy — the documents come from one distribution
written by the same codebase. A report must say both.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import intake  # noqa: E402

DEFAULT_CORPUS = BASE / 'data' / 'synthetic-corpus'


def norm_text(value: str | None) -> str:
    if not value:
        return ''
    value = value.replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"')
    value = value.replace('’', "'").replace('`', "'")
    return re.sub(r'\s+', ' ', value).strip().casefold()


def norm_number(value: str | None) -> str:
    if not value:
        return ''
    return re.sub(r'\s+', '', str(value)).casefold()


def norm_date(value: str | None) -> str:
    """The parser returns ISO; accept `dd.mm.yyyy` too rather than fail on shape."""
    if not value:
        return ''
    value = str(value).strip()
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', value):
        day, month, year = value.split('.')
        return f'{year}-{month}-{day}'
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', value):
        day, month, year = value.split('.')
        return f'20{year}-{month}-{day}'
    return value


def supplier_match(expected: str, got: str | None) -> str:
    """`exact`, `partial` (same name, different legal form or quotes) or `miss`."""
    left, right = norm_text(expected), norm_text(got)
    if not right:
        return 'miss'
    if left == right:
        return 'exact'
    legal = ('тов', 'фоп', 'пп', 'дп', 'ат', 'ооо')
    def strip_legal(text: str) -> str:
        words = text.split()
        if words and words[0] in legal:
            words = words[1:]
        return ' '.join(words).strip(' "')
    if strip_legal(left) == strip_legal(right):
        return 'partial'
    if strip_legal(left) in strip_legal(right) or strip_legal(right) in strip_legal(left):
        return 'partial'
    return 'miss'


def close(left: float | None, right: float | None, tolerance: float = 0.05) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= tolerance


def score_one(entry: dict, parsed: dict) -> dict:
    expected = entry['expected']
    fields = parsed.get('fields', {})
    def value(name):
        field = fields.get(name) or {}
        return field.get('value')
    checks = {
        'number': norm_number(value('number')) == norm_number(expected['number']),
        'date': norm_date(value('date')) == expected['date'],
        'tax_id': norm_number(value('tax_id')) == expected['tax_id'],
        'total': close(value('total'), expected['total']),
        'vat': close(value('vat'), expected['vat']) or (
            expected['vat'] is None and value('vat') == 0
        ),
        'currency': (value('currency') or 'UAH') == expected['currency'],
    }
    supplier = supplier_match(expected['supplier'], value('supplier'))
    checks['supplier'] = supplier == 'exact'
    return {
        'checks': checks,
        'supplier': supplier,
        'got': {name: value(name) for name in ('number', 'date', 'supplier', 'tax_id',
                                               'total', 'vat', 'currency')},
        'expected': expected,
        'kind': entry.get('expected', {}).get('kind'),
        'scan': entry.get('scan'),
        'template': entry.get('template'),
    }


def run(corpus: Path) -> dict:
    manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
    rows = []
    for entry in manifest:
        path = corpus / entry['file']
        meta = intake._process(entry['file'], entry['file'], path, persist=False)
        rows.append({'file': entry['file'], **score_one(entry, meta.get('parsed') or {})})
    return {'rows': rows, 'manifest': manifest}


def summarize(rows: list[dict]) -> dict:
    field_names = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')
    total = len(rows)
    summary = {
        'documents': total,
        'fields': {},
        'full_document': sum(1 for row in rows if all(row['checks'][name] for name in field_names)),
        'supplier_partial': sum(1 for row in rows if row['supplier'] == 'partial'),
        'by_kind': {},
        'by_scan': {},
        'by_template': {},
        'errors': defaultdict(list),
    }
    for name in field_names:
        summary['fields'][name] = sum(1 for row in rows if row['checks'][name])
    for key in ('kind', 'scan', 'template'):
        buckets = defaultdict(list)
        for row in rows:
            buckets[str(row[key])].append(row)
        summary['by_' + key] = {
            name: {
                'documents': len(items),
                'full_document': sum(
                    1 for item in items if all(item['checks'][f] for f in field_names)
                ),
            }
            for name, items in sorted(buckets.items())
        }
    for row in rows:
        for name in field_names:
            if not row['checks'][name]:
                summary['errors'][name].append(row)
    return summary


def markdown(report: dict, corpus: Path) -> str:
    summary = report['summary']
    total = summary['documents']
    lines = [
        '# Synthetic corpus: parser evaluation',
        '',
        f'Corpus: `{corpus.relative_to(BASE)}`, {total} documents generated by '
        '`tools/make_synthetic_corpus.py` (seed fixed, so this is reproducible).',
        '',
        '**Caveat:** these are synthetic documents from one generated distribution, '
        'not real scans of real invoices. They check survival of layout, wording, '
        'number formats and scan noise; they are not a claim of real-world accuracy.',
        '',
        '## Field accuracy',
        '',
        '| field | correct | share |',
        '| --- | --- | --- |',
    ]
    for name, correct in summary['fields'].items():
        lines.append(f'| {name} | {correct}/{total} | {correct / total:.0%} |')
    lines += [
        '',
        f"Documents with every checked field correct: "
        f"**{summary['full_document']}/{total}** "
        f"({summary['full_document'] / total:.0%}); supplier matches that differ only "
        f"in legal form or quotes: {summary['supplier_partial']}.",
        '',
        '## By document type',
        '',
        '| kind | documents | fully correct |',
        '| --- | --- | --- |',
    ]
    for name, bucket in summary['by_kind'].items():
        lines.append(f"| {name} | {bucket['documents']} | {bucket['full_document']} |")
    lines += ['', '## By scan / text', '', '| input | documents | fully correct |', '| --- | --- | --- |']
    for name, bucket in summary['by_scan'].items():
        label = 'scan (OCR)' if name == 'True' else 'text layer'
        lines.append(f"| {label} | {bucket['documents']} | {bucket['full_document']} |")
    lines += ['', '## By template', '', '| template | documents | fully correct |', '| --- | --- | --- |']
    for name, bucket in summary['by_template'].items():
        lines.append(f"| {name} | {bucket['documents']} | {bucket['full_document']} |")
    lines += ['', '## Misses', '']
    for name, rows in summary['errors'].items():
        lines.append(f'### {name} ({len(rows)})')
        lines.append('')
        for row in rows[:6]:
            expected = row['expected']
            got = row['got']
            lines.append(
                f"- `{row['file']}`: expected `{expected.get(name)}`, "
                f"got `{got.get(name)}`"
            )
        if len(rows) > 6:
            lines.append(f'- …and {len(rows) - 6} more')
        lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--report', type=Path, default=BASE / 'memory' / 'synthetic-eval.md')
    args = parser.parse_args()
    if not (args.corpus / 'manifest.json').exists():
        print(f'no corpus at {args.corpus}; run tools/make_synthetic_corpus.py first')
        return 1
    result = run(args.corpus)
    summary = summarize(result['rows'])
    report = {'summary': summary, 'rows': result['rows']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown(report, args.corpus), encoding='utf-8')
    print(f"{summary['documents']} documents, "
          f"{summary['full_document']} fully correct, "
          f"per-field: {summary['fields']}")
    print(f'report: {args.report}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
