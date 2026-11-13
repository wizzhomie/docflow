"""Deterministic checks for duplicate suppression.

Run: python3 tests/test_dedupe.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def fields(number='55', party='23456789', date='2026-01-28', use_tax_id=True):
    data = {'number': number, 'date': date, 'supplier': 'ТОВ «Чистий дім»'}
    if use_tax_id:
        data['tax_id'] = party
    else:
        data['supplier'] = party
    return data


def history_row(**kwargs):
    return {'id': 'doc-1', 'filename': 'invoice_55.pdf', 'fields': fields(**kwargs)}


def test_key_requires_all_parts():
    assert app._dedupe_key(fields()) == ('55', '23456789', '2026-01-28')
    assert app._dedupe_key({'number': '55', 'date': '2026-01-28'}) is None
    assert app._dedupe_key({'number': '55', 'tax_id': '1'}) is None


def test_same_invoice_is_a_duplicate():
    assert app._local_duplicate(fields(), [history_row()]) is not None


def test_different_period_is_not_a_duplicate():
    # Monthly invoices legitimately reuse the number.
    assert app._local_duplicate(fields(date='2026-02-28'), [history_row()]) is None


def test_different_counterparty_is_not_a_duplicate():
    assert app._local_duplicate(fields(party='99999999'), [history_row()]) is None


def test_fallback_to_supplier_when_tax_id_missing():
    row = history_row(use_tax_id=False)
    assert app._local_duplicate(fields(use_tax_id=False), [row]) is not None


def test_number_matching_is_case_and_space_insensitive():
    assert app._local_duplicate(fields(number=' 55 '), [history_row()]) is not None


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('dedupe checks passed')


if __name__ == '__main__':
    main()
