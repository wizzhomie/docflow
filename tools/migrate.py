"""Apply the SQL files in `sql/` once each, and remember which ones were applied.

The server used to be updated by running `psql` over the files by hand. That works
until two people do it, or until the third file is re-run: the second run stops
halfway with "column already exists", and the state of the schema becomes a
question someone has to answer by looking at `\\d` and hoping.

With `schema_migrations` the state is a fact: a file is applied, or it is not, and
the same command moves any database — the demo on the server, the one in CI, a
fresh one on a laptop — to the same version. Each file runs in one transaction
with its own version row, so a failed migration leaves neither the schema nor the
bookkeeping half-done.

Usage:
    python3 tools/migrate.py             # apply what is missing
    python3 tools/migrate.py --status    # what is applied, what is not
    python3 tools/migrate.py --dry-run   # what would be applied
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / 'sql'
DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

# Fully qualified on purpose: `001_schema.sql` is a pg_dump and leaves the session
# with an empty `search_path`, which is correct for a dump and fatal for the next
# unqualified name.
BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

# A dump resets `search_path` and `row_security` for the session; both must be
# restored after each file, or the next migration runs with an empty search path
# and with the policies switched off — which is how a data migration silently
# updates nothing.
RESET_SESSION = (
    "SELECT set_config('search_path', 'public', false), "
    "set_config('row_security', 'on', false)"
)


def migrations(directory: Path = SQL_DIR) -> list[Path]:
    """Every migration, in the order their names sort: 001, 002, ..."""
    return sorted(path for path in directory.glob('*.sql') if path.is_file())


def without_meta_commands(sql: str) -> str:
    """Drop psql meta-commands, which are not SQL.

    `pg_dump` writes `\\restrict` / `\\unrestrict` guards around a dump, and psql
    understands them; the driver does not. Everything else in these files is
    plain SQL.
    """
    return '\n'.join(
        line for line in sql.splitlines() if not line.lstrip().startswith('\\')
    )


def applied(connection) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute('SELECT version, applied_at::text FROM public.schema_migrations')
        return {version: when for version, when in cursor.fetchall()}


def pending(connection, directory: Path = SQL_DIR) -> list[Path]:
    done = applied(connection)
    return [path for path in migrations(directory) if path.name not in done]


def apply_one(connection, path: Path) -> None:
    """One file, one transaction, one version row.

    The dump resets `search_path`, so it is restored right after the file runs:
    otherwise the version row, and every migration after this one, would be
    written into a session that cannot see the schema it just created.
    """
    sql = without_meta_commands(path.read_text(encoding='utf-8'))
    with connection.cursor() as cursor:
        cursor.execute(sql)
        cursor.execute(RESET_SESSION)
        cursor.execute(
            'INSERT INTO public.schema_migrations (version) VALUES (%s)', (path.name,)
        )


def connect():
    return psycopg2.connect(DSN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Apply the SQL migrations once each.')
    parser.add_argument('--status', action='store_true', help='show applied and pending, change nothing')
    parser.add_argument('--dry-run', action='store_true', help='show what would be applied')
    parser.add_argument(
        '--baseline',
        metavar='VERSION',
        help='record every file up to and including VERSION as applied, without running it '
        '(for a database that was migrated by hand before this tool existed)',
    )
    args = parser.parse_args(argv)

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(BOOKKEEPING)
        done = applied(connection)
        if args.baseline:
            names = [path.name for path in migrations()]
            if args.baseline not in names:
                print(f'no such migration: {args.baseline}', file=sys.stderr)
                return 1
            upto = names[: names.index(args.baseline) + 1]
            with connection.cursor() as cursor:
                for name in upto:
                    cursor.execute(
                        'INSERT INTO public.schema_migrations (version) VALUES (%s) '
                        'ON CONFLICT (version) DO NOTHING',
                        (name,),
                    )
            connection.commit()
            print(f'baselined {len(upto)} migration(s)')
            done = applied(connection)
        todo = pending(connection)
        if args.status or args.dry_run:
            for path in migrations():
                mark = 'applied' if path.name in done else 'pending'
                print(f'{mark:8} {path.name}')
            return 0
        if not todo:
            print('nothing to apply')
            return 0
        for path in todo:
            print(f'applying {path.name}')
            try:
                apply_one(connection, path)
                connection.commit()
            except Exception as error:  # noqa: BLE001 - the message is the report
                connection.rollback()
                print(f'failed on {path.name}: {error}', file=sys.stderr)
                return 1
    print(f'applied {len(todo)} migration(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
