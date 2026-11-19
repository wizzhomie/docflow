"""Checks that a document lives in one place from upload to history.

The debt this closes: an upload used to write a JSON snapshot and a history line,
the queue row appeared only after an import, and the two could disagree. These
checks drive the real endpoints with a real session and then look at the database
directly: the row exists after the upload, the confirm finishes it, the history and
the reports read it back, and another company sees none of it.

Run: python3 tests/test_lifecycle.py
Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import docstore  # noqa: E402
import pg_documents  # noqa: E402
import settings  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')
SAMPLE = Path(__file__).resolve().parents[1] / 'samples' / 'pilot_01_2026-501.pdf'

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def _person() -> dict:
    """A company, a person in it and a session token: what a signed-in request has."""
    slug = f'life-{uuid.uuid4().hex[:8]}'
    token = auth.token_urlsafe(32) if hasattr(auth, 'token_urlsafe') else uuid.uuid4().hex
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id', ('Life', slug)
        )
        company_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (company_id, email, full_name, role) "
            "VALUES (%s, %s, 'Life Test', 'owner') RETURNING id",
            (company_id, f'{slug}@test.local'),
        )
        user_id = cursor.fetchone()[0]
        cursor.execute(
            'INSERT INTO sessions (user_id, token_hash, expires_at) '
            "VALUES (%s, %s, now() + interval '1 hour')",
            (user_id, auth.token_hash(token)),
        )
    return {'company_id': company_id, 'user_id': user_id, 'token': token, 'slug': slug}


def _drop(company_id: int) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def _cleanup_files(company_id: int) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute('SELECT file_path FROM documents WHERE company_id = %s', (company_id,))
        paths = [row[0] for row in cursor.fetchall() if row[0]]
    for stored in paths:
        (Path(settings.UPLOADS) / Path(stored).name).unlink(missing_ok=True)


def _upload(client: TestClient) -> dict:
    payload = SAMPLE.read_bytes()
    response = client.post(
        '/api/upload', files={'file': ('pilot_01.pdf', payload, 'application/pdf')}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(company_id: int, document_id: str) -> dict | None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute(
            'SELECT id, doc_type, number, status, parsed, filename, source, file_path, '
            'confirmed_at, edited_fields FROM documents WHERE id = %s',
            (int(document_id),),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    keys = ('id', 'doc_type', 'number', 'status', 'parsed', 'filename', 'source', 'file_path',
            'confirmed_at', 'edited_fields')
    return dict(zip(keys, row, strict=True))


def test_an_upload_becomes_a_row_and_the_confirm_finishes_it():
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    try:
        meta = _upload(client)
        document_id = meta['id']
        assert document_id.isdigit(), f'an upload must return the row id, got {document_id!r}'

        row = _row(person['company_id'], document_id)
        assert row is not None, 'the upload did not create a document row'
        assert row['status'] == 'new'
        assert row['filename'] == 'pilot_01.pdf'
        assert row['source'] == 'upload'
        assert row['number'], 'the number was not stored with the row'
        assert row['parsed']['fields']['total']['value'] > 0

        queue = client.get('/api/inbox').json()
        assert any(item['id'] == document_id for item in queue), 'the queue does not read the row'

        preview = client.get(f'/files/{document_id}')
        assert preview.status_code == 200
        assert preview.content.startswith(b'%PDF')

        # Opening the document in the workspace starts the review session: the
        # reported review time is the session, not the wait between upload and
        # confirmation.
        opened = client.get(f'/api/documents/{document_id}')
        assert opened.status_code == 200
        assert opened.json()['id'] == document_id

        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        answer = client.post(
            '/api/confirm', json={'id': document_id, 'fields': {**fields, 'number': '2026-999'}}
        )
        assert answer.status_code == 200, answer.text
        confirmed = _row(person['company_id'], document_id)
        assert confirmed['status'] == 'confirmed'
        assert confirmed['confirmed_at'] is not None
        assert confirmed['edited_fields'] == ['number']

        history = client.get('/api/documents').json()
        assert any(item['id'] == document_id for item in history), 'history does not read the row'
        record = next(item for item in history if item['id'] == document_id)
        assert record['uploaded_at'] and record['confirmed_at']
        assert record['crm_deal_id'], 'a confirmed document must carry its deal'
        assert isinstance(record['review_seconds'], (int, float)), 'the session was not measured'
        assert impact_review_count(client) == 1

        impact = client.get('/api/impact').json()
        stats = client.get('/api/stats').json()
        assert impact['documents'] == 1
        assert stats['accuracy'] == impact['field_accuracy']
        assert stats['documents'] == 1

        exported = client.get('/api/export.csv').text
        assert '2026-999' in exported
    finally:
        _cleanup_files(person['company_id'])
        _drop(person['company_id'])


def impact_review_count(client: TestClient) -> int:
    return client.get('/api/impact').json()['review']['measured_documents']


def test_a_document_never_opened_has_no_measured_review_time():
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    try:
        meta = _upload(client)
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        # Confirmed straight from the queue form, without opening the panel.
        answer = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields})
        assert answer.status_code == 200, answer.text
        history = client.get('/api/documents').json()
        record = next(item for item in history if item['id'] == meta['id'])
        assert record['review_seconds'] is None, 'the wait was counted as review time'
        assert impact_review_count(client) == 0
    finally:
        _cleanup_files(person['company_id'])
        _drop(person['company_id'])


def test_the_confirm_form_needs_its_token():
    if not POSTGRES_AVAILABLE:
        return
    import re

    person = _person()
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    try:
        meta = _upload(client)
        page = client.get(f"/pg/queue/{meta['id']}")
        assert page.status_code == 200
        assert 'name="csrf"' in page.text

        refused = client.post(
            f"/pg/queue/{meta['id']}/confirm",
            data={'number': '2026-777'},
            follow_redirects=False,
        )
        assert refused.status_code == 303
        assert 'invalid=' in refused.headers['location']
        assert _row(person['company_id'], meta['id'])['status'] == 'new'

        token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        accepted = client.post(
            f"/pg/queue/{meta['id']}/confirm",
            data={'number': '2026-777', 'csrf': token},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert 'ok=1' in accepted.headers['location']
        assert _row(person['company_id'], meta['id'])['status'] == 'confirmed'
    finally:
        _cleanup_files(person['company_id'])
        _drop(person['company_id'])


def test_the_same_invoice_uploaded_twice_is_one_document():
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    try:
        first = _upload(client)
        second = _upload(client)
        assert first['id'] == second['id'], 'the unique index was supposed to decide this'
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(person['company_id']),))
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 1
    finally:
        _cleanup_files(person['company_id'])
        _drop(person['company_id'])


def test_signed_out_requests_read_the_file_demo_and_never_a_row():
    """The database is reachable only through a session; anonymous traffic stays on files."""
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    anonymous = TestClient(app.app)
    try:
        document_id = _upload(client)['id']
        assert any(item['id'] == document_id for item in client.get('/api/inbox').json())
        assert document_id not in [item['id'] for item in anonymous.get('/api/inbox').json()]
        assert document_id not in [item['id'] for item in anonymous.get('/api/documents').json()]
        assert anonymous.get(f'/api/documents/{document_id}').status_code == 404
    finally:
        _cleanup_files(person['company_id'])
        _drop(person['company_id'])


def test_another_company_neither_sees_nor_opens_the_document():
    if not POSTGRES_AVAILABLE:
        return
    owner = _person()
    stranger = _person()
    owner_client = TestClient(app.app, cookies={'docflow_session': owner['token']})
    stranger_client = TestClient(app.app, cookies={'docflow_session': stranger['token']})
    try:
        document_id = _upload(owner_client)['id']

        assert stranger_client.get(f'/files/{document_id}').status_code == 404
        assert stranger_client.get('/api/documents').json() == []
        assert stranger_client.get('/api/impact').json()['documents'] == 0
        assert stranger_client.get('/api/inbox').json() == []

        refused = stranger_client.post(
            '/api/confirm', json={'id': document_id, 'fields': {}}
        )
        assert refused.status_code == 404
    finally:
        _cleanup_files(owner['company_id'])
        _drop(owner['company_id'])
        _drop(stranger['company_id'])


def test_a_history_row_shows_the_deal_id_that_was_recorded():
    base = {
        'id': 42,
        'doc_type': 'invoice_ua',
        'received_at': datetime(2026, 11, 1, tzinfo=UTC),
        'parsed': {},
        'confirmed_fields': {},
        'edited_fields': None,
        'confirmed_at': None,
        'viewed_at': None,
        'source': 'synthetic',
        'filename': 'invoice.pdf',
        'deal_id': 7,
        'deal_external_id': 'DEMO-00',
    }
    assert pg_documents._record(base)['crm_deal_id'] == 'DEMO-00'
    base['deal_external_id'] = None
    assert pg_documents._record(base)['crm_deal_id'] == 'DEMO-DB-7'


def test_without_a_session_the_document_stays_in_the_file_store():
    if not POSTGRES_AVAILABLE:
        return
    client = TestClient(app.app)
    meta = _upload(client)
    try:
        # The distinction is not the shape of the id (a hex id can be all
        # digits): a signed-out upload writes the file store, a signed-in one
        # writes a row and no snapshot.
        assert docstore._meta_path(meta['id']).exists(), 'the file store lost its snapshot'
    finally:
        docstore._meta_path(meta['id']).unlink(missing_ok=True)
        for suffix in settings.ALLOWED:
            (Path(settings.UPLOADS) / f"{meta['id']}{suffix}").unlink(missing_ok=True)


def main():
    if not POSTGRES_AVAILABLE:
        print('skipped: PostgreSQL is not reachable')
        return
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('lifecycle checks passed')


if __name__ == '__main__':
    main()
