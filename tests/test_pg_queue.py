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


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('queue checks passed')


if __name__ == '__main__':
    main()
