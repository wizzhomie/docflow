"""The scripted checks must fail when the thing they claim to test is broken.

A check registry is only worth having if a broken check is noticed as quickly as a
broken parser. These tests drive a few checks with hand-made manifests — no PDF,
no OCR — so a check that would pass anything is caught without a corpus.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import checks  # noqa: E402


def document(fields: dict | None = None, items=None, preview: str = 'Рахунок'):
    return {
        'parsed': {'fields': {name: {'value': value} for name, value in (fields or {}).items()}},
        'items': items or [],
        'text_preview': preview,
    }


def test_every_check_has_a_unique_id_and_an_invariant():
    ids = [entry.id for entry in checks.CHECKS]
    assert len(ids) == len(set(ids))
    assert all(entry.invariant for entry in checks.CHECKS)


def test_a_dropped_number_fails_the_number_check():
    entry = {'printed': {'number': 'КП-77'}, 'expected': {}}
    check = checks.by_id('F1')
    assert check.check(entry, document()) is False
    assert check.check(entry, document({'number': 'КП-77'})) is True


def test_a_dropped_date_fails_the_date_check():
    entry = {'printed': {'date': '17 березня 2026'}, 'expected': {}}
    check = checks.by_id('F2')
    assert check.check(entry, document()) is False
    assert check.check(entry, document({'date': '2026-03-17'})) is True


def test_a_tax_id_inside_the_supplier_name_fails_the_leak_check():
    entry = {'printed': {'number': '1'}, 'expected': {'tax_id': '3244793895'}}
    check = checks.by_id('L1')
    leaked = document({'supplier': 'ФОП Руденко Тетяна, РНОКПП 3244793895'})
    clean = document({'supplier': 'ФОП Руденко Тетяна'})
    assert check.check(entry, leaked) is False
    assert check.check(entry, clean) is True


def test_a_total_that_is_not_the_sum_of_lines_fails():
    entry = {'printed': {'number': '1'}, 'expected': {}}
    items = [{'quantity': 2, 'price': 100.0}]
    check = checks.by_id('F3')
    assert check.check(entry, document({'total': 150.0}, items=items)) is False
    assert check.check(entry, document({'total': 200.0}, items=items)) is True


def test_an_empty_text_preview_fails_the_extraction_check():
    entry = {'printed': {}, 'expected': {}}
    check = checks.by_id('S3')
    assert check.check(entry, document(preview='   ')) is False
    assert check.check(entry, document(preview='Рахунок')) is True
