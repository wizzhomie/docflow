"""Deterministic checks for the automation-impact report.

Run: python3 tests/test_impact.py

The report must stay a pure function of confirmed records: these cases pin the
arithmetic the business story relies on (auto-pass, duplicates, review time,
time saved, invariant risk).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def row(doc_id, edited=None, issues=None, uploaded='2026-11-13T10:00:00+00:00', confirmed='2026-11-13T10:01:00+00:00'):
    record = {
        'id': doc_id,
        'uploaded_at': uploaded,
        'confirmed_at': confirmed,
        'edited_fields': edited or [],
    }
    if issues is not None:
        record['issues'] = issues
    return record


def test_empty():
    impact = app.build_impact([], 3.0)
    assert impact['documents'] == 0
    assert impact['auto_pass_rate'] == 0.0
    assert impact['top_corrections'] == []
    assert impact['time_saved_minutes'] == 0.0
    assert impact['period'] == {'from': None, 'to': None}


def test_auto_pass_and_corrections():
    rows = [
        row('a'),
        row('b', edited=['supplier', 'supplier', 'total']),
    ]
    impact = app.build_impact(rows, 3.0)
    assert impact['documents'] == 2
    assert impact['unique_documents'] == 2
    assert impact['auto_pass_rate'] == 50.0
    assert impact['edited_fields'] == 3
    assert impact['top_corrections'][0] == {'field': 'supplier', 'label': 'Supplier', 'count': 2}
    assert impact['top_corrections'][1]['field'] == 'total'


def test_duplicates_counted_but_not_deduplicated_in_rates():
    rows = [row('a'), row('a')]
    impact = app.build_impact(rows, 3.0)
    assert impact['documents'] == 2
    assert impact['unique_documents'] == 1
    assert impact['duplicate_confirmations'] == 1


def test_review_time_and_savings():
    rows = [
        row('fast', uploaded='2026-11-13T10:00:00+00:00', confirmed='2026-11-13T10:00:30+00:00'),
        row('slow', uploaded='2026-11-13T10:00:00+00:00', confirmed='2026-11-13T10:05:00+00:00'),
    ]
    impact = app.build_impact(rows, 3.0)
    assert impact['review']['measured_documents'] == 2
    assert impact['review']['median_seconds'] == 165.0
    # 30 s -> 2.5 min saved, 300 s -> 0 saved (already over the manual baseline)
    assert impact['time_saved_minutes'] == 2.5


def test_stored_review_seconds_win_over_timestamps():
    record = row('x', uploaded='2026-11-13T10:00:00+00:00', confirmed='2026-11-13T10:10:00+00:00')
    record['review_seconds'] = 12
    impact = app.build_impact([record], 3.0)
    assert impact['review']['median_seconds'] == 12.0


def test_risk_of_confirmed_invariant_failures():
    rows = [
        row('risky', issues=['supplier: invariant failed']),
        row('fixed', edited=['supplier'], issues=['supplier: invariant failed']),
        row('clean'),
    ]
    impact = app.build_impact(rows, 3.0)
    assert impact['validation']['documents_with_issues'] == 2
    assert impact['validation']['unreviewed_risk_documents'] == 1
    assert impact['validation']['top'] == [{'issue': 'supplier: invariant failed', 'count': 2}]


def test_legacy_rows_fall_back_to_pending_snapshot():
    record = row('legacy', edited=[])
    original = app._issues_for
    app._issues_for = lambda doc_id: ['vat: invariant failed'] if doc_id == 'legacy' else []
    try:
        impact = app.build_impact([record], 3.0)
    finally:
        app._issues_for = original
    assert impact['validation']['documents_with_issues'] == 1
    assert impact['validation']['unreviewed_risk_documents'] == 1


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('impact checks passed')


if __name__ == '__main__':
    main()
