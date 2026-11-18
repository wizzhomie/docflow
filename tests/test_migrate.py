"""Checks for the migration runner.

Two things matter here and neither is the happy path: a `pg_dump` file carries psql
meta-commands that SQL does not have, and a migration must never be applied twice.
The first is checked without a database; the second against one, when there is one.

Run: python3 tests/test_migrate.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import migrate  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def test_migrations_are_ordered_by_name():
    names = [path.name for path in migrate.migrations()]
    assert names, 'no migrations found'
    assert names == sorted(names)
    assert names[0].startswith('001')


def test_psql_meta_commands_do_not_reach_the_driver():
    sql = '\\restrict abc\nCREATE TABLE t (id int);\n\\unrestrict abc\n'
    cleaned = migrate.without_meta_commands(sql)
    assert '\\restrict' not in cleaned and '\\unrestrict' not in cleaned
    assert 'CREATE TABLE t' in cleaned


def test_the_real_dump_is_clean_after_cleaning():
    first = migrate.migrations()[0].read_text(encoding='utf-8')
    cleaned = migrate.without_meta_commands(first)
    assert '\\restrict' not in cleaned
    assert 'CREATE TABLE' in cleaned


def test_nothing_is_pending_once_the_database_is_migrated():
    if not POSTGRES_AVAILABLE:
        return
    with migrate.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(migrate.BOOKKEEPING)
        done = migrate.applied(connection)
        todo = migrate.pending(connection)
    assert not todo, f'pending migrations: {[path.name for path in todo]}'
    assert '006_jobs.sql' in done


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('migration checks passed')


if __name__ == '__main__':
    main()
