"""Give the demo history back its two halves.

A document has two snapshots: what the parser read (`parsed`) and what the person
approved (`confirmed_fields`). The demo history was loaded before the second one
existed, so for the imported documents the approved values sit in `parsed` and
`confirmed_fields` is empty — the history looks as if nobody ever changed a field,
and the parser's own values are gone.

This tool rebuilds the pair from `data/confirmed.jsonl`, which kept both:
`parsed` is the machine snapshot and `fields` is the approved one. It only writes
rows whose `confirmed_fields` is still empty, so running it twice changes nothing.

    python3 tools/backfill_confirmed_fields.py            # report only
    python3 tools/backfill_confirmed_fields.py --apply    # write

A row whose approved snapshot is genuinely unknown (it was confirmed by hand when
the application did not record one) is reported as unresolved and left alone: an
invented approval is worse than a missing one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')
HISTORY = ROOT / 'data' / 'confirmed.jsonl'


def _flat(value: Any) -> Any:
    """A field value, whether it is the parser's entry or the flat snapshot."""
    return value.get('value') if isinstance(value, dict) else value


def _flat_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    return {key: _flat(entry) for key, entry in (parsed.get('fields') or {}).items()}


def load_history(path: Path = HISTORY) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _index(records: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Records by (number, date); the seed contains a deliberate duplicate."""
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        fields = record.get('fields') or {}
        number = str(fields.get('number') or '')
        if not number:
            continue
        index.setdefault((number, str(fields.get('date') or '')), []).append(record)
    return index


def _match(
    index: dict[tuple[str, str], list[dict[str, Any]]],
    row: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any] | None:
    """The record that produced this row, if one can be identified.

    Number and date are not always enough: the seed has a duplicated invoice
    `2026-448` with two different approved snapshots, so the stored values decide
    between them. A row that matches none of the candidates is not guessed.
    """
    if not str(row.get('file_path') or '').startswith('data/history/'):
        return None
    candidates = index.get((str(row.get('number') or ''), str(row.get('doc_date') or '')), [])
    exact = [
        record
        for record in candidates
        if _flat_fields({'fields': record.get('fields') or {}}) == current
    ]
    if exact:
        # The seed has a duplicated invoice whose two copies carry the same
        # snapshots: either record rebuilds the same row, so the pick is stable.
        if all(record.get('parsed') == exact[0].get('parsed') for record in exact):
            return exact[0]
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def plan(
    rows: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """What would be written, per row, without touching the database.

    Three outcomes: `backfill` (both snapshots rebuild from the history file or
    from the row itself when there were no edits), `unresolved` (approved values
    were lost, the row is left alone) and `already` (nothing to do).
    """
    index = _index(records)
    actions = []
    for row in rows:
        if row.get('confirmed_fields'):
            actions.append({'row': row, 'action': 'already'})
            continue
        parsed = row.get('parsed') or {}
        current = _flat_fields(parsed)
        record = _match(index, row, current)
        if record is not None:
            actions.append(
                {
                    'row': row,
                    'action': 'backfill',
                    'approved': {key: _flat(value) for key, value in (record.get('fields') or {}).items()},
                    'machine': {key: _flat(value) for key, value in (record.get('parsed') or {}).items()},
                    'edited': list(record.get('edited_fields') or []),
                    'source': 'history',
                }
            )
        elif not row.get('edited_fields'):
            # Confirmed with no edits: the parser's values are what was approved.
            actions.append(
                {
                    'row': row,
                    'action': 'backfill',
                    'approved': current,
                    'machine': None,
                    'edited': [],
                    'source': 'no-edits',
                }
            )
        else:
            actions.append({'row': row, 'action': 'unresolved'})
    return actions


def companies(connection) -> list[dict[str, Any]]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT id, slug FROM companies ORDER BY id')
        return [dict(row) for row in cursor.fetchall()]


def _tenant(cursor, company_id: int) -> None:
    """Row-level security is forced: the transaction has to name its company."""
    cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))


def fetch_rows(connection, company_id: int) -> list[dict[str, Any]]:
    """Documents of one company, oldest first, inside that company's transaction."""
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        _tenant(cursor, company_id)
        cursor.execute(
            """
            SELECT id, number, doc_date, file_path, parsed, confirmed_fields, edited_fields
              FROM documents
             ORDER BY id
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def apply_actions(connection, company_id: int, actions: list[dict[str, Any]]) -> int:
    """Write the plan; a row already filled by someone else stays untouched."""
    written = 0
    with connection.cursor() as cursor:
        _tenant(cursor, company_id)
        for action in actions:
            if action['action'] != 'backfill':
                continue
            row = action['row']
            parsed = dict(row.get('parsed') or {})
            if action['machine'] is not None:
                parsed['fields'] = action['machine']
            cursor.execute(
                """
                UPDATE documents
                   SET confirmed_fields = %s,
                       parsed = %s,
                       edited_fields = %s
                 WHERE id = %s
                   AND confirmed_fields IS NULL
                """,
                (
                    Json(action['approved']),
                    Json(parsed),
                    action['edited'] or None,
                    row['id'],
                ),
            )
            written += cursor.rowcount
    connection.commit()
    return written


def report(actions: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action['action']] = counts.get(action['action'], 0) + 1
    for action in actions:
        row = action['row']
        label = f"{row['id']:>3} {row.get('number') or '—':<9} {row.get('doc_date') or ''}"
        if action['action'] == 'backfill':
            edited = ','.join(action['edited']) or '—'
            print(f'{label}  backfill from {action["source"]:<8} edits: {edited}')
        elif action['action'] == 'unresolved':
            print(f'{label}  unresolved: edits {row.get("edited_fields")} have no snapshot')
        else:
            print(f'{label}  already has confirmed_fields')
    print(
        'plan: '
        + ', '.join(f'{name} {count}' for name, count in sorted(counts.items()))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--apply', action='store_true', help='write the plan (default: report only)')
    parser.add_argument('--dsn', default=DSN, help='database to connect to')
    args = parser.parse_args(argv)

    records = load_history()
    if not records:
        print(f'no history file at {HISTORY}', file=sys.stderr)
        return 1

    with psycopg2.connect(args.dsn) as connection:
        for company in companies(connection):
            print(f'== company {company["id"]} ({company["slug"]})')
            actions = plan(fetch_rows(connection, company['id']), records)
            report(actions)
            unresolved = [action for action in actions if action['action'] == 'unresolved']
            if unresolved:
                print(f'{len(unresolved)} row(s) left alone: the approved values are not recorded')
            if args.apply:
                print(f'written: {apply_actions(connection, company["id"], actions)}')
            connection.commit()
    if not args.apply:
        print('report only; pass --apply to write')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
