"""Checks that one company cannot see or write another company's rows.

The isolation is not claimed in prose: these checks create a throwaway company,
work as it, and then try everything a leaked query would do — read the other
company's documents, write a row stamped with someone else's id, reuse a tax ID
that belongs elsewhere. Each attempt must be refused by the database, not by a
`WHERE` clause somebody remembered to write.

Run: python3 tests/test_tenancy.py
Skipped when PostgreSQL is not reachable, so the deterministic suite stays
runnable on a machine without a database.
"""

import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')
DEMO_SLUG = 'demo'


def connect():
    return psycopg2.connect(DSN)


try:
    with connect():
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


@contextmanager
def as_company(company_id: int):
    """A connection that has told the database which tenant it serves."""
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, false)", (str(company_id),))
        yield connection
    finally:
        connection.close()


def _company_id(connection, slug: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute('SELECT id FROM companies WHERE slug = %s', (slug,))
        row = cursor.fetchone()
    assert row is not None, f'company {slug} is missing'
    return row[0]


def _make_company(name: str) -> tuple[int, str]:
    slug = f'test-{uuid.uuid4().hex[:8]}'
    connection = connect()
    try:
        with connection:  # commits on exit
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id', (name, slug))
                company_id = cursor.fetchone()[0]
    finally:
        connection.close()
    return company_id, slug


def _drop_company(company_id: int) -> None:
    connection = connect()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))
    finally:
        connection.close()


def test_the_first_company_sees_its_own_documents():
    with connect() as connection:
        demo = _company_id(connection, DEMO_SLUG)
    with as_company(demo) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM documents')
        own = cursor.fetchone()[0]
    assert own > 0, 'the demo company has no documents, the fixture looks empty'


def test_a_new_company_starts_empty():
    company_id, _ = _make_company('Isolation test')
    try:
        with as_company(company_id) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM suppliers')
            assert cursor.fetchone()[0] == 0
    finally:
        _drop_company(company_id)


def test_a_row_of_one_company_is_invisible_to_the_other():
    company_id, _ = _make_company('Own row')
    try:
        with as_company(company_id) as connection:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO suppliers (company_id, name) VALUES (%s, %s) RETURNING id',
                        (company_id, 'Only Mine LLC'),
                    )
                    supplier_id = cursor.fetchone()[0]
            with connection.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM suppliers WHERE name = %s', ('Only Mine LLC',))
                assert cursor.fetchone()[0] == 1, 'the owner cannot read its own row'

        with connect() as connection:
            demo = _company_id(connection, DEMO_SLUG)
        with as_company(demo) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM suppliers WHERE id = %s', (supplier_id,))
            assert cursor.fetchone()[0] == 0, 'another company can read a foreign supplier'
    finally:
        _drop_company(company_id)


def test_a_row_stamped_with_a_foreign_company_is_refused():
    company_id, _ = _make_company('Trespass')
    try:
        with connect() as connection:
            demo = _company_id(connection, DEMO_SLUG)
        refused = False
        with as_company(company_id) as connection:
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO documents (company_id, doc_type) VALUES (%s, 'invoice_ua')",
                            (demo,),
                        )
            except psycopg2.Error:
                refused = True
        assert refused, 'a company managed to write a document into another company'
    finally:
        _drop_company(company_id)


def test_tax_ids_are_unique_per_company_not_globally():
    first_id, _ = _make_company('First tax id')
    second_id, _ = _make_company('Second tax id')
    tax_id = f'TEST{uuid.uuid4().hex[:8]}'
    try:
        for company_id in (first_id, second_id):
            with as_company(company_id) as connection:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'INSERT INTO suppliers (company_id, name, tax_id) VALUES (%s, %s, %s)',
                            (company_id, 'Same Legal Entity LLC', tax_id),
                        )
        refused = False
        with as_company(first_id) as connection:
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'INSERT INTO suppliers (company_id, name, tax_id) VALUES (%s, %s, %s)',
                            (first_id, 'Duplicate inside one company', tax_id),
                        )
            except psycopg2.Error:
                refused = True
        assert refused, 'the same tax ID was accepted twice inside one company'
    finally:
        _drop_company(first_id)
        _drop_company(second_id)


def test_the_policy_is_forced_for_the_owner_too():
    """Without FORCE the table owner silently bypasses every policy."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM pg_class
            WHERE relname IN ('documents', 'suppliers', 'deals')
              AND relrowsecurity AND relforcerowsecurity
            """
        )
        assert cursor.fetchone()[0] == 3


def main():
    if not POSTGRES_AVAILABLE:
        print('postgres is not reachable, tenancy checks skipped')
        return
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('tenancy checks passed')


if __name__ == '__main__':
    main()
