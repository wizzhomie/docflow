"""Seed a labelled synthetic dataset for demos.

Run: python3 tools/seed_demo.py [--reset]

Rows are appended to data/confirmed.jsonl with `"source": "synthetic"`, so the
impact report and the UI can say honestly how much of the picture is demo data.
The ids are stable, which makes the script idempotent: running it twice does not
duplicate the dataset. `--reset` removes previously seeded rows first.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CONFIRMED = BASE / 'data' / 'confirmed.jsonl'

SUPPLIERS = [
    ('ТОВ «Чистий дім»', '23456789'),
    ('ФОП Коваленко Олена Іванівна', '1234567890'),
    ('ТОВ «Затишок-Сервіс»', '34567890'),
    ('ПП «Альфа-Постач»', '45678901'),
]

ITEMS = [
    ('Прибирання офісних приміщень', 25000.0),
    ('Канцелярія та витратні матеріали', 12000.0),
    ('Оренда складського приміщення', 48000.0),
    ('Технічне обслуговування обладнання', 8500.0),
    ('Послуги бухгалтерського супроводу', 15000.0),
]

EDITS = [
    ('supplier', 'ТОВ «Чистий дім», ЄДРПОУ 23456789', 'ТОВ «Чистий дім»'),
    ('supplier', 'ПП «Альфа-Постач» ЄДРПОУ 45678901', 'ПП «Альфа-Постач»'),
    ('total', 148000.0, 150000.0),
    ('vat', 24666.67, 25000.0),
    ('tax_id', '2345678', '23456789'),
]

ISSUES = [
    'vat: invariant failed',
    'tax_id: invariant failed',
    'items_sum: invariant failed',
]


def money(value: float) -> float:
    return round(value, 2)


def make_row(index: int, rng: random.Random) -> dict:
    supplier, tax_id = SUPPLIERS[index % len(SUPPLIERS)]
    item, base_price = ITEMS[index % len(ITEMS)]
    quantity = rng.randint(1, 6)
    total = money(base_price * quantity)
    vat = 0.0 if index % 4 == 0 else money(total * 20 / 120)
    day = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=index % 13, hours=8 + index % 9)
    review = rng.randint(38, 231)

    number = f'2026-{441 + index}'
    fields = {
        'number': number,
        'date': f'2026-11-{1 + index % 13:02d}',
        'supplier': supplier,
        'tax_id': tax_id,
        'total': total,
        'vat': vat,
        'currency': 'UAH',
    }
    parsed = dict(fields)
    edited: list[str] = []
    issues: list[str] = []

    # Roughly a third of the documents needed a human correction.
    if index % 3 == 0:
        field, parsed_value, confirmed_value = EDITS[index % len(EDITS)]
        if field in fields:
            parsed[field] = parsed_value
            fields[field] = confirmed_value
            edited.append(field)

    if index % 5 == 0:
        issues.append(ISSUES[index % len(ISSUES)])
    if index in {9, 16}:
        issues.append('items_sum: invariant failed')

    file_names = ['invoice_2026-441.pdf', 'scan_invoice_nov.pdf', 'invoice_2026-455.pdf', 'receipt_photo.jpg']
    return {
        'id': f'demo-{index:02d}',
        'filename': file_names[index % len(file_names)],
        'uploaded_at': (day - timedelta(minutes=rng.randint(1, 9))).isoformat(timespec='seconds'),
        'confirmed_at': day.isoformat(timespec='seconds'),
        'review_seconds': review,
        'fields': fields,
        'parsed': parsed,
        'edited_fields': edited,
        'confidence': round(rng.uniform(0.58, 0.93), 2),
        'issues': issues,
        'crm_deal_id': f'DEMO-{index:02d}' if index % 7 else None,
        'source': 'synthetic',
        'note': f'Синтетичний демо-документ: {item}, {quantity} од.',
    }


def load_rows() -> list[dict]:
    if not CONFIRMED.exists():
        return []
    return [json.loads(line) for line in CONFIRMED.read_text(encoding='utf-8').splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true', help='remove previously seeded synthetic rows')
    parser.add_argument('--count', type=int, default=24, help='number of unique synthetic documents')
    args = parser.parse_args()

    rows = load_rows()
    if args.reset:
        rows = [row for row in rows if row.get('source') != 'synthetic']
        CONFIRMED.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        print(f'reset: {len(rows)} real rows kept')

    known = {row.get('id') for row in rows}
    rng = random.Random(20261114)
    fresh: list[dict] = []
    for index in range(args.count):
        row = make_row(index, rng)
        if row['id'] in known:
            continue
        fresh.append(row)
        # One duplicated confirmation, to show the duplicate metric honestly.
        if index == 7:
            clone = dict(row)
            clone['id'] = 'demo-07-duplicate'
            clone['confirmed_at'] = (datetime.fromisoformat(row['confirmed_at']) + timedelta(minutes=4)).isoformat(timespec='seconds')
            clone['review_seconds'] = row['review_seconds'] + 240
            fresh.append(clone)

    if not fresh:
        print('nothing to seed: dataset already present')
        return 0

    with CONFIRMED.open('a', encoding='utf-8') as out:
        for row in fresh:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')

    edited = sum(1 for row in fresh if row['edited_fields'])
    risky = sum(1 for row in fresh if row['issues'] and not row['edited_fields'])
    print(f'seeded {len(fresh)} synthetic rows: {edited} with corrections, {risky} confirmed despite issues')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
