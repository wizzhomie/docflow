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
    return {'fields': dict(pointers)}


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


def test_a_buyer_first_tax_id_is_not_accepted_for_the_supplier():
    """The supplier anchor decides the block: a tax id before it is the buyer's."""
    text = '\n'.join([
        'Счёт на оплату № КП-79 от 9 мая 2026',
        'Покупатель: ТОВ «Вода-Град»',
        'ИНН 46187055',
        'Поставщик: ФОП Савченко Дмитро Вікторович',
        'ИНН 4530378074',
    ])
    supplier = {'line': 4, 'text': 'ФОП Савченко Дмитро Вікторович'}
    buyer_tax = {'line': 3, 'text': '46187055'}
    assert 'tax_id' not in meaning.resolve(
        fake_plan(supplier=supplier, tax_id=buyer_tax), text)
    resolved = meaning.resolve(
        fake_plan(supplier=supplier,
                  tax_id={'line': 5, 'text': '4530378074'}), text)
    assert resolved['tax_id'].value == '4530378074'


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


def invoice_text() -> str:
    return '\n'.join([
        'Рахунок на оплату № 2026-501 від 27 січня 2026',
        'Постачальник  ТОВ «Смак-Дистрибуція»',
        'ЄДРПОУ  41234567',
        'Всього до сплати: 1 234,56 грн',
        'в т.ч. ПДВ 20% — 205,76 грн',
    ])


def test_a_changed_value_gets_an_honest_weight_and_is_not_called_verified(monkeypatch):
    parsed = {'fields': {'total': {'value': 1.0, 'confidence': 0.9, 'base_confidence': 0.9}}}
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        total={'line': 4, 'text': '1 234,56 грн'}))
    meaning.apply(parsed, invoice_text())
    field = parsed['fields']['total']
    assert field['value'] == 1234.56
    assert field['verified'] is False
    assert field['confidence'] == 0.6  # the weakest anchor, not a made-up 0.97
    assert field['provenance']['by'] == 'model-pointer'


def test_an_agreeing_pointer_keeps_the_parser_weight(monkeypatch):
    parsed = {'fields': {'date': {'value': '2026-01-27', 'confidence': 0.9,
                                  'base_confidence': 0.9}}}
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        date={'line': 1, 'text': '27 січня 2026'}))
    meaning.apply(parsed, invoice_text())
    field = parsed['fields']['date']
    assert field['confidence'] == 0.9
    assert field['provenance']['line'] == 1


def test_a_pointer_to_the_vat_line_is_caught_by_revalidation(monkeypatch, tmp_path):
    """The defect: a real line, the wrong one — the VAT line copied as the total."""
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'on')
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        total={'line': 5, 'text': '205,76 грн'}))
    path = tmp_path / 'invoice.txt'
    path.write_text(invoice_text(), encoding='utf-8')
    meta = intake._process('doc0000001', 'invoice.txt', path, persist=False)
    total = meta['parsed']['fields']['total']
    assert total['value'] == 205.76
    assert total['verified'] is False
    # The invariants saw the change: total is not above its own VAT line.
    assert any(issue.startswith('total') for issue in meta['parsed']['issues'])
    assert total['confidence'] == 0.3


def test_a_failed_revalidation_does_not_leave_the_model_value_behind(monkeypatch, tmp_path):
    """The merge is in place: if the invariants cannot run, the parser's record stands."""
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'on')
    monkeypatch.setattr(meaning, '_ask', lambda text: fake_plan(
        total={'line': 4, 'text': '1 234,56 грн'}))

    def broken(*args, **kwargs):
        raise RuntimeError('the invariants could not run')

    monkeypatch.setattr(intake, 'revise', broken)
    path = tmp_path / 'invoice.txt'
    path.write_text(invoice_text(), encoding='utf-8')
    meta = intake._process('doc0000002', 'invoice.txt', path, persist=False)
    # The record is the parser's record, field for field: no value survived that no
    # invariant has seen.
    monkeypatch.setattr(settings, 'MEANING', 'off')
    plain = intake._process('doc0000003', 'invoice.txt', path, persist=False)
    assert meta['parsed']['fields'] == plain['parsed']['fields']
    assert 'meaning' not in meta


def test_a_failed_model_call_is_logged_with_its_reason(monkeypatch, caplog):
    """A throttled model must not look like an answer with no pointers."""
    import logging

    import httpx

    def refuse(*args, **kwargs):
        raise httpx.ConnectError('boom')

    monkeypatch.setattr(meaning.httpx, 'post', refuse)
    with caplog.at_level(logging.WARNING, logger='docflow'):
        assert meaning._ask(doc_text()) == {}
    assert 'meaning layer' in caplog.text


def test_the_layer_does_not_run_when_it_is_off(monkeypatch, tmp_path):
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'off')
    calls: list[str] = []
    monkeypatch.setattr(meaning, '_ask', lambda text: calls.append(text) or {})
    path = tmp_path / 'invoice.txt'
    path.write_text(invoice_text(), encoding='utf-8')
    meta = intake._process('doc0000002', 'invoice.txt', path, persist=False)
    assert calls == []
    assert 'meaning' not in meta
