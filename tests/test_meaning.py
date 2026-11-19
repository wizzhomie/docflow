"""The meaning layer: a model points, the code copies.

The point of these tests is the boundary, not the model: a pointer that names
text which is not on that line must change nothing, a copied value must be parsed
as a format (not as a meaning), and shadow mode must record without answering.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import meaning


def fake_plan(**pointers) -> dict:
    return {'fields': {name: pointer for name, pointer in pointers.items()}}


def doc_text() -> str:
    return '\n'.join([
        'Рахунок на оплату № 2026-501 від 27 січня 2026',
        'Постачальник  ТОВ «Смак-Дистрибуція»',
        'ЄДРПОУ  41234567',
        'Всього до сплати: 1 234,56 грн',
        'в т.ч. ПДВ 20% — 205,76 грн',
    ])


def test_a_pointer_that_names_the_text_is_copied_and_parsed():
    text = doc_text()
    resolved = meaning.resolve(fake_plan(
        date={'line': 1, 'text': '27 січня 2026'},
        tax_id={'line': 3, 'text': '41234567'},
        total={'line': 4, 'text': 'Всього до сплати: 1 234,56 грн'},
    ), text)
    assert resolved['date'].value == '2026-01-27'
    assert resolved['tax_id'].value == '41234567'
    assert resolved['total'].value == 1234.56


def test_a_pointer_to_text_that_is_not_on_the_line_is_dropped():
    text = doc_text()
    resolved = meaning.resolve(fake_plan(
        total={'line': 2, 'text': '1 234,56'},
        date={'line': 99, 'text': '27 січня 2026'},
    ), text)
    assert resolved == {}


def test_the_longest_tax_id_wins_because_a_prefix_is_a_wrong_number():
    text = doc_text()
    resolved = meaning.resolve(fake_plan(tax_id={'line': 3, 'text': '41234567'}), text)
    assert resolved['tax_id'].value == '41234567'
    assert meaning.parse_tax('3244793895') == '3244793895'


def test_applying_a_pointer_replaces_the_field_and_records_provenance(monkeypatch):
    parsed = {'fields': {'date': {'value': None}, 'total': {'value': None}}}
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        date={'line': 1, 'text': '27 січня 2026'},
        total={'line': 4, 'text': 'Всього до сплати: 1 234,56 грн'},
    ))
    meaning.apply(parsed, doc_text())
    assert parsed['fields']['date']['value'] == '2026-01-27'
    assert parsed['fields']['total']['value'] == 1234.56
    assert parsed['fields']['date']['provenance'] == {
        'line': 1, 'text': '27 січня 2026', 'by': 'model-pointer',
    }


def test_shadow_mode_records_without_touching_the_parsed_fields(monkeypatch):
    parsed = {'fields': {'date': {'value': '2026-01-01'}}}
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        date={'line': 1, 'text': '27 січня 2026'}))
    resolved = meaning.apply(parsed, doc_text(), shadow=True)
    assert resolved['date'].value == '2026-01-27'
    assert parsed['fields']['date']['value'] == '2026-01-01'


def test_an_unreachable_model_returns_nothing_and_changes_nothing(monkeypatch):
    parsed = {'fields': {'date': {'value': '2026-01-01'}}}
    monkeypatch.setattr(meaning, '_ask', lambda text: {})
    assert meaning.apply(parsed, doc_text()) == {}
    assert parsed['fields']['date']['value'] == '2026-01-01'
