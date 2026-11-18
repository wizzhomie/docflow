"""The review queue on PostgreSQL, next to the file-based workspace.

The file-based workspace stays as it is; this is the same workflow with its state
in the database, so the tenancy and the audit trail can be demonstrated end to
end. Three things differ from a plain CRUD screen:

- the person is a session: no passwords in a demo, but the tenant and the role
  come from the person, not from the URL, so a link cannot switch tenants;
- every read and write runs inside the tenant of that person, and the database
  enforces it (see `sql/003_multi_tenant.sql`);
- a viewer can open the queue and read a document, but the confirm is refused —
  the role is checked before anything is written.

The approval threshold is not a constant here: it comes from the settings table,
where a company value overrides the platform default.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg2
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg2.extras import Json, RealDictCursor

import routing

router = APIRouter(prefix="/pg", tags=["pg-queue"])
DSN = os.environ.get("DOCFLOW_PG_DSN", "dbname=docflow_dev")
COOKIE = "docflow_user"

STYLE = """
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:36px;background:#faf9f7;color:#1d1b1a}
h1{font-size:21px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 10px}
.sub{color:#6b6560;margin-bottom:22px}
table{border-collapse:collapse;width:100%;max-width:860px;margin-bottom:22px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #e8e4df;font-size:14px}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b6560}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
a{color:#4a3aff} .pill{display:inline-block;background:#efece7;border-radius:999px;padding:2px 10px;font-size:12px}
.card{background:#fff;border:1px solid #e8e4df;border-radius:14px;padding:18px 20px;max-width:860px;margin-bottom:20px}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b6560;margin:12px 0 4px}
input,select{font:15px inherit;padding:8px 10px;border:1px solid #d8d3cc;border-radius:9px;width:260px}
button{font:15px inherit;margin-top:16px;padding:9px 18px;border:0;border-radius:999px;background:#4a3aff;color:#fff;cursor:pointer}
.notice{background:#eef7ee;border:1px solid #cbe3cb;border-radius:12px;padding:10px 14px;max-width:860px;margin-bottom:18px}
.denied{background:#fdf2f2;border:1px solid #f3d0d0;border-radius:12px;padding:10px 14px;max-width:860px;margin-bottom:18px}
"""


def can_confirm(role: str) -> bool:
    """Who may turn a reviewed document into a deal. A viewer may not."""
    return role in ("owner", "reviewer")


def _page(title: str, body: str, notice: str = "") -> str:
    banner = f'<div class="notice">{notice}</div>' if notice else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>DocFlow · {title}</title><style>{STYLE}</style></head>
<body>{banner}<h1>{title}</h1>{body}</body></html>"""


def _connect() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(DSN)
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error


def _user(user_id: str | None) -> dict[str, Any] | None:
    """The person behind the cookie, with the company and the role."""
    if not user_id or not user_id.isdigit():
        return None
    connection = _connect()
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.full_name, u.role, u.company_id, c.name AS company_name
                FROM users u JOIN companies c ON c.id = u.company_id
                WHERE u.id = %s
                """,
                (int(user_id),),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        connection.close()


def _tenant(user: dict[str, Any]):
    """A connection that has told the database which company it serves."""
    connection = _connect()
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.company_id', %s, false)", (str(user["company_id"]),))
    return connection


def _rows(user: dict[str, Any], sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    connection = _tenant(user)
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _threshold(user: dict[str, Any]) -> float:
    rows = _rows(
        user,
        "SELECT setting_value('routing.approval_threshold', %s) AS value",
        (user["company_id"],),
    )
    try:
        return float(rows[0]["value"])
    except (IndexError, TypeError, ValueError):
        return routing.APPROVAL_THRESHOLD


@router.get("/login", response_class=HTMLResponse)
def login(user: str | None = None) -> Any:
    """Demo sign-in: pick a company and a person. No passwords in a demo."""
    if user:
        response = RedirectResponse("/pg/queue", status_code=303)
        response.set_cookie(COOKIE, user, httponly=True, samesite="lax")
        return response

    connection = _connect()
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT c.name AS company, c.slug, u.id, u.full_name, u.email, u.role
                FROM companies c JOIN users u ON u.company_id = c.id
                ORDER BY c.id, u.id
                """
            )
            people = [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

    body = ['<div class="sub">Pick a person to enter as. Each one belongs to one company '
            'and has one role — that is the whole session.</div>']
    current_company = None
    for person in people:
        if person["company"] != current_company:
            if current_company is not None:
                body.append("</table></div>")
            current_company = person["company"]
            body.append(f'<div class="card"><h2>{person["company"]}</h2><table>'
                        '<tr><th>Person</th><th>Role</th><th></th></tr>')
        body.append(
            f'<tr><td>{person["full_name"]}<br><span class="sub">{person["email"]}</span></td>'
            f'<td><span class="pill">{person["role"]}</span></td>'
            f'<td><a href="/pg/login?user={person["id"]}">enter</a></td></tr>'
        )
    if people:
        body.append("</table></div>")
    return _page("Sign in", "".join(body))


@router.get("/queue", response_class=HTMLResponse)
def queue(docflow_user: str | None = Cookie(None), ok: str | None = None) -> Any:
    person = _user(docflow_user)
    if person is None:
        return RedirectResponse("/pg/login", status_code=303)

    documents = _rows(
        person,
        """
        SELECT d.id, d.doc_type, d.number, d.doc_date, d.total, d.currency,
               coalesce(s.name, 'unknown supplier') AS supplier,
               (SELECT count(*) FROM document_events e
                 WHERE e.document_id = d.id) AS events
        FROM documents d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.status = 'new'
        ORDER BY d.received_at, d.id
        """,
    )
    rows = "".join(
        f'<tr><td><a href="/pg/queue/{item["id"]}">{item["doc_type"].replace("_ua", "")}</a></td>'
        f'<td>{item["supplier"]}</td><td>{item["number"] or "—"}</td>'
        f'<td>{item["doc_date"] or "—"}</td>'
        f'<td class="n">{float(item["total"] or 0):,.0f} {item["currency"]}</td>'
        f'<td class="n">{item["events"]}</td></tr>'.replace(",", " ")
        for item in documents
    ) or '<tr><td colspan="6">Nothing is waiting for review.</td></tr>'

    body = (
        f'<div class="sub">Signed in as {person["full_name"]} · {person["company_name"]} · '
        f'<span class="pill">{person["role"]}</span> · <a href="/pg/login">switch</a></div>'
        f'<table><tr><th>Type</th><th>Supplier</th><th>Number</th><th>Date</th>'
        f'<th class="n">Total</th><th class="n">Events</th></tr>{rows}</table>'
        f'<div class="sub">{len(documents)} waiting for review. Every read and write is '
        f'filtered by the database to this company.</div>'
    )
    notice = "Confirmed. The deal and the event were written in one transaction." if ok else ""
    return _page("Review queue", body, notice)


@router.get("/queue/{doc_id}", response_class=HTMLResponse)
def document(doc_id: int, docflow_user: str | None = Cookie(None)) -> Any:
    person = _user(docflow_user)
    if person is None:
        return RedirectResponse("/pg/login", status_code=303)

    found = _rows(
        person,
        """
        SELECT d.id, d.doc_type, d.number, d.doc_date, d.total, d.vat, d.currency,
               d.status, d.edited_fields, d.edited_by,
               coalesce(s.name, 'unknown supplier') AS supplier, s.tax_id
        FROM documents d LEFT JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.id = %s
        """,
        (doc_id,),
    )
    if not found:
        raise HTTPException(status_code=404, detail="No such document in this company")
    item = found[0]

    events = _rows(
        person,
        "SELECT event_type, actor, created_at FROM document_events "
        "WHERE document_id = %s ORDER BY id",
        (doc_id,),
    )
    history = "".join(
        f'<tr><td>{event["event_type"]}</td><td>{event["actor"] or "—"}</td>'
        f'<td>{event["created_at"]:%Y-%m-%d %H:%M}</td></tr>'
        for event in events
    ) or '<tr><td colspan="3">No events yet.</td></tr>'

    form = f"""
    <form method="post" action="/pg/queue/{doc_id}/confirm">
      <label for="number">Number</label>
      <input id="number" name="number" value="{item['number'] or ''}">
      <label for="doc_date">Date</label>
      <input id="doc_date" name="doc_date" type="date" value="{item['doc_date'] or ''}">
      <label for="total">Total</label>
      <input id="total" name="total" value="{float(item['total'] or 0):.2f}">
      <label for="currency">Currency</label>
      <input id="currency" name="currency" value="{item['currency']}">
      <button type="submit">Confirm</button>
    </form>
    """

    if item["status"] != "new":
        form = f'<div class="card">This document is already <strong>{item["status"]}</strong>.</div>'
    elif not can_confirm(person["role"]):
        form = ('<div class="denied">Your role is <strong>viewer</strong>: you can read the '
                'queue, but confirming a document is refused before anything is written.</div>')

    body = (
        f'<div class="sub"><a href="/pg/queue">← queue</a> · {person["company_name"]} · '
        f'<span class="pill">{person["role"]}</span></div>'
        f'<div class="card"><h2>{item["doc_type"]} · {item["supplier"]}</h2>'
        f'<table><tr><th>Field</th><th>Value</th></tr>'
        f'<tr><td>Tax ID</td><td>{item["tax_id"] or "—"}</td></tr>'
        f'<tr><td>Status</td><td>{item["status"]}</td></tr>'
        f'<tr><td>Edited by</td><td>{item["edited_by"] or "—"}</td></tr>'
        f'<tr><td>Edited fields</td><td>{", ".join(item["edited_fields"] or []) or "—"}</td></tr>'
        f'</table>{form}</div>'
        f'<h2>History</h2><table><tr><th>Event</th><th>Actor</th><th>When</th></tr>{history}</table>'
    )
    return _page("Document", body)


@router.post("/queue/{doc_id}/confirm")
async def confirm(doc_id: int, request: Request, docflow_user: str | None = Cookie(None)) -> Any:
    person = _user(docflow_user)
    if person is None:
        return RedirectResponse("/pg/login", status_code=303)
    if not can_confirm(person["role"]):
        raise HTTPException(status_code=403, detail="A viewer cannot confirm documents")

    form = await request.form()
    submitted = {key: str(form.get(key, "")).strip() for key in ("number", "doc_date", "total", "currency")}

    connection = _tenant(person)
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, doc_type, number, doc_date, total, currency, status
                FROM documents WHERE id = %s FOR UPDATE
                """,
                (doc_id,),
            )
            current = cursor.fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="No such document in this company")
            if current["status"] != "new":
                raise HTTPException(status_code=409, detail="This document is already confirmed")

            changed = []
            for field in ("number", "doc_date", "total", "currency"):
                new = submitted[field]
                if not new:
                    continue
                if field == "total":
                    try:
                        if float(current["total"] or 0) != float(new.replace(",", ".")):
                            changed.append(field)
                    except ValueError:
                        changed.append(field)
                    continue
                if str(current[field]) != new:
                    changed.append(field)

            cursor.execute(
                """
                UPDATE documents
                   SET number = coalesce(nullif(%s, ''), number),
                       doc_date = coalesce(nullif(%s, '')::date, doc_date),
                       total = coalesce(nullif(%s, '')::numeric, total),
                       currency = coalesce(nullif(%s, ''), currency),
                       status = 'confirmed',
                       edited_fields = %s,
                       edited_at = now(),
                       edited_by = %s
                 WHERE id = %s
                """,
                (
                    submitted["number"],
                    submitted["doc_date"],
                    submitted["total"],
                    submitted["currency"],
                    changed or None,
                    person["email"],
                    doc_id,
                ),
            )

            destination = routing.route(
                current["doc_type"],
                {"total": submitted["total"] or current["total"]},
                threshold=_threshold(person),
            )

            cursor.execute(
                """
                INSERT INTO deals (company_id, document_id, status, tech_status)
                VALUES (%s, %s, 'new', 'pending')
                RETURNING id
                """,
                (person["company_id"], doc_id),
            )
            deal_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO document_events (company_id, document_id, event_type, actor, payload)
                VALUES (%s, %s, 'confirmed', %s, %s)
                """,
                (
                    person["company_id"],
                    doc_id,
                    person["email"],
                    Json(
                        {
                            "destination": destination["destination"],
                            "reason": destination["reason"],
                            "edited_fields": changed,
                            "deal_id": deal_id,
                        }
                    ),
                ),
            )
    finally:
        connection.close()

    return RedirectResponse("/pg/queue?ok=1", status_code=303)

# --- what the workspace at /app uses -------------------------------------------
#
# The workspace keeps its upload and extraction on files; only the queue, the
# document panel and the confirm come from the database when a person is signed
# in. The shapes below are the ones the existing interface already expects, so
# the front end needs no knowledge of where the rows came from.


def session_person(request: Request) -> dict[str, Any] | None:
    """The signed-in person behind the request, or None for the file-based demo."""
    return _user(request.cookies.get(COOKIE))


def _document_fields(parsed: dict[str, Any] | None) -> dict[str, Any]:
    fields = (parsed or {}).get("fields") or {}
    return {key: (value.get("value") if isinstance(value, dict) else value) for key, value in fields.items()}


def _queue_row(row: dict[str, Any], threshold: float) -> dict[str, Any]:
    parsed = row.get("parsed") or {}
    fields = _document_fields(parsed)
    return {
        "id": str(row["id"]),
        "filename": f"{row['doc_type']}-{row.get('number') or row['id']}.pdf",
        "uploaded_at": row["received_at"].isoformat(timespec="seconds"),
        "suffix": "pdf",
        "source": "postgres",
        "issues": list(parsed.get("issues") or []),
        "confidence": parsed.get("confidence"),
        "doc_type": row["doc_type"],
        "route": routing.route(row["doc_type"], fields, threshold=threshold),
        "fields": fields,
    }


def queue_documents(person: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    rows = _rows(
        person,
        """
        SELECT d.id, d.doc_type, d.number, d.received_at, d.parsed, d.total, d.currency
        FROM documents d
        WHERE d.status = 'new'
        ORDER BY d.received_at DESC, d.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    threshold = _threshold(person)
    return [_queue_row(row, threshold) for row in rows]


def document_meta(person: dict[str, Any], doc_id: str) -> dict[str, Any]:
    if not str(doc_id).isdigit():
        raise HTTPException(status_code=404, detail="No such document in this company")
    rows = _rows(
        person,
        """
        SELECT d.id, d.doc_type, d.number, d.received_at, d.parsed, d.status,
               coalesce(s.name, 'unknown supplier') AS supplier
        FROM documents d LEFT JOIN suppliers s ON s.id = d.supplier_id
        WHERE d.id = %s
        """,
        (int(doc_id),),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No such document in this company")
    row = rows[0]
    parsed = row.get("parsed") or {}
    return {
        "id": str(row["id"]),
        "filename": f"{row['doc_type']}-{row.get('number') or row['id']}.pdf",
        "uploaded_at": row["received_at"].isoformat(timespec="seconds"),
        "suffix": "pdf",
        "source": "postgres",
        "supplier": row["supplier"],
        "status": row["status"],
        "parsed": {
            "fields": {
                key: {"value": value, "confidence": 1.0}
                for key, value in _document_fields(parsed).items()
            },
            "issues": list(parsed.get("issues") or []),
            "confidence": parsed.get("confidence"),
            "type": row["doc_type"],
            "type_label": row["doc_type"].replace("_ua", "").replace("_", " ").title(),
        },
    }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def confirm_document(person: dict[str, Any], doc_id: str, submitted: dict[str, Any]) -> dict[str, Any]:
    """Confirm one document: the row, the deal and the event, in one transaction."""
    if not can_confirm(person["role"]):
        raise HTTPException(status_code=403, detail="A viewer cannot confirm documents")
    if not str(doc_id).isdigit():
        raise HTTPException(status_code=404, detail="No such document in this company")

    deal_id: int | None = None
    connection = _tenant(person)
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, doc_type, number, doc_date, total, currency, status, parsed
                FROM documents WHERE id = %s FOR UPDATE
                """,
                (int(doc_id),),
            )
            current = cursor.fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="No such document in this company")
            if current["status"] != "new":
                raise HTTPException(status_code=409, detail="This document is already confirmed")

            parsed_fields = _document_fields(current["parsed"])
            effective = {**parsed_fields, **{k: v for k, v in submitted.items() if v not in (None, "")}}
            edited = [key for key, value in submitted.items() if parsed_fields.get(key) != value]

            cursor.execute(
                """
                UPDATE documents
                   SET number = coalesce(nullif(%s, ''), number),
                       doc_date = coalesce(nullif(%s, '')::date, doc_date),
                       total = coalesce(%s, total),
                       currency = coalesce(nullif(%s, ''), currency),
                       status = 'confirmed',
                       edited_fields = %s,
                       edited_at = now(),
                       edited_by = %s
                 WHERE id = %s
                """,
                (
                    str(submitted.get("number") or ""),
                    str(submitted.get("date") or submitted.get("doc_date") or ""),
                    _number(submitted.get("total")),
                    str(submitted.get("currency") or ""),
                    edited or None,
                    person["email"],
                    int(doc_id),
                ),
            )

            destination = routing.route(current["doc_type"], effective, threshold=_threshold(person))

            cursor.execute(
                """
                INSERT INTO deals (company_id, document_id, status, tech_status)
                VALUES (%s, %s, 'new', 'pending') RETURNING id
                """,
                (person["company_id"], int(doc_id)),
            )
            deal_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO document_events (company_id, document_id, event_type, actor, payload)
                VALUES (%s, %s, 'confirmed', %s, %s)
                """,
                (
                    person["company_id"],
                    int(doc_id),
                    person["email"],
                    Json({
                        "destination": destination["destination"],
                        "reason": destination["reason"],
                        "edited_fields": edited,
                        "deal_id": deal_id,
                        "source": "workspace",
                    }),
                ),
            )
    finally:
        connection.close()

    return {
        "ok": True,
        "edited_fields": edited,
        "route": destination,
        "record": {
            "id": str(doc_id),
            "edited_fields": edited,
            "destination": destination["destination"],
            "crm_deal_id": f"DEMO-DB-{deal_id}",
            "confirmed_by": person["email"],
        },
        "crm": {
            "demo": True,
            "simulated": True,
            "provider": "postgres-pilot",
            # The interface shows the deal title, so the answer carries the same
            # shape the file-based path returns.
            "deal": {
                "id": f"DEMO-DB-{deal_id}",
                "title": f"{current['doc_type']} {current.get('number') or doc_id}".strip(),
            },
        },
    }
