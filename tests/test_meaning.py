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
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
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
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
        date={'line': 1, 'text': '27 січня 2026'}))
    resolved = meaning.apply(parsed, doc_text(), shadow=True)
    assert resolved['date'].value == '2026-01-27'
    assert parsed['fields']['date']['value'] == '2026-01-01'


def test_an_unreachable_model_returns_nothing_and_changes_nothing(monkeypatch):
    parsed = {'fields': {'date': {'value': '2026-01-01'}}}
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: {})
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
    parsed = {'fields': {'total': {'value': 1.0, 'confidence': 0.45, 'base_confidence': 0.9}},
              'issues': ['total: invariant failed']}
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
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
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
        date={'line': 1, 'text': '27 січня 2026'}))
    meaning.apply(parsed, invoice_text(), fields=['date'])
    field = parsed['fields']['date']
    assert field['confidence'] == 0.9
    assert field['provenance']['line'] == 1


def test_a_pointer_to_the_vat_line_is_caught_by_revalidation(monkeypatch, tmp_path):
    """The defect: a real line, the wrong one — the VAT line copied as the total."""
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'on')
    # The gap test is separate: here the document is asked about its total on purpose.
    monkeypatch.setattr(meaning, 'gaps', lambda parsed: ['total'])
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
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
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
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


def test_an_unescaped_quote_in_a_copied_name_is_repaired():
    """A supplier printed as ТОВ "РЕГІОН ДСТ" breaks the JSON the model returns."""
    answer = (
        '{"fields": {"supplier": {"line": 2, "text": "ТОВ "РЕГІОН ДСТ""}, '
        '"total": {"line": 5, "text": "2 500,00"}}}'
    )
    assert meaning._loads(answer) == {
        'fields': {
            'supplier': {'line': 2, 'text': 'ТОВ "РЕГІОН ДСТ"'},
            'total': {'line': 5, 'text': '2 500,00'},
        }
    }


def test_valid_json_is_not_touched_by_the_repair():
    answer = '{"fields": {"number": {"line": 1, "text": "104038"}}}'
    assert meaning._loads(answer) == {
        'fields': {'number': {'line': 1, 'text': '104038'}}
    }


def test_the_layer_does_not_run_when_it_is_off(monkeypatch, tmp_path):
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'off')
    calls: list[str] = []
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: calls.append(text) or {})
    path = tmp_path / 'invoice.txt'
    path.write_text(invoice_text(), encoding='utf-8')
    meta = intake._process('doc0000002', 'invoice.txt', path, persist=False)
    assert calls == []
    assert 'meaning' not in meta


def parsed_with(**fields) -> dict:
    """A parse in the shape the extractor produces, with an optional issue list."""
    issues = fields.pop('issues', [])
    return {
        'fields': {
            name: {'value': value[0], 'confidence': value[1],
                   'base_confidence': value[1]}
            for name, value in fields.items()
        },
        'issues': issues,
    }


def test_a_field_the_rules_answered_is_not_asked_about():
    """The gap is the whole point of the change: an approved value costs no tokens.

    The money fields are the exception, and on purpose: no deterministic check can
    confirm them, so they are asked about even when the rules are confident.
    """
    parsed = parsed_with(
        number=('104038', 0.95),
        supplier=('ТОВ «Чистий дім»', 0.85),
        total=(487776.0, 0.9),
    )
    assert meaning.gaps(parsed) == ['date', 'tax_id', 'total', 'vat', 'currency']
    assert 'number' not in meaning.gaps(parsed)
    assert 'supplier' not in meaning.gaps(parsed)


def test_an_invariant_that_refused_the_value_opens_the_gap():
    parsed = parsed_with(
        total=(81296.0, 0.45),
        issues=['total: invariant failed'],
    )
    assert 'total' in meaning.gaps(parsed)


def test_a_low_weight_opens_the_gap_even_without_an_issue():
    """`items_sum` halves the money weights without naming the field in the issues."""
    parsed = parsed_with(total=(8000.0, 0.45), vat=(0.0, 0.38),
                         issues=['items_sum: invariant failed'])
    assert 'total' in meaning.gaps(parsed) and 'vat' in meaning.gaps(parsed)


def test_a_clean_document_asks_only_about_the_money_fields(monkeypatch):
    """Seven fields became two: that is where the speed comes from."""
    parsed = parsed_with(
        number=('104038', 0.95), date=('2025-12-09', 0.9),
        supplier=('ТОВ «Чистий дім»', 0.85), tax_id=('41234567', 0.85),
        total=(1234.56, 0.9), vat=(205.76, 0.75), currency=('UAH', 0.6),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        meaning, '_ask', lambda text, fields=None: calls.append(list(fields)) or {})
    assert meaning.apply(parsed, invoice_text()) == {}
    assert calls == [['total', 'vat']]


def test_the_prompt_names_only_the_asked_fields(monkeypatch):
    """The answer is the slow part; the request must not invite values nobody needs."""
    sent: list[dict] = []

    class Answer:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '{}'}}]}

    def capture(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return Answer()

    monkeypatch.setattr(meaning.httpx, 'post', capture)
    meaning._ask(doc_text(), ['date', 'total'])
    prompt = sent[0]['messages'][0]['content']
    field_line = next(line for line in prompt.splitlines() if line.startswith('Поля:'))
    assert field_line.strip() == 'Поля: date, total.'


def test_the_layer_records_what_it_asked(monkeypatch, tmp_path):
    """A shadow run must say which fields were opened, or it cannot be analysed."""
    import intake
    import settings

    monkeypatch.setattr(settings, 'MEANING', 'shadow')
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: fake_plan(
        total={'line': 4, 'text': '205,76 грн'}))
    # The total equals its own VAT line: the invariant refuses it, so it is a gap.
    text = '\n'.join([
        'Рахунок на оплату № 2026-501 від 27 січня 2026',
        'Постачальник  ТОВ «Смак-Дистрибуція»',
        'ЄДРПОУ  41234567',
        'Всього до сплати: 205,76 грн',
        'в т.ч. ПДВ 20% — 205,76 грн',
    ])
    path = tmp_path / 'invoice.txt'
    path.write_text(text, encoding='utf-8')
    meta = intake._process('doc0000004', 'invoice.txt', path, persist=False)
    asked = meta['meaning']['asked']
    assert 'total' in asked
    assert meta['meaning']['fields']['total']['line'] == 4
