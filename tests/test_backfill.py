"""Checks for the history backfill: two snapshots, never a guess.

The tool exists because the imported demo documents kept the approved values in
`parsed` and left `confirmed_fields` empty. These cases pin the three ways a row
can be rebuilt (from the history file, from itself when there were no edits, and
not at all when the approved values are lost) without a database.

Run: python3 tests/test_backfill.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import backfill_confirmed_fields as backfill  # noqa: E402

FIELDS = {
    'number': '2026-450',
    'date': '2026-11-10',
    'supplier': 'ПП «Альфа-Постач»',
    'tax_id': '2345678',
    'total': 45000.0,
    'vat': 9000.0,
    'currency': 'UAH',
}
APPROVED = {**FIELDS, 'tax_id': '23456789'}


def history_record(fields=None, parsed=None, edited=None, *, number=None, date=None):
    confirmed = fields or APPROVED
    machine = parsed or FIELDS
    return {
        'id': f'demo-{confirmed.get("number")}',
        'fields': confirmed,
        'parsed': machine,
        'edited_fields': edited if edited is not None else ['tax_id'],
        'filename': 'scan_invoice_nov.pdf',
    }


def imported_row(row_id=4, *, fields=None, edited=None, stored=None):
    """A row as the old import left it: `parsed` holds the approved values."""
    approved = fields or APPROVED
    return {
        'id': row_id,
        'number': approved['number'],
        'doc_date': approved['date'],
        'file_path': 'data/history/scan_invoice_nov.pdf',
        'parsed': {'fields': stored or approved, 'issues': [], 'confidence': 0.8},
        'confirmed_fields': None,
        'edited_fields': edited if edited is not None else ['tax_id'],
    }


def upload_row(row_id=21, *, parsed=None, edited=None):
    machine = parsed or {
        'number': '2026-601',
        'date': '2026-11-10',
        'supplier': 'ТОВ «Тест»',
        'total': 5000.0,
    }
    return {
        'id': row_id,
        'number': machine['number'],
        'doc_date': machine['date'],
        'file_path': 'data/uploads/4aa67ec97a.pdf',
        'parsed': {
            'fields': {key: {'value': value, 'confidence': 0.9} for key, value in machine.items()},
            'issues': [],
        },
        'confirmed_fields': None,
        'edited_fields': edited,
    }


def test_an_imported_row_gets_both_snapshots_from_the_history_file():
    [action] = backfill.plan([imported_row()], [history_record()])
    assert action['action'] == 'backfill'
    assert action['source'] == 'history'
    assert action['approved'] == APPROVED
    assert action['machine'] == FIELDS
    assert action['edited'] == ['tax_id']


def test_a_row_without_edits_is_approved_as_read():
    row = upload_row()
    [action] = backfill.plan([row], [])
    assert action['action'] == 'backfill'
    assert action['source'] == 'no-edits'
    assert action['machine'] is None
    assert action['approved']['number'] == '2026-601'


def test_a_row_whose_approved_values_are_lost_is_left_alone():
    row = upload_row(edited=['email', 'bank', 'iban'])
    [action] = backfill.plan([row], [])
    assert action['action'] == 'unresolved', 'an invented approval is worse than a missing one'


def test_a_duplicated_invoice_is_matched_by_its_values():
    record = history_record()
    twin = history_record()
    twin['id'] = 'demo-450-twin'
    [action] = backfill.plan([imported_row()], [record, twin])
    assert action['action'] == 'backfill'
    assert action['approved'] == APPROVED


def test_the_snapshot_that_matches_the_row_wins_over_a_stale_twin():
    record = history_record()
    stale = history_record(fields={**APPROVED, 'total': 1.0}, parsed=FIELDS)
    [action] = backfill.plan([imported_row()], [record, stale])
    assert action['action'] == 'backfill'
    assert action['approved']['total'] == 45000.0, 'only the snapshot matching the row is used'


def test_an_upload_that_happens_to_share_a_number_is_not_rebuilt_from_history():
    row = imported_row(edited=[])
    row['file_path'] = 'data/uploads/deadbeef00.pdf'
    [action] = backfill.plan([row], [history_record()])
    assert action['source'] == 'no-edits', 'history is not evidence for a different row'


def test_a_filled_row_is_left_untouched():
    row = imported_row()
    row['confirmed_fields'] = APPROVED
    [action] = backfill.plan([row], [history_record()])
    assert action['action'] == 'already'


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('backfill checks passed')


if __name__ == '__main__':
    main()
