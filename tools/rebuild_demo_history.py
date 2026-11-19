"""Rebuild the demo company's documents from the history file.

The public pages read the file history (`data/confirmed.jsonl`); a signed-in
person reads the database. When the database holds an import of that file plus
whatever was uploaded during a walkthrough, the two views drift: the same demo
shows nine edited fields in one place and eight in the other, and review time
that exists in the file is missing in the database because `viewed_at` was never
set.

This tool makes the database a faithful copy of the file for one company:
every record becomes one confirmed document with the parser's snapshot, the
approved snapshot, the edited fields and `viewed_at = confirmed_at -
review_seconds`. The company's documents are replaced, deals and events included,
so the file remains the single source of truth for the demo.

    python3 tools/rebuild_demo_history.py              # report only
    python3 tools/rebuild_demo_history.py --apply      # replace the documents

This is destructive by design. It refuses to run without `--apply`, and it
prints the company slug it is about to replace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')
HISTORY = ROOT / 'data' / 'confirmed.jsonl'


def load_history(path: Path = HISTORY) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def document_type(filename: str) -> str:
    """What the file name says. A type is a profile, so an unknown name stays unknown."""
    name = (filename or '').lower()
    if 'invoice' in name:
        return 'invoice_ua'
    if 'act' in name:
        return 'act_ua'
    if 'delivery' in name:
        return 'delivery_ua'
    return 'unknown'


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def review_start(record: dict[str, Any]) -> str | None:
    """When the reviewer opened the document, derived from the measured seconds.

    The seed stores how long the review took, not when it started; the database
    stores the start. Deriving one from the other keeps the measured number
    instead of turning it into an empty cell.
    """
    confirmed = _timestamp(record.get('confirmed_at'))
    seconds = record.get('review_seconds')
    if confirmed is None or seconds is None:
        return None
    return (confirmed - timedelta(seconds=seconds)).isoformat(timespec='seconds')


def document_row(record: dict[str, Any], supplier_id: int | None) -> dict[str, Any]:
    fields = record.get('fields') or {}
    parsed = record.get('parsed') or {}
    edited = list(record.get('edited_fields') or [])
    confirmed_at = record.get('confirmed_at')
    return {
        'supplier_id': supplier_id,
        'doc_type': document_type(record.get('filename', '')),
        'number': fields.get('number'),
        'doc_date': fields.get('date'),
        'received_at': record.get('uploaded_at'),
        'total': fields.get('total'),
        'vat': fields.get('vat'),
        'currency': fields.get('currency') or 'UAH',
        'status': 'confirmed',
        'file_path': f"data/history/{record.get('filename') or ''}",
        'parsed': Json({'fields': parsed, 'issues': record.get('issues') or [], 'confidence': record.get('confidence')}),
        'edited_fields': edited or None,
        'edited_at': confirmed_at if edited else None,
        'edited_by': 'reviewer' if edited else None,
        'filename': record.get('filename'),
        'source': record.get('source') or 'synthetic',
        'confirmed_at': confirmed_at,
        'confirmed_fields': Json(fields),
        'viewed_at': review_start(record),
        'crm_deal_id': record.get('crm_deal_id'),
    }


def plan(records: list[dict[str, Any]], suppliers: dict[str, int]) -> dict[str, Any]:
    """The rows to insert and the records that have no supplier name.

    A supplier is matched by name and created on demand. The seed contains a
    duplicated invoice, so the second row of the same (supplier, number, date)
    is inserted without a supplier link: the unique rule exists to stop real
    duplicates, not to lose a demo record.
    """
    rows = []
    seen: set[tuple[str, str, str]] = set()
    missing: list[str] = []
    for index, record in enumerate(records):
        fields = record.get('fields') or {}
        name = fields.get('supplier')
        supplier_id = suppliers.get(name) if name else None
        if name and supplier_id is None:
            missing.append(name)
        number = str(fields.get('number') or '')
        date = str(fields.get('date') or '')
        key = (str(supplier_id), number, date)
        dedupe_conflict = supplier_id is not None and key in seen
        if dedupe_conflict:
            supplier_id = None
        elif supplier_id is not None:
            seen.add(key)
        rows.append(
            {
                'index': index,
                'supplier_name': name,
                'dedupe_conflict': dedupe_conflict,
                'row': document_row(record, supplier_id),
            }
        )
    return {'rows': rows, 'missing_suppliers': missing}


def report(plan_result: dict[str, Any], company: dict[str, Any]) -> None:
    rows = plan_result['rows']
    edits = sum(1 for item in rows if item['row']['edited_fields'])
    reviewed = sum(1 for item in rows if item['row']['viewed_at'])
    types: dict[str, int] = {}
    for item in rows:
        types[item['row']['doc_type']] = types.get(item['row']['doc_type'], 0) + 1
    print(f"company {company['id']} ({company['slug']}): {len(rows)} documents")
    print(f'  edited: {edits}, with review start: {reviewed}')
    print('  types: ' + ', '.join(f'{name} {count}' for name, count in sorted(types.items())))
    if plan_result['missing_suppliers']:
        names = sorted(set(plan_result['missing_suppliers']))
        print(f'  suppliers created: {len(names)} ({", ".join(names[:4])}{"…" if len(names) > 4 else ""})')


def suppliers_of(connection, company_id: int) -> dict[str, int]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute('SELECT id, name FROM suppliers ORDER BY id')
        return {row['name']: row['id'] for row in cursor.fetchall()}


def ensure_suppliers(connection, company_id: int, plan_result: dict[str, Any]) -> dict[str, int]:
    names = {item['supplier_name'] for item in plan_result['rows'] if item['supplier_name']}
    known = suppliers_of(connection, company_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        for name in sorted(names - set(known)):
            # A tax ID is not invented: suppliers created here are known by name.
            cursor.execute(
                'INSERT INTO suppliers (company_id, name) VALUES (%s, %s) RETURNING id',
                (company_id, name),
            )
            known[name] = cursor.fetchone()[0]
    return known


def apply_plan(connection, company: dict[str, Any], plan_result: dict[str, Any]) -> int:
    company_id = company['id']
    known = ensure_suppliers(connection, company_id, plan_result)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        cursor.execute('DELETE FROM documents WHERE company_id = %s', (company_id,))
        removed = cursor.rowcount
        for item in plan_result['rows']:
            row = item['row']
            supplier_id = (
                None
                if item['dedupe_conflict']
                else known.get(item['supplier_name'])
            )
            row['supplier_id'] = supplier_id
            cursor.execute(
                """
                INSERT INTO documents (
                    supplier_id, doc_type, number, doc_date, received_at, total, vat,
                    currency, status, file_path, parsed, edited_fields, edited_at,
                    edited_by, company_id, filename, source, confirmed_at,
                    confirmed_fields, viewed_at
                ) VALUES (
                    %(supplier_id)s, %(doc_type)s, %(number)s, %(doc_date)s, %(received_at)s,
                    %(total)s, %(vat)s, %(currency)s, %(status)s, %(file_path)s, %(parsed)s,
                    %(edited_fields)s, %(edited_at)s, %(edited_by)s, %(company_id)s,
                    %(filename)s, %(source)s, %(confirmed_at)s, %(confirmed_fields)s,
                    %(viewed_at)s
                )
                RETURNING id
                """,
                {**row, 'company_id': company_id},
            )
            document_id = cursor.fetchone()[0]
            if row.get('crm_deal_id'):
                cursor.execute(
                    """
                    INSERT INTO deals (company_id, document_id, status, tech_status,
                                       external_id, attempts, created_at, updated_at)
                    VALUES (%s, %s, 'done', 'created', %s, 1, %s, %s)
                    """,
                    (
                        company_id,
                        document_id,
                        row['crm_deal_id'],
                        row['confirmed_at'],
                        row['confirmed_at'],
                    ),
                )
    connection.commit()
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--apply', action='store_true', help='replace the documents (default: report only)')
    parser.add_argument('--company', type=int, help='company id (default: DOCFLOW_DEFAULT_COMPANY or 1)')
    parser.add_argument('--dsn', default=DSN)
    args = parser.parse_args(argv)

    records = load_history()
    if not records:
        print(f'no history file at {HISTORY}', file=sys.stderr)
        return 1

    company_id = args.company or int(os.environ.get('DOCFLOW_DEFAULT_COMPANY', '1'))
    with psycopg2.connect(args.dsn) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute('SELECT id, slug FROM companies WHERE id = %s', (company_id,))
            company = cursor.fetchone()
        if company is None:
            print(f'no company {company_id}', file=sys.stderr)
            return 1

        suppliers = suppliers_of(connection, company_id)
        plan_result = plan(records, suppliers)
        report(plan_result, company)
        if not args.apply:
            print('report only; pass --apply to replace the documents')
            return 0

        removed = apply_plan(connection, company, plan_result)
        print(f'rebuilt company {company_id}: removed {removed}, inserted {len(plan_result["rows"])}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
