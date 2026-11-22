"""Compare the real intake path with DOCFLOW_MEANING off vs on.

This is the product-level A/B: `intake._process` on the 31 held-out documents,
once with the rules only and once with the local meaning layer applying a
resolved pointer (`revise` and invariants included). It answers whether the local
model improves the pipeline, not just whether its pointers look good in isolation.

The same script measures the held-out set as text and as scanned pages: pass
`--corpus` with the directory and `--tolerant` when the text came out of OCR, since
a scan prints a quote, a space or a digit differently and an exact match would
report a model failure where the page is at fault.

Run with the training venv and the llama.cpp LoRA server on 127.0.0.1:8080.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

FT = Path(__file__).resolve().parent
REPO = FT.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault('DOCFLOW_MEANING_URL', 'http://127.0.0.1:8080/v1/chat/completions')
os.environ.setdefault('DOCFLOW_MEANING_MODEL', 'local')
os.environ['DOCFLOW_MEANING_KEY'] = ''

import settings  # noqa: E402
import intake  # noqa: E402

FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')


def _words(value: str) -> set[str]:
    text = str(value).casefold().replace('«', '"').replace('»', '"')
    return {word for word in re.findall(r'[0-9a-zа-яіїєґ\'’]+', text) if word}


def same_supplier(got, want) -> bool:
    if not got or not want:
        return False
    left, right = _words(got), _words(want)
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return len(left & right) / len(left | right) >= 0.6


def check(name: str, got, want, tolerant: bool = False) -> bool:
    if got is None or got == '':
        return False
    if name in ('total', 'vat'):
        try:
            return abs(float(got) - float(want)) <= 0.05
        except (TypeError, ValueError):
            return False
    if name == 'tax_id':
        return re.sub(r'\D', '', str(got)) == re.sub(r'\D', '', str(want))
    if name == 'date':
        return str(got).strip() == str(want).strip()
    if tolerant and name == 'supplier':
        return same_supplier(got, want)
    if tolerant and name == 'currency':
        return str(got).strip().casefold() in {'uah', 'грн', '₴'}
    return str(got).strip().casefold() == str(want).strip().casefold()


def run(mode: str, corpus: Path, manifest: list[dict], tolerant: bool = False) -> dict:
    settings.MEANING = mode
    summary = {name: {'correct': 0, 'wrong': 0, 'missing': 0, 'confident_wrong': 0}
               for name in FIELDS}
    for entry in manifest:
        meta = intake._process('eval-' + entry['file'], entry['file'],
                               corpus / entry['file'], persist=False)
        fields = meta['parsed']['fields']
        for name in FIELDS:
            field = fields.get(name) or {}
            value = field.get('value')
            confidence = field.get('confidence') or 0
            ok = check(name, value, entry['expected'].get(name), tolerant)
            if value is None or value == '':
                summary[name]['missing'] += 1
            elif ok:
                summary[name]['correct'] += 1
            else:
                summary[name]['wrong'] += 1
                if confidence >= 0.5:
                    summary[name]['confident_wrong'] += 1
    return summary


def report(off: dict, on: dict, documents: int) -> str:
    lines = ['field       off correct  on correct  off wrong  on wrong  on confident wrong']
    for name in FIELDS:
        lines.append(f"{name:10} {off[name]['correct']:>10} {on[name]['correct']:>11} "
                     f"{off[name]['wrong']:>10} {on[name]['wrong']:>9} "
                     f"{on[name]['confident_wrong']:>19}")
    total_off = sum(b['correct'] for b in off.values())
    total_on = sum(b['correct'] for b in on.values())
    lines.append(f"total correct: off {total_off}/{documents * len(FIELDS)}, "
                 f"on {total_on}/{documents * len(FIELDS)}")
    return '\n'.join(lines)


def _shown(path: Path) -> str:
    """Report a corpus path relative to the repository, not the machine it ran on."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=REPO / 'data' / 'synthetic-corpus-test-v2',
                        help='held-out corpus directory: texts plus its manifest.json')
    parser.add_argument('--manifest', default='manifest.json')
    parser.add_argument('--report', type=Path,
                        default=FT / 'data' / 'pipeline-eval.json')
    parser.add_argument('--tolerant', action='store_true',
                        help='OCR text: compare names and currency loosely')
    args = parser.parse_args()
    manifest = json.loads((args.corpus / args.manifest).read_text(encoding='utf-8'))
    off = run('off', args.corpus, manifest, args.tolerant)
    on = run('on', args.corpus, manifest, args.tolerant)
    print(report(off, on, len(manifest)))
    args.report.write_text(json.dumps({'off': off, 'on': on, 'documents': len(manifest),
                                       'corpus': _shown(args.corpus),
                                       'tolerant': args.tolerant},
                                      ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('report:', args.report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
