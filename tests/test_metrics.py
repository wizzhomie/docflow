"""Checks that the per-company counters agree with the documents they count.

The counters exist so the metrics endpoint does not have to open one transaction
per company. That only works if a number in `company_stats` is never a stale guess:
these cases move a document through its life (new → confirmed → deleted → company
removed) and read the counters after every step.

Run: python3 tests/test_metrics.py
Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

import metrics  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def _company(name: str = 'Metrics test') -> tuple[int, str]:
    slug = f'metrics-{uuid.uuid4().hex[:8]}'
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id',
            (name, slug),
        )
        return cursor.fetchone()[0], name


def _drop(company_id: int) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def _document(company_id: int, status: str = 'new') -> int:
    """A document inserted the way the application inserts one: inside its tenant."""
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute(
            "INSERT INTO documents (company_id, doc_type, status) "
            "VALUES (%s, 'invoice_ua', %s) RETURNING id",
            (company_id, status),
        )
        return cursor.fetchone()[0]


def _set_status(company_id: int, doc_id: int, status: str) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute('UPDATE documents SET status = %s WHERE id = %s', (status, doc_id))


def _counts(company: str) -> dict:
    """What the endpoint says about one company, by name."""
    for row in metrics.tenants():
        if row['company'] == company:
            return row
    return {'company': company, 'documents': 0, 'waiting': 0}


def test_a_new_document_counts_as_waiting():
    if not POSTGRES_AVAILABLE:
        return
    company_id, company = _company()
    try:
        _document(company_id)
        row = _counts(company)
        assert row['documents'] == 1
        assert row['waiting'] == 1
    finally:
        _drop(company_id)


def test_confirming_moves_the_document_out_of_the_queue():
    if not POSTGRES_AVAILABLE:
        return
    company_id, company = _company()
    try:
        doc_id = _document(company_id)
        _set_status(company_id, doc_id, 'confirmed')
        row = _counts(company)
        assert row['documents'] == 1, 'a confirmed document is still a document'
        assert row['waiting'] == 0, 'a confirmed document must leave the queue'
    finally:
        _drop(company_id)


def test_deleting_a_document_decrements_both_numbers():
    if not POSTGRES_AVAILABLE:
        return
    company_id, company = _company()
    try:
        doc_id = _document(company_id)
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
            cursor.execute('DELETE FROM documents WHERE id = %s', (doc_id,))
        row = _counts(company)
        assert row['documents'] == 0
        assert row['waiting'] == 0
    finally:
        _drop(company_id)


def test_one_companys_documents_do_not_touch_another_counter():
    if not POSTGRES_AVAILABLE:
        return
    first_id, first = _company('Metrics first')
    second_id, second = _company('Metrics second')
    try:
        _document(first_id)
        _document(second_id)
        _document(second_id)
        assert _counts(first)['documents'] == 1
        assert _counts(second)['documents'] == 2
    finally:
        _drop(first_id)
        _drop(second_id)


def test_a_company_without_documents_reports_zero():
    if not POSTGRES_AVAILABLE:
        return
    company_id, company = _company('Metrics empty')
    try:
        row = _counts(company)
        assert row['documents'] == 0
        assert row['waiting'] == 0
    finally:
        _drop(company_id)


def main():
    if not POSTGRES_AVAILABLE:
        print('skipped: PostgreSQL is not reachable')
        return
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('metrics checks passed')


if __name__ == '__main__':
    main()
