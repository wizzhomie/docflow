"""Checks for the demo-history rebuild: the database learns what the file knows.

The rebuild is destructive, so the mapping is pinned before it touches a
database: a record becomes one confirmed document with both snapshots, the
review start is derived from the measured seconds, and the duplicated invoice
keeps its place without breaking the unique rule.

Run: python3 tests/test_rebuild_demo_history.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import rebuild_demo_history as rebuild  # noqa: E402

RECORD = {
    'id': 'demo-00',
    'filename': 'invoice_2026-441.pdf',
    'uploaded_at': '2026-11-01T07:51:00+00:00',
    'confirmed_at': '2026-11-01T08:00:00+00:00',
    'review_seconds': 99,
    'fields': {
        'number': '2026-441',
        'date': '2026-11-01',
        'supplier': 'ТОВ «Чистий дім»',
        'tax_id': '23456789',
        'total': 25000.0,
        'vat': 0.0,
        'currency': 'UAH',
    },
    'parsed': {
        'number': '2026-441',
        'date': '2026-11-01',
        'supplier': 'ТОВ «Чистий дім», ЄДРПОУ 23456789',
        'tax_id': '23456789',
        'total': 25000.0,
        'vat': 0.0,
        'currency': 'UAH',
    },
    'edited_fields': ['supplier'],
    'confidence': 0.8,
    'issues': [],
    'crm_deal_id': 'DEMO-00',
    'source': 'synthetic',
}


def test_a_record_becomes_one_confirmed_document_with_both_snapshots():
    row = rebuild.document_row(RECORD, 8)
    assert row['status'] == 'confirmed'
    assert row['supplier_id'] == 8
    assert row['confirmed_fields'].adapted == RECORD['fields']
    assert row['parsed'].adapted['fields'] == RECORD['parsed']
    assert row['edited_fields'] == ['supplier']
    assert row['source'] == 'synthetic'
    assert row['file_path'] == 'data/history/invoice_2026-441.pdf'


def test_the_review_start_comes_from_the_measured_seconds():
    assert rebuild.review_start(RECORD) == '2026-11-01T07:58:21+00:00'
    assert rebuild.review_start({**RECORD, 'review_seconds': None}) is None


def test_the_document_type_comes_from_the_file_name():
    assert rebuild.document_type('invoice_2026-455.pdf') == 'invoice_ua'
    assert rebuild.document_type('act_01.pdf') == 'act_ua'
    assert rebuild.document_type('delivery_note.pdf') == 'delivery_ua'
    assert rebuild.document_type('receipt_photo.jpg') == 'unknown'


def test_a_duplicated_invoice_keeps_its_row_without_a_supplier_link():
    twin = {**RECORD, 'id': 'demo-00-duplicate'}
    result = rebuild.plan([RECORD, twin], {'ТОВ «Чистий дім»': 8})
    rows = result['rows']
    assert len(rows) == 2
    assert rows[0]['row']['supplier_id'] == 8
    assert rows[1]['row']['supplier_id'] is None, 'the second copy must not hit the unique rule'


def test_a_new_supplier_is_reported_not_invented():
    result = rebuild.plan([{**RECORD, 'fields': {**RECORD['fields'], 'supplier': 'ТОВ «Новий»'}}], {})
    assert result['missing_suppliers'] == ['ТОВ «Новий»']
    assert result['rows'][0]['row']['supplier_id'] is None


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('rebuild checks passed')


if __name__ == '__main__':
    main()
