"""Documents that live in the database from the first byte.

Before this module a document was born in files and grew up in the database: the
upload wrote a pending JSON and a history line, the queue row appeared only after
an import, and the two could disagree. Now the signed-in upload creates the row,
the confirm finishes it, and the history reads it back — in the same shape the
file store returns, so the interface never has to know which store answered.

What is deliberately not here: the file store. The standalone demonstration runs
without PostgreSQL, and `app.py` keeps that path. The difference is not two
products side by side any more — it is one product, plus a demonstration mode that
stores its state in files because it has no database to store it in.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

import db

DOC_TYPES = ('invoice_ua', 'act_ua', 'delivery_ua')


def _values(parsed: dict[str, Any] | None) -> dict[str, Any]:
    """The field values out of the parser's `{field: {value, confidence}}` shape."""
    fields = (parsed or {}).get('fields') or {}
    return {
        key: (entry.get('value') if isinstance(entry, dict) else entry)
        for key, entry in fields.items()
    }


def _date(value: Any) -> date | None:
    """An ISO date or nothing: a bad date must not fail the upload after extraction."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).replace(' ', '').replace(',', '.'))
    except ValueError:
        return None


def _currency(value: Any) -> str:
    text = str(value or '').strip().upper()
    return text[:3] if len(text) == 3 else 'UAH'


def supplier_id(cursor, company_id: int, fields: dict[str, Any]) -> int | None:
    """The supplier this document belongs to, created the first time it is seen.

    Matched on the tax id when there is one, otherwise on the name: two invoices
    from one company must land on one supplier row, or the history per supplier is
    fiction. The partial unique index on the documents side still decides what is
    a duplicate invoice.
    """
    name = str(fields.get('supplier') or '').strip()
    tax_id = str(fields.get('tax_id') or '').strip()
    if not name and not tax_id:
        return None

    if tax_id:
        cursor.execute(
            'SELECT id FROM suppliers WHERE company_id = %s AND tax_id = %s LIMIT 1',
            (company_id, tax_id),
        )
    else:
        cursor.execute(
            'SELECT id FROM suppliers WHERE company_id = %s AND lower(name) = lower(%s) LIMIT 1',
            (company_id, name),
        )
    row = cursor.fetchone()
    if row:
        return row['id']

    cursor.execute(
        'INSERT INTO suppliers (company_id, name, tax_id) VALUES (%s, %s, %s) RETURNING id',
        (company_id, name or tax_id, tax_id or None),
    )
    return cursor.fetchone()['id']


def store_upload(person: dict[str, Any], meta: dict[str, Any], file_path: str) -> str:
    """Write the uploaded document, and return its row id as a string.

    The id is returned as a string because that is what the interface has always
    used for a document, and a numeric id is one of the two shapes the confirm
    accepts.
    """
    parsed = meta.get('parsed') or {}
    fields = _values(parsed)
    doc_type = parsed.get('type') if parsed.get('type') in DOC_TYPES else 'invoice_ua'
    company_id = person['company_id']

    try:
        with db.tenant(company_id) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            supplier = supplier_id(cursor, company_id, fields)
            cursor.execute(
                """
                INSERT INTO documents (
                    company_id, supplier_id, doc_type, number, doc_date, received_at,
                    total, vat, currency, status, file_path, parsed, filename, source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    company_id,
                    supplier,
                    doc_type,
                    str(fields.get('number') or '') or None,
                    _date(fields.get('date')),
                    meta.get('uploaded_at') or datetime.now().astimezone(),
                    _number(fields.get('total')),
                    _number(fields.get('vat')),
                    _currency(fields.get('currency')),
                    file_path,
                    Json(parsed),
                    meta.get('filename'),
                    meta.get('source') or 'upload',
                ),
            )
            return str(cursor.fetchone()['id'])
    except psycopg2.errors.UniqueViolation:
        # The same invoice, uploaded twice: the unique index is the judge, and the
        # answer is the document that already exists — not a second one.
        with db.tenant(company_id) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(
                """
                SELECT id FROM documents
                 WHERE company_id = %s AND supplier_id = %s AND number = %s AND doc_date = %s
                """,
                (
                    company_id,
                    supplier_id(cursor, company_id, fields),
                    str(fields.get('number') or '') or None,
                    _date(fields.get('date')),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise
            return str(row['id'])


def update_meaning(person: dict[str, Any], doc_id: str, parsed: dict[str, Any]) -> None:
    """Put an improved parse on a document that is still waiting for a person.

    The model step runs after the upload response, so the row already exists. Only a
    document still in `new` is touched: one a person confirmed, or one an outbox has
    sent, is a record with someone's name behind it, and the model has no business
    rewriting it afterwards.
    """
    fields = _values(parsed)
    company_id = person['company_id']
    with db.tenant(company_id) as connection, connection.cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute(
            """
            UPDATE documents
               SET supplier_id = %s, number = %s, doc_date = %s, total = %s,
                   vat = %s, currency = %s, parsed = %s
             WHERE id = %s AND status = 'new'
            """,
            (
                supplier_id(cursor, company_id, fields),
                str(fields.get('number') or '') or None,
                _date(fields.get('date')),
                _number(fields.get('total')),
                _number(fields.get('vat')),
                _currency(fields.get('currency')),
                Json(parsed),
                int(doc_id),
            ),
        )


def document_file_path(person: dict[str, Any], doc_id: str, uploads: Path) -> Path | None:
    """The stored original of a document, if this company owns it and it is on disk.

    The path in the row is repo-relative and only the name is used to find the
    file, under the uploads directory the application was started with: the
    database must not be able to point the server at an arbitrary file.
    """
    if not str(doc_id).isdigit():
        return None
    with db.tenant(person['company_id']) as connection, connection.cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute('SELECT file_path FROM documents WHERE id = %s', (int(doc_id),))
        row = cursor.fetchone()
    if not row or not row['file_path']:
        return None
    candidate = (uploads / Path(row['file_path']).name).resolve()
    root = uploads.resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _flat(value: Any) -> Any:
    """A field value, whether it came from the parser's shape or the snapshot."""
    return value.get('value') if isinstance(value, dict) else value


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """One database row in the shape the file store has always returned."""
    parsed = row.get('parsed') or {}
    # What the person approved, when there is such a thing: a history that shows
    # the parsed number as if it had been read is a history nobody can audit.
    confirmed = row.get('confirmed_fields') or parsed.get('fields') or {}
    fields = {key: _flat(value) for key, value in confirmed.items()}
    confirmed_at = row.get('confirmed_at')
    viewed_at = row.get('viewed_at')
    deal_id = row.get('deal_id')
    deal_external_id = row.get('deal_external_id')
    # Measured only when a person actually opened the document: the time between
    # upload and confirmation is mostly waiting, and reporting it as review time
    # would flatter the product with someone else's queue.
    review_seconds = (
        int((confirmed_at - viewed_at).total_seconds())
        if confirmed_at is not None and viewed_at is not None and confirmed_at >= viewed_at
        else None
    )
    return {
        'id': str(row['id']),
        'filename': row.get('filename') or f"{row['doc_type']}-{row['id']}.pdf",
        'uploaded_at': row['received_at'].isoformat(timespec='seconds'),
        'confirmed_at': confirmed_at.isoformat(timespec='seconds') if confirmed_at else None,
        'fields': fields,
        'parsed': {key: _flat(entry) for key, entry in (parsed.get('fields') or {}).items()},
        'edited_fields': list(row.get('edited_fields') or []),
        'review_seconds': review_seconds,
        'confidence': parsed.get('confidence'),
        'issues': list(parsed.get('issues') or []),
        # The number the CRM returned is what a person would search for; only
        # a deal that never left the host gets the demo marker.
        'crm_deal_id': deal_external_id or (f'DEMO-DB-{deal_id}' if deal_id else None),
        'doc_type': row['doc_type'],
        'source': row.get('source') or 'upload',
    }


def history(person: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    """Confirmed documents, newest last, in the shape the reports already read."""
    sql = """
        SELECT d.id, d.filename, d.received_at, d.confirmed_at, d.viewed_at, d.parsed,
               d.confirmed_fields, d.edited_fields, d.doc_type, d.source,
               (SELECT max(deals.id) FROM deals WHERE deals.document_id = d.id) AS deal_id,
               (SELECT max(deals.external_id) FROM deals WHERE deals.document_id = d.id) AS deal_external_id
          FROM documents d
         WHERE d.status IN ('confirmed', 'sent')
         ORDER BY d.confirmed_at NULLS LAST, d.id
    """
    params: tuple = ()
    if limit is not None:
        sql += ' LIMIT %s'
        params = (limit,)
    with db.tenant(person['company_id']) as connection, connection.cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute(sql, params)
        return [_record(row) for row in cursor.fetchall()]
