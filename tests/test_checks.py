"""The scripted checks must fail when the thing they claim to test is broken.

A registry is only worth having if a broken check is noticed as quickly as a broken
parser, and if a check that has nothing to test says so instead of counting as a
pass. These tests drive the checks with hand-made manifests — no PDF, no OCR — so a
check that would pass anything is caught without a corpus.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import checks  # noqa: E402


def document(fields: dict | None = None, items=None, preview: str = 'Рахунок 12'):
    return {
        'parsed': {'fields': {name: {'value': value} for name, value in (fields or {}).items()}},
        'items': items or [],
        'text_preview': preview,
    }


def entry(printed: dict | None = None, expected: dict | None = None) -> dict:
    return {'printed': printed or {}, 'expected': expected or {}}


def test_every_check_has_a_unique_id_and_an_invariant():
    ids = [item.id for item in checks.CHECKS]
    assert len(ids) == len(set(ids))
    assert all(item.invariant for item in checks.CHECKS)


def test_a_dropped_number_fails_the_number_check():
    item = entry({'number': 'КП-77'}, {'number': 'КП-77'})
    check = checks.by_id('F1')
    assert check.check(item, document()) is False
    assert check.check(item, document({'number': 'КП-77'})) is True
    # Spaces around the token are formatting, not a different number.
    assert check.check(item, document({'number': 'КП - 77'})) is True


def test_a_wrong_number_fails_even_when_it_is_not_empty():
    item = entry({'number': 'КП-77'}, {'number': 'КП-77'})
    check = checks.by_id('F1')
    assert check.check(item, document({'number': 'КП-7'})) is False


def test_a_word_date_must_match_the_printed_date():
    item = entry({'date': '17 березня 2026'}, {'date': '2026-03-17'})
    check = checks.by_id('F2')
    assert check.check(item, document()) is False
    assert check.check(item, document({'date': '2026-03-17'})) is True
    assert check.check(item, document({'date': '2026-03-18'})) is False


def test_a_check_says_not_applicable_instead_of_passing():
    item = entry({'date': '17.03.2026'}, {})
    assert checks.by_id('F2').check(item, document()) is None


def test_a_tax_id_inside_the_supplier_name_fails_the_leak_check():
    item = entry({}, {'tax_id': '3244793895'})
    check = checks.by_id('L1')
    leaked = document({'supplier': 'ФОП Руденко Тетяна, РНОКПП 3244793895'})
    clean = document({'supplier': 'ФОП Руденко Тетяна'})
    assert check.check(item, leaked) is False
    assert check.check(item, clean) is True


def test_a_supplier_called_television_is_not_a_requisite_leak():
    item = entry({}, {'tax_id': '12345678'})
    check = checks.by_id('L2')
    assert check.check(item, document({'supplier': 'ТОВ «Телеканал»'})) is True
    assert check.check(item, document({'supplier': 'ТОВ «Телеканал», тел. 044 123 45 67'})) is False


def test_a_total_equal_to_vat_fails_the_total_check():
    item = entry({'number': '1'}, {'total': 33614.82, 'vat': 5602.47})
    total_check = checks.by_id('F6')
    wrong = document({'total': 5602.47, 'vat': 5602.47})
    right = document({'total': 33614.82, 'vat': 5602.47})
    assert total_check.check(item, wrong) is False
    assert total_check.check(item, right) is True


def test_vat_is_not_checked_on_a_document_without_vat():
    item = entry({}, {'total': 100.0, 'vat': None})
    assert checks.by_id('F4').check(item, document({'total': 100.0})) is None


def test_extracted_text_must_contain_the_printed_number():
    item = entry({'number': '2026-970'}, {})
    check = checks.by_id('S3')
    assert check.check(item, document(preview='Рахунок Ne 2026-970')) is True
    assert check.check(item, document(preview='Рахунок')) is False


def test_a_scan_is_compared_with_its_text_twin():
    item = entry({'number': '1'}, {})
    item['_twin'] = document({'number': '1', 'total': 10.0})
    check = checks.by_id('S5')
    assert check.check(item, document({'number': '1', 'total': 10.0})) is True
    assert check.check(item, document({'number': '1', 'total': 11.0})) is False
    assert checks.by_id('S5').check(entry({}, {}), document()) is None
