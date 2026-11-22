"""Build chat-JSONL training data from the synthetic corpus manifest.

The model is trained on exactly the message `meaning.py` sends at inference: one
user message with the Ukrainian prompt and numbered lines. The assistant target is
the pointer JSON — line number plus the exact printed substring.

Split is deterministic and stratified by layout: 80% train, 10% validation,
10% test. The test manifest keeps the expected values and pointers for scoring.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

import meaning  # noqa: E402

FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')
# A scan whose OCR kept fewer fields than this teaches mostly "null": the page is
# damaged, not noisy, and it belongs in the loss, not in the training set.
MIN_SCAN_FIELDS = 4


def numbered(text: str) -> str:
    return '\n'.join(f'{index}\t{line}' for index, line in enumerate(text.splitlines(), 1))


def plan(pointers: dict) -> dict:
    fields = {}
    for name in FIELDS:
        pointer = pointers.get(name)
        if isinstance(pointer, dict) and pointer.get('line') and pointer.get('text'):
            fields[name] = {'line': int(pointer['line']), 'text': str(pointer['text'])}
        else:
            fields[name] = None
    return {'fields': fields}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path,
                        default=BASE / 'data' / 'synthetic-corpus-300')
    parser.add_argument('--out', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data' / 'training')
    parser.add_argument('--test-out', type=Path, default=None,
                        help='materialise the held-out split as a corpus directory')
    parser.add_argument('--scans', type=Path, default=None,
                        help='manifest of OCR text and pointers per document, to add one '
                             'scanned example next to each text one')
    parser.add_argument('--seed', type=int, default=20261121)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.corpus / 'manifest.json').read_text(encoding='utf-8'))
    rng = random.Random(args.seed)

    by_layout: dict[int, list[dict]] = defaultdict(list)
    for entry in manifest:
        by_layout[entry['meta']['layout']].append(entry)

    train, val, test = [], [], []
    for _layout, entries in sorted(by_layout.items()):
        rng.shuffle(entries)
        n = len(entries)
        n_test = max(1, round(n * 0.1))
        n_val = max(1, round(n * 0.1))
        test.extend(entries[:n_test])
        val.extend(entries[n_test:n_test + n_val])
        train.extend(entries[n_test + n_val:])

    scans: dict = {}
    if args.scans is not None:
        scans = json.loads(args.scans.read_text(encoding='utf-8'))
        scans = {name: scan for name, scan in scans.items()
                 if scan.get('fields', 0) >= MIN_SCAN_FIELDS}

    def record(text: str, pointers: dict) -> dict:
        # Exactly the prompt the layer builds at inference, `Поля:` line included:
        # a marker that drifts from the runtime is worse than none.
        prompt = meaning.PROMPT.format(fields=', '.join(FIELDS), lines=numbered(text))
        return {'messages': [
            {'role': 'user', 'content': prompt},
            {'role': 'assistant', 'content': json.dumps(plan(pointers), ensure_ascii=False,
                                                       separators=(',', ':'))},
        ]}

    def write(name: str, entries: list[dict]) -> None:
        path = args.out / name
        written = scanned = 0
        with path.open('w', encoding='utf-8') as handle:
            for entry in entries:
                text = (args.corpus / entry['file']).read_text(encoding='utf-8')
                handle.write(json.dumps(record(text, entry['pointers']),
                                        ensure_ascii=False) + '\n')
                written += 1
                scan = scans.get(entry['file'])
                if scan:
                    handle.write(json.dumps(record(scan['text'], scan['pointers']),
                                            ensure_ascii=False) + '\n')
                    written += 1
                    scanned += 1
        suffix = f' ({scanned} of them scans)' if scans else ''
        print(f'{name}: {written} examples{suffix}')

    write('train.jsonl', train)
    write('val.jsonl', val)
    write('test.jsonl', test)
    (args.out / 'test_manifest.json').write_text(
        json.dumps(test, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (args.out / 'split.json').write_text(json.dumps({
        'train': [entry['file'] for entry in train],
        'val': [entry['file'] for entry in val],
        'test': [entry['file'] for entry in test],
    }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    if args.test_out is not None:
        args.test_out.mkdir(parents=True, exist_ok=True)
        for entry in test:
            (args.test_out / entry['file']).write_bytes(
                (args.corpus / entry['file']).read_bytes())
        (args.test_out / 'manifest.json').write_text(
            json.dumps(test, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(f'held-out corpus: {args.test_out} ({len(test)} documents)')
    (args.out / 'manifest.json').write_text(json.dumps({
        'corpus': str(args.corpus),
        'train': len(train), 'val': len(val), 'test': len(test),
        'seed': args.seed,
        'scans': str(args.scans) if args.scans else None,
    }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'train={len(train)} val={len(val)} test={len(test)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
