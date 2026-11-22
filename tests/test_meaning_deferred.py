"""The model step after the response, not inside it.

The layer used to sit in the upload path: every document waited for the model, and an
integration's POST waited with it. That is harmless while the layer is off, but it is
the reason it could not be turned on: eleven seconds per upload is inside several
clients' timeouts.

These tests hold the new contract: the request finishes without the model, the record
is improved afterwards through the same invariants, and a document a person confirmed
in the meantime is never rewritten.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docstore
import intake
import meaning
import settings

DOC_ID = 'a1b2c3d4e5'  # ten hex characters, the shape the file store accepts

# The rules read this total and VAT correctly, so what changes below is the model's
# pointer, not a defect of the parser: the test is about plumbing, not judgement.
TEXT = '\n'.join([
    'Рахунок на оплату № 2026-501 від 27 січня 2026',
    'Постачальник  ТОВ «Смак-Дистрибуція»',
    'ЄДРПОУ  41234567',
    'Сума без ПДВ: 1 028,80 грн',
    'ПДВ 20%: 205,76 грн',
    'Всього до сплати: 1 234,56 грн',
])


def stored(doc_id: str = DOC_ID) -> dict:
    return json.loads(docstore._meta_path(doc_id).read_text(encoding='utf-8'))


def prepare(tmp_path, monkeypatch) -> Path:
    """The file store of one test: pending records and the confirmation history."""
    monkeypatch.setattr(settings, 'PENDING', tmp_path)
    monkeypatch.setattr(settings, 'CONFIRMED', tmp_path / 'confirmed.jsonl')
    source = tmp_path / 'invoice.txt'
    source.write_text(TEXT, encoding='utf-8')
    return source


def test_the_document_is_stored_before_the_model_is_asked(tmp_path, monkeypatch):
    """The upload returns with the rules' values and a note that the model is queued."""
    source = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, 'MEANING', 'on')
    calls: list[list[str]] = []
    monkeypatch.setattr(
        meaning, '_ask', lambda text, fields=None: calls.append(list(fields)) or {})

    meta = intake._process(DOC_ID, 'invoice.txt', source, persist=True, defer_meaning=True)

    assert calls == [], 'the request must not wait for the model'
    assert meta['meaning'] == {'mode': 'on', 'state': 'queued'}
    assert meta['parsed']['fields']['total']['value'] == 1234.56
    record = stored()
    assert record['meaning']['state'] == 'queued'
    assert '_source_text' not in record, 'the extracted text is not a record field'


def test_the_deferred_step_lands_in_the_stored_record(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, 'MEANING', 'on')

    source = tmp_path / 'invoice.txt'
    meta = intake._process(DOC_ID, 'invoice.txt', source, persist=True, defer_meaning=True)
    text = meta.pop('_source_text')

    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: {
        'fields': {'total': {'line': 4, 'text': '1 028,80'}}})
    intake.finish_meaning(meta, text)

    record = stored()
    assert record['parsed']['fields']['total']['value'] == 1028.8
    assert record['meaning']['state'] == 'done'
    assert set(record['meaning']['asked']) == {'total', 'vat'}
    assert record['parsed']['fields']['total']['provenance']['line'] == 4


def test_a_confirmed_document_is_not_rewritten(tmp_path, monkeypatch):
    """A person's record wins over a model that finished reading afterwards."""
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, 'MEANING', 'on')

    source = tmp_path / 'invoice.txt'
    meta = intake._process(DOC_ID, 'invoice.txt', source, persist=True, defer_meaning=True)
    text = meta.pop('_source_text')
    monkeypatch.setattr(docstore, '_read_confirmed', lambda: [{'id': DOC_ID}])
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: {
        'fields': {'total': {'line': 4, 'text': '1 028,80'}}})

    intake.finish_meaning(meta, text)

    record = stored()
    assert record['parsed']['fields']['total']['value'] == 1234.56
    assert 'meaning' not in record or record['meaning']['state'] == 'queued'


def test_a_failed_deferred_step_leaves_the_rules_values(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, 'MEANING', 'on')

    source = tmp_path / 'invoice.txt'
    meta = intake._process(DOC_ID, 'invoice.txt', source, persist=True, defer_meaning=True)
    text = meta.pop('_source_text')
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: {})

    assert intake.finish_meaning(meta, text) == {}
    record = stored()
    assert record['parsed']['fields']['total']['value'] == 1234.56
    assert record['parsed']['issues'] == meta['parsed']['issues']


def test_nothing_is_scheduled_when_the_layer_is_off(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, 'MEANING', 'off')
    calls: list[list[str]] = []
    monkeypatch.setattr(
        meaning, '_ask', lambda text, fields=None: calls.append(list(fields)) or {})

    source = tmp_path / 'invoice.txt'
    meta = intake._process(DOC_ID, 'invoice.txt', source, persist=True, defer_meaning=True)

    assert calls == []
    assert '_source_text' not in meta
    assert 'meaning' not in stored()
