"""The machine door: a key instead of a session cookie, the same pipeline behind it.

An integration is a machine, so the checks are about what a machine may and may not
do: read what it uploaded, never somebody else's document, confirm only when its
key says so, and — when it does confirm — leave a record that names the key. The
other half is what the door must not become: a second implementation of the
pipeline. It is the same extraction, the same invariants and the same confirmation
the review form calls.

Run: python3 tests/test_api_v1.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apikeys  # noqa: E402
import app  # noqa: E402
import settings  # noqa: E402
from routes import api_v1  # noqa: E402

# Every check builds a store of its own; the directories are removed after it, so
# a run does not leave seventeen copies of the demo behind in the working tree.
_CREATED: list[Path] = []


@pytest.fixture(autouse=True)
def _remove_the_stores():
    yield
    for root in _CREATED:
        shutil.rmtree(root, ignore_errors=True)
    _CREATED.clear()

INVOICE = (
    'Рахунок на оплату № API-17 від 12 лютого 2026\n'
    'Постачальник: ТОВ «Платформа-Тест», ЄДРПОУ 41234567\n'
    'Всього: 1 234,56 грн\n'
)

SCAN_NAME = 'scan.pdf'


def isolate() -> Path:
    """A store of one test's own: the checks upload, confirm and revoke."""
    root = Path(tempfile.mkdtemp(prefix='docflow-api-'))
    _CREATED.append(root)
    settings.UPLOADS = root / 'uploads'
    settings.PENDING = root / 'pending'
    settings.INBOX = root / 'inbox'
    settings.INBOX_DONE = settings.INBOX / 'processed'
    settings.CONFIRMED = root / 'confirmed.jsonl'
    settings.API_KEYS = root / 'api_keys.json'
    settings.API_EXTERNAL_IDS = root / 'api_external_ids.json'
    for directory in (settings.UPLOADS, settings.PENDING, settings.INBOX, settings.INBOX_DONE):
        directory.mkdir(parents=True, exist_ok=True)
    settings.CONFIRMED.write_text('', encoding='utf-8')
    settings.DEMO_MODE = True
    settings.API_ENABLED = True
    return root


def issue(scopes=None, company=None) -> str:
    _, token = apikeys.issue('integration', scopes, company)
    return token


def upload(client: TestClient, token: str, external_id: str | None = None, name='invoice.txt'):
    form = {'external_id': external_id} if external_id else None
    return client.post(
        '/api/v1/documents',
        headers={'Authorization': f'Bearer {token}'},
        files={'file': (name, INVOICE.encode('utf-8'), 'text/plain')},
        data=form,
    )


def test_without_a_key_the_door_answers_401():
    isolate()
    with TestClient(app.app) as client:
        answer = client.get('/api/v1/documents')
        assert answer.status_code == 401
        assert answer.headers['www-authenticate'].startswith('Bearer')


def test_a_token_that_was_never_issued_is_not_a_key():
    isolate()
    with TestClient(app.app) as client:
        answer = client.get('/api/v1/me', headers={'X-API-Key': 'dfk_000000000000_' + '0' * 48})
        assert answer.status_code == 401


def test_a_revoked_key_stops_working():
    isolate()
    token = issue()
    record = [key for key in apikeys.listing() if key['label'] == 'integration'][0]
    assert apikeys.revoke(record['id'])
    with TestClient(app.app) as client:
        assert client.get('/api/v1/me', headers={'X-API-Key': token}).status_code == 401


def test_a_reader_cannot_confirm():
    """The scope is the gate: a key issued to read results has no right to approve."""
    isolate()
    token = issue(scopes=['documents:read'])
    with TestClient(app.app) as client:
        assert client.get('/api/v1/me', headers={'X-API-Key': token}).status_code == 200
        answer = client.post(
            '/api/v1/documents/aaaaaaaaaa/confirm', headers={'X-API-Key': token}, json={}
        )
        assert answer.status_code == 403


def test_an_upload_answers_with_the_fields_the_pipeline_extracted():
    isolate()
    token = issue()
    with TestClient(app.app) as client:
        answer = upload(client, token)
        assert answer.status_code == 201
        body = answer.json()
        assert body['status'] == 'pending'
        assert body['values']['tax_id'] == '41234567'
        assert body['values']['total'] == 1234.56
        assert body['confidence'] is not None
        assert isinstance(body['issues'], list)
        # The value keeps the confidence it was read with: an integration that gets
        # one number for every field cannot tell a guess from a reading.
        assert 'confidence' in body['fields']['total']


def test_the_same_external_id_returns_the_same_document():
    """A retried upload is the same document, not a second one in the queue."""
    isolate()
    token = issue()
    with TestClient(app.app) as client:
        first = upload(client, token, external_id='ERP-77').json()
        again = upload(client, token, external_id='ERP-77')
        assert again.status_code == 200
        assert again.json()['id'] == first['id']
        assert again.json()['idempotent'] is True
        assert len(list(settings.PENDING.glob('*.json'))) == 1


def test_a_document_belongs_to_the_key_that_uploaded_it():
    isolate()
    first, second = issue(), issue()
    with TestClient(app.app) as client:
        mine = upload(client, first).json()
        assert client.get(f"/api/v1/documents/{mine['id']}", headers={'X-API-Key': first}).status_code == 200
        assert (
            client.get(f"/api/v1/documents/{mine['id']}", headers={'X-API-Key': second}).status_code
            == 404
        )


def test_confirm_records_who_approved_it():
    isolate()
    token = issue(scopes=['documents:write', 'documents:read', 'documents:confirm'])
    with TestClient(app.app) as client:
        meta = upload(client, token).json()
        answer = client.post(
            f"/api/v1/documents/{meta['id']}/confirm",
            headers={'X-API-Key': token},
            json={},
        )
        assert answer.status_code == 200
        body = answer.json()
        assert body['document']['status'] == 'confirmed'
        record = body['confirmation']['record']
        assert record['confirmed_by'].startswith('api:')
        assert record['api_key'] == apikeys.listing()[0]['id']
        again = client.post(
            f"/api/v1/documents/{meta['id']}/confirm", headers={'X-API-Key': token}, json={}
        )
        assert again.status_code == 409


def test_a_value_that_breaks_an_invariant_is_refused():
    """The API confirms through the form's rules: a bad tax id is not accepted here."""
    isolate()
    token = issue(scopes=['documents:write', 'documents:read', 'documents:confirm'])
    with TestClient(app.app) as client:
        meta = upload(client, token).json()
        answer = client.post(
            f"/api/v1/documents/{meta['id']}/confirm",
            headers={'X-API-Key': token},
            json={'fields': {'tax_id': '123'}},
        )
        assert answer.status_code == 422
        assert 'Tax ID' in answer.json()['detail']


def test_the_upload_is_listed_until_it_is_confirmed():
    isolate()
    token = issue(scopes=['documents:write', 'documents:read', 'documents:confirm'])
    with TestClient(app.app) as client:
        meta = upload(client, token).json()
        pending = client.get('/api/v1/documents', headers={'X-API-Key': token}).json()
        assert [item['id'] for item in pending['documents']] == [meta['id']]
        client.post(
            f"/api/v1/documents/{meta['id']}/confirm", headers={'X-API-Key': token}, json={}
        )
        after = client.get('/api/v1/documents', headers={'X-API-Key': token}).json()
        assert after['documents'] == []
        confirmed = client.get(
            '/api/v1/documents?status=confirmed', headers={'X-API-Key': token}
        ).json()
        assert [item['status'] for item in confirmed['documents']] == ['confirmed']


def test_the_text_door_skips_ocr_but_not_the_parser():
    isolate()
    token = issue(scopes=['documents:write'])
    with TestClient(app.app) as client:
        answer = client.post(
            '/api/v1/documents/text',
            headers={'X-API-Key': token},
            json={'text': INVOICE, 'filename': 'invoice.txt', 'external_id': 'TXT-1'},
        )
        assert answer.status_code == 201
        assert answer.json()['values']['total'] == 1234.56


def test_a_format_the_pipeline_does_not_read_is_refused():
    isolate()
    token = issue(scopes=['documents:write'])
    with TestClient(app.app) as client:
        answer = client.post(
            '/api/v1/documents',
            headers={'X-API-Key': token},
            files={'file': ('setup.exe', b'MZ', 'application/octet-stream')},
        )
        assert answer.status_code == 400


def test_the_key_file_keeps_no_plaintext():
    isolate()
    token = issue()
    stored = settings.API_KEYS.read_text(encoding='utf-8')
    assert token not in stored
    assert apikeys._digest(token.split('_', 2)[2]) in stored


def test_a_turned_off_door_is_not_found():
    isolate()
    token = issue()
    settings.API_ENABLED = False
    with TestClient(app.app) as client:
        assert client.get('/api/v1/me', headers={'X-API-Key': token}).status_code == 404


def test_a_company_key_without_the_database_is_told_so():
    """A company-bound key must not quietly write into a shared file store."""
    isolate()
    token = issue(company=1)
    with TestClient(app.app) as client:
        answer = client.get('/api/v1/documents', headers={'X-API-Key': token})
        assert answer.status_code == 503
        assert 'database' in answer.json()['detail'].lower()


def test_an_integration_document_reaches_a_real_workspace():
    """On a workspace the operator sees what the integration uploaded; the public
    showcase does not list it, because a demonstration is not somebody's inbox."""
    isolate()
    token = issue()
    with TestClient(app.app) as client:
        meta = upload(client, token).json()
        assert [item['id'] for item in client.get('/api/inbox').json()] == []
    settings.DEMO_MODE = False
    with TestClient(app.app) as client:
        assert [item['id'] for item in client.get('/api/inbox').json()] == [meta['id']]


def test_a_company_read_does_not_start_the_review_timer(monkeypatch):
    """The reviewer's clock starts when a person opens the document, not when an
    integration polls for a status — otherwise the wait is reported as review time."""
    isolate()
    token = issue(company=1)
    seen: dict = {}

    class FakeStore:
        AVAILABLE = True

        def document_meta(self, person, doc_id, **kwargs):
            seen.update(kwargs, person=person, doc_id=doc_id)
            return {
                'id': doc_id,
                'status': 'new',
                'filename': 'invoice.pdf',
                'uploaded_at': '2026-02-12T10:31:07+02:00',
                'parsed': {'fields': {'total': {'value': 1.0, 'confidence': 0.9}}},
            }

    monkeypatch.setattr(api_v1, 'pgapi', FakeStore())
    with TestClient(app.app) as client:
        answer = client.get('/api/v1/documents/12', headers={'X-API-Key': token})
    assert answer.status_code == 200
    assert seen['mark_viewed'] is False
    assert seen['person']['company_id'] == 1


def test_a_confirmed_record_names_the_key_that_approved_it():
    isolate()
    token = issue(scopes=['documents:write', 'documents:read', 'documents:confirm'])
    with TestClient(app.app) as client:
        meta = upload(client, token).json()
        client.post(
            f"/api/v1/documents/{meta['id']}/confirm", headers={'X-API-Key': token}, json={}
        )
    rows = [json.loads(line) for line in settings.CONFIRMED.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['confirmed_by'].startswith('api:')
    assert rows[0]['source'] == 'api'


def test_the_answer_is_built_before_the_model_reads_the_document(monkeypatch):
    """An integration's POST must not wait for the model.

    The answer it receives is the rules' answer, complete on its own; the model step
    is scheduled and lands in the record afterwards, through the invariants. The two
    values below are the proof: the response carries the rules' total and the stored
    record carries the model's.
    """
    isolate()
    token = issue()
    import meaning

    monkeypatch.setattr(settings, 'MEANING', 'on')
    monkeypatch.setattr(meaning, '_ask', lambda text, fields=None: {
        'fields': {'total': {'line': 3, 'text': '1 028,80'}}})
    document = (
        'Рахунок на оплату № API-18 від 12 лютого 2026\n'
        'Постачальник: ТОВ «Платформа-Тест», ЄДРПОУ 41234567\n'
        'Сума без ПДВ: 1 028,80 грн\n'
        'Всього: 1 234,56 грн\n'
    )
    with TestClient(app.app) as client:
        answer = client.post(
            '/api/v1/documents',
            headers={'Authorization': f'Bearer {token}'},
            files={'file': ('invoice.txt', document.encode('utf-8'), 'text/plain')},
        )
        body = answer.json()
        assert answer.status_code == 201
        assert body['values']['total'] == 1234.56
        stored = json.loads(
            (settings.PENDING / f"{body['id']}.json").read_text(encoding='utf-8'))
        assert stored['parsed']['fields']['total']['value'] == 1028.8
        assert stored['meaning']['state'] == 'done'
