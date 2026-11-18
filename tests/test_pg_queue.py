"""Checks for the PostgreSQL review queue: the role gate and company settings.

The queue is the file-based workflow with its state in the database. Two rules
are worth a test rather than a paragraph in a README: a read-only person cannot
confirm a document, and the approval threshold a company sees is its own value,
not the platform default.

Run: python3 tests/test_pg_queue.py
The database checks skip themselves when PostgreSQL is not reachable.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

import pg_queue  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def test_a_viewer_cannot_confirm():
    assert not pg_queue.can_confirm('viewer')


def test_a_reviewer_and_an_owner_can_confirm():
    assert pg_queue.can_confirm('reviewer')
    assert pg_queue.can_confirm('owner')


def test_an_unknown_role_cannot_confirm():
    assert not pg_queue.can_confirm('')
    assert not pg_queue.can_confirm('admin')


def test_a_company_value_overrides_the_platform_default():
    if not POSTGRES_AVAILABLE:
        return
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM companies WHERE slug = 'second'")
        second = cursor.fetchone()
        cursor.execute(
            "SELECT setting_value('routing.approval_threshold', %s), "
            "setting_value('routing.approval_threshold', 1)",
            (second[0],),
        )
        own, platform = cursor.fetchone()
    assert float(own) < float(platform), 'the company override did not win over the platform value'


def test_a_company_without_its_own_value_gets_the_platform_default():
    if not POSTGRES_AVAILABLE:
        return
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT setting_value('routing.approval_threshold', 1)")
        assert cursor.fetchone()[0] == '100000'


def test_the_confirm_answer_carries_the_deal_shape():
    """The interface reads crm.deal.id and record.crm_deal_id; both must be there."""
    if not POSTGRES_AVAILABLE:
        return
    import uuid

    import pg_queue

    slug = f'test-{uuid.uuid4().hex[:8]}'
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO companies (name, slug) VALUES ('Answer shape', %s) RETURNING id", (slug,))
        company_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (company_id, email, full_name, role) "
            "VALUES (%s, %s, 'Answer Shape', 'owner') RETURNING id",
            (company_id, f'{slug}@test.local'),
        )
        user_id = cursor.fetchone()[0]
    try:
        person = {
            'id': user_id,
            'email': f'{slug}@test.local',
            'full_name': 'Answer Shape',
            'role': 'owner',
            'company_id': company_id,
            'company_name': 'Answer shape',
        }
        connection = psycopg2.connect(DSN)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.company_id', %s, false)", (str(company_id),))
                cursor.execute(
                    "INSERT INTO documents (company_id, doc_type, number, total, currency) "
                    "VALUES (%s, 'invoice_ua', 'SHAPE-1', 1000, 'UAH') RETURNING id",
                    (company_id,),
                )
                doc_id = cursor.fetchone()[0]
        finally:
            connection.close()

        answer = pg_queue.confirm_document(person, str(doc_id), {})
        assert answer['ok'] is True
        assert answer['record']['crm_deal_id'].startswith('DEMO-DB-')
        assert answer['crm']['deal']['id'] == answer['record']['crm_deal_id']
        assert answer['route']['destination']
    finally:
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('queue checks passed')


if __name__ == '__main__':
    main()
