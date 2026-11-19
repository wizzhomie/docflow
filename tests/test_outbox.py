"""The outbox: a confirmed deal always has work that sends it, and a retry is safe.

These checks drive the real confirmation, then the real handler, against the demo
database. The property under test is not "the CRM call happens" (that is a fake
here) but the bookkeeping around it: the job is committed with the deal, a second
run does not duplicate the deal, a timeout leaves `timeout` and a reason on the
row, and a job whose worker died goes back to the queue.

Run: python3 tests/test_outbox.py
Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import connectors  # noqa: E402
import crm_outbox  # noqa: E402
import jobqueue  # noqa: E402
import settings  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')
SAMPLE = Path(__file__).resolve().parents[1] / 'samples' / 'pilot_01_2026-501.pdf'

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def _person() -> dict:
    slug = f'outbox-{uuid.uuid4().hex[:8]}'
    token = uuid.uuid4().hex
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id', ('Outbox', slug)
        )
        company_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (company_id, email, full_name, role) "
            "VALUES (%s, %s, 'Outbox Test', 'owner') RETURNING id",
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
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute('SELECT file_path FROM documents WHERE company_id = %s', (company_id,))
        paths = [row[0] for row in cursor.fetchall() if row[0]]
        cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))
    for stored in paths:
        (Path(settings.UPLOADS) / Path(stored).name).unlink(missing_ok=True)


def _confirmed(person: dict) -> dict:
    """Upload and confirm one document through the real endpoints."""
    client = TestClient(app.app, cookies={'docflow_session': person['token']})
    payload = SAMPLE.read_bytes()
    meta = client.post(
        '/api/upload', files={'file': ('pilot_01.pdf', payload, 'application/pdf')}
    ).json()
    fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
    answer = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields})
    assert answer.status_code == 200, answer.text
    return {'document_id': int(meta['id']), 'answer': answer.json()}


def _deal(company_id: int, document_id: int) -> dict | None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute(
            'SELECT id, tech_status, attempts, external_id, last_error FROM deals '
            'WHERE document_id = %s',
            (document_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    keys = ('id', 'tech_status', 'attempts', 'external_id', 'last_error')
    return dict(zip(keys, row, strict=True))


def _jobs(company_id: int) -> list[dict]:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT id, company_id, kind, payload, status, attempts, max_attempts '
            'FROM jobs WHERE company_id = %s ORDER BY id',
            (company_id,),
        )
        rows = cursor.fetchall()
    keys = ('id', 'company_id', 'kind', 'payload', 'status', 'attempts', 'max_attempts')
    return [dict(zip(keys, row, strict=True)) for row in rows]


class FakeConnector:
    name = 'fake'

    def __init__(self, existing=None, error=None):
        self.existing = existing
        self.error = error
        self.created = []

    def configured(self):
        return True

    def existing_deal(self, fields):
        return self.existing

    def ensure_company(self, fields, doc_type=None):
        return {'id': 'company-1', 'title': 'Supplier'}

    def ensure_contact(self, fields, company=None):
        return {'id': 'contact-1', 'title': 'Person'}

    def create_deal(self, **kwargs):
        if self.error:
            raise self.error
        self.created.append(kwargs)
        return {'id': 'crm-42', 'title': kwargs['title']}


def test_a_confirmation_queues_the_crm_call_in_the_same_transaction():
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    try:
        result = _confirmed(person)
        jobs = _jobs(person['company_id'])
        assert len(jobs) == 1, 'a confirmation must queue exactly one send'
        assert jobs[0]['kind'] == crm_outbox.KIND
        assert jobs[0]['payload']['document_id'] == result['document_id']
        deal = _deal(person['company_id'], result['document_id'])
        assert deal['tech_status'] == 'pending'

        # Confirming twice is refused, and the refused attempt adds no work.
        client = TestClient(app.app, cookies={'docflow_session': person['token']})
        again = client.post('/api/confirm', json={'id': str(result['document_id']), 'fields': {}})
        assert again.status_code == 409
        assert len(_jobs(person['company_id'])) == 1
    finally:
        _drop(person['company_id'])


def test_the_worker_sends_the_deal_and_marks_it_created(monkeypatch):
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    try:
        result = _confirmed(person)
        job = _jobs(person['company_id'])[0]
        monkeypatch.setattr(settings, 'DEMO_MODE', True)
        crm_outbox.push(job)

        deal = _deal(person['company_id'], result['document_id'])
        assert deal['tech_status'] == 'created'
        assert deal['external_id'] == f"DEMO-DB-{deal['id']}"
        assert deal['attempts'] == 1
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(person['company_id']),))
            cursor.execute('SELECT status FROM documents WHERE id = %s', (result['document_id'],))
            assert cursor.fetchone()[0] == 'sent'
            cursor.execute(
                "SELECT count(*) FROM document_events WHERE document_id = %s "
                "AND event_type = 'deal_created'",
                (result['document_id'],),
            )
            assert cursor.fetchone()[0] == 1

        # A second run is a no-op: the deal is already there.
        crm_outbox.push(job)
        assert _deal(person['company_id'], result['document_id'])['attempts'] == 1
    finally:
        _drop(person['company_id'])


def test_a_timeout_is_recorded_and_the_job_lets_the_queue_retry(monkeypatch):
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    try:
        result = _confirmed(person)
        job = _jobs(person['company_id'])[0]
        fake = FakeConnector(error=TimeoutError('CRM did not answer'))
        monkeypatch.setattr(settings, 'DEMO_MODE', False)
        monkeypatch.setattr(connectors, 'active', lambda: fake)

        raised = False
        try:
            crm_outbox.push(job)
        except TimeoutError:
            raised = True
        assert raised, 'the handler must raise so the queue can retry'
        deal = _deal(person['company_id'], result['document_id'])
        assert deal['tech_status'] == 'timeout'
        assert deal['attempts'] == 1
        assert 'did not answer' in deal['last_error']
    finally:
        _drop(person['company_id'])


def test_a_retry_adopts_the_deal_the_crm_already_has(monkeypatch):
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    try:
        result = _confirmed(person)
        job = _jobs(person['company_id'])[0]
        fake = FakeConnector(existing={'id': 'crm-old', 'title': 'That invoice'})
        monkeypatch.setattr(settings, 'DEMO_MODE', False)
        monkeypatch.setattr(connectors, 'active', lambda: fake)

        crm_outbox.push(job)
        deal = _deal(person['company_id'], result['document_id'])
        assert deal['tech_status'] == 'created'
        assert deal['external_id'] == 'crm-old'
        assert fake.created == [], 'an existing deal must not be created twice'
    finally:
        _drop(person['company_id'])


def test_a_job_whose_worker_died_goes_back_to_the_queue():
    if not POSTGRES_AVAILABLE:
        return
    person = _person()
    try:
        _confirmed(person)
        job = _jobs(person['company_id'])[0]
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET status = 'running', locked_by = 'dead-worker', "
                "locked_at = now() - interval '2 hours' WHERE id = %s",
                (job['id'],),
            )
        assert jobqueue.reap_stale() >= 1
        assert _jobs(person['company_id'])[0]['status'] == 'queued'
    finally:
        _drop(person['company_id'])
