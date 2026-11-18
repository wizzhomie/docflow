"""Checks for the connection pool and the tenant scope it carries.

The point of a pool is that connections are reused, and the danger of a pool is
exactly that: a connection that still remembers the previous request's company
would answer the next request with another tenant's rows, and the row-level
policies would allow it because they were told the wrong company.

These checks run on a pool of one connection on purpose: with a single
connection, every request is the "next request", so a tenant that survives the
transaction is caught immediately instead of once a month in production.

Run: python3 tests/test_db.py
Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

import db  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def _pool_of(size: int) -> None:
    """Rebuild the pool with a different size, keeping the process honest."""
    db.close_pool()
    db.POOL_MIN = 1
    db.POOL_MAX = size


def _make_company(name: str) -> int:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id',
            (name, f'pool-{uuid.uuid4().hex[:8]}'),
        )
        return cursor.fetchone()[0]


def _drop_company(company_id: int) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def _add_document(company_id: int, number: str) -> int:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute(
            "INSERT INTO documents (company_id, doc_type, number) "
            "VALUES (%s, 'invoice_ua', %s) RETURNING id",
            (company_id, number),
        )
        return cursor.fetchone()[0]


def _numbers_seen() -> list[str]:
    """What a transaction sees when nobody told it which company it serves."""
    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT number FROM documents ORDER BY number')
        return [row['number'] for row in cursor.fetchall()]


def test_a_tenant_does_not_outlive_its_transaction():
    if not POSTGRES_AVAILABLE:
        return
    company = _make_company('Pool scope')
    try:
        _add_document(company, f'SCOPE-{company}')
        _pool_of(1)
        with db.tenant(company) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 1, 'the company cannot read its own document'
        # Same single pooled connection, a new transaction, nobody set a tenant:
        # with a session-scoped company id this would return the row above.
        assert _numbers_seen() == [], 'the tenant leaked into the next request'
    finally:
        _drop_company(company)
        db.close_pool()


def test_two_tenants_on_one_connection_do_not_share_rows():
    if not POSTGRES_AVAILABLE:
        return
    first = _make_company('Pool first')
    second = _make_company('Pool second')
    try:
        _add_document(first, f'FIRST-{first}')
        _add_document(second, f'SECOND-{second}')
        _pool_of(1)
        with db.tenant(first) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT number FROM documents')
            seen_first = [row[0] for row in cursor.fetchall()]
        with db.tenant(second) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT number FROM documents')
            seen_second = [row[0] for row in cursor.fetchall()]
        assert seen_first == [f'FIRST-{first}']
        assert seen_second == [f'SECOND-{second}']
    finally:
        _drop_company(first)
        _drop_company(second)
        db.close_pool()


def test_the_connection_goes_back_to_the_pool():
    if not POSTGRES_AVAILABLE:
        return
    _pool_of(1)
    try:
        with db.tenant(1) as connection:
            assert db.status()['in_use'] == 1
            assert connection is not None
        assert db.status()['in_use'] == 0, 'a connection was not returned'
        assert db.status()['idle'] >= 1
    finally:
        db.close_pool()


def test_a_failed_transaction_leaves_nothing_behind():
    if not POSTGRES_AVAILABLE:
        return
    company = _make_company('Pool rollback')
    try:
        _pool_of(1)
        try:
            with db.tenant(company) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents (company_id, doc_type, number) VALUES (%s, %s, %s)",
                    (company, 'invoice_ua', f'ROLLBACK-{company}'),
                )
                raise RuntimeError('a route failed halfway')
        except RuntimeError:
            pass
        with db.tenant(company) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0, 'the failed transaction was committed'
    finally:
        _drop_company(company)
        db.close_pool()


def main():
    if not POSTGRES_AVAILABLE:
        print('skipped: PostgreSQL is not reachable')
        return
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('pool checks passed')


if __name__ == '__main__':
    main()
