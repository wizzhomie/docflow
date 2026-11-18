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

from typing import Any
from urllib.parse import quote

import psycopg2
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg2.extras import Json, RealDictCursor

import auth
import db
import profiles
import routing
import security
from validation import blocking_issues, field_names, validate
from webutil import esc, shown

router = APIRouter(prefix="/pg", tags=["pg-queue"])
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


def _page(title: str, body: str, notice: str = "", error: str = "") -> str:
    """A page shell. Everything that is a value is escaped here; the caller
    passes `body` already safe, because it is built from escaped values and
    literal markup."""
    banner = f'<div class="notice">{esc(notice)}</div>' if notice else ""
    if error:
        banner += f'<div class="denied">{esc(error)}</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>DocFlow · {esc(title)}</title><style>{STYLE}</style></head>
<body>{banner}<h1>{esc(title)}</h1>{body}</body></html>"""


class _Refused(Exception):
    """A confirmation the invariants refuse; the page shows the reason."""


def _invariant_problem(
    fields: dict[str, Any], doc_type: str, edited: list[str] | None = None
) -> str | None:
    """What a human edit broke, in the words of the review form.

    A value that is not what it claims to be must not become a deal, so the check
    runs on the merged result — the stored values with the reviewer's edits — not
    on the stored values alone.
    """
    check = validate(
        {key: {"value": value} for key, value in fields.items()},
        profile=profiles.get_profile(doc_type),
    )
    blocking = [
        issue
        for issue in blocking_issues(check)
        if edited is None or issue.split(":")[0] in edited
    ]
    if not blocking:
        return None
    labels = profiles.ui_labels(doc_type)
    names = [labels.get(name, name.replace("_", " ")) for name in field_names(blocking)]
    return "Fix before confirming: " + ", ".join(names)


def _mark_viewed(person: dict[str, Any], doc_id: int | str) -> None:
    """Start the review session the first time the document is opened.

    Review time used to be measured from upload to confirmation, which counts the
    waiting: a document confirmed a week later reported a week of review. The
    session is what a person actually spends, and it is measured from here.
    """
    try:
        with db.tenant(person["company_id"]) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE documents SET viewed_at = coalesce(viewed_at, now()) WHERE id = %s",
                (int(doc_id),),
            )
    except (psycopg2.Error, ValueError):
        # Never lose a page because the timer could not be started.
        pass


def _amount(value: Any, currency: str | None) -> str:
    """A number as the page shows it: grouped with spaces, unit appended."""
    try:
        text = f"{float(value or 0):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        text = str(value)
    return esc(f"{text} {currency}".strip())


def _rows(user: dict[str, Any], sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Read inside the tenant of that person, in one transaction.

    The company is set by `db.tenant`, which scopes it to the transaction: a
    connection that goes back to the pool carries no tenant at all, so the next
    request cannot inherit this one's company.
    """
    try:
        with db.tenant(user["company_id"]) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error


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


@router.get("/login")
def login(next: str | None = None) -> Any:
    """The old demo picker is gone: sign-in is a password or a Google account."""
    return RedirectResponse("/auth/login" + (f"?next={next}" if next else ""), status_code=303)


def _queue_rows(documents: list[dict[str, Any]]) -> str:
    """The table of documents waiting for review.

    A supplier name here is whatever the uploaded invoice said, so every value
    goes through `shown`: the queue is the page a reviewer opens, which makes it
    the place a stored cross-site script would run.
    """
    return "".join(
        f'<tr><td><a href="/pg/queue/{int(item["id"])}">'
        f'{shown(item["doc_type"].replace("_ua", ""))}</a></td>'
        f'<td>{shown(item["supplier"])}</td><td>{shown(item["number"])}</td>'
        f'<td>{shown(item["doc_date"])}</td>'
        f'<td class="n">{_amount(item["total"], item["currency"])}</td>'
        f'<td class="n">{_amount(item["events"], None)}</td></tr>'
        for item in documents
    ) or '<tr><td colspan="6">Nothing is waiting for review.</td></tr>'


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, ok: str | None = None) -> Any:
    person = session_person(request)
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
    rows = _queue_rows(documents)

    body = (
        f'<div class="sub"><a href="/app">← workspace</a> · signed in as {shown(person["full_name"])}'
        f' · {shown(person["company_name"])} · <span class="pill">{shown(person["role"])}</span>'
        f' · <a href="/auth/logout">sign out</a></div>'
        f'<table><tr><th>Type</th><th>Supplier</th><th>Number</th><th>Date</th>'
        f'<th class="n">Total</th><th class="n">Events</th></tr>{rows}</table>'
        f'<div class="sub">{len(documents)} waiting for review. Every read and write is '
        f'filtered by the database to this company.</div>'
    )
    notice = "Confirmed. The deal and the event were written in one transaction." if ok else ""
    return _page("Review queue", body, notice)


@router.get("/queue/{doc_id}", response_class=HTMLResponse)
def document(doc_id: int, request: Request, invalid: str | None = None) -> Any:
    person = session_person(request)
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
    _mark_viewed(person, doc_id)
    csrf = security.expected(request)

    events = _rows(
        person,
        "SELECT event_type, actor, created_at FROM document_events "
        "WHERE document_id = %s ORDER BY id",
        (doc_id,),
    )
    history = "".join(
        f'<tr><td>{shown(event["event_type"])}</td><td>{shown(event["actor"])}</td>'
        f'<td>{esc(event["created_at"].strftime("%Y-%m-%d %H:%M"))}</td></tr>'
        for event in events
    ) or '<tr><td colspan="3">No events yet.</td></tr>'

    response = HTMLResponse(
        _page(
            "Document",
            _document_body(item, person, history, _document_form(item, person, csrf)),
            error=invalid,
        )
    )
    security.ensure(response, request, csrf)
    return response


def _document_form(item: dict[str, Any], person: dict[str, Any], csrf: str = "") -> str:
    """The review form, or the reason there is none.

    Every value is escaped: the number and the currency are read from the PDF,
    and this form writes them back into an attribute.
    """
    if item["status"] != "new":
        return (f'<div class="card">This document is already '
                f'<strong>{esc(item["status"])}</strong>.</div>')
    if not can_confirm(person["role"]):
        return ('<div class="denied">Your role is <strong>viewer</strong>: you can read the '
                'queue, but confirming a document is refused before anything is written.</div>')

    total_value = f'{float(item["total"] or 0):.2f}'
    return f"""
    <form method="post" action="/pg/queue/{int(item["id"])}/confirm">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <label for="number">Number</label>
      <input id="number" name="number" value="{esc(item['number'] or '')}">
      <label for="doc_date">Date</label>
      <input id="doc_date" name="doc_date" type="date" value="{esc(item['doc_date'] or '')}">
      <label for="total">Total</label>
      <input id="total" name="total" value="{esc(total_value)}">
      <label for="currency">Currency</label>
      <input id="currency" name="currency" value="{esc(item['currency'] or '')}">
      <button type="submit">Confirm</button>
    </form>
    """


def _document_body(
    item: dict[str, Any], person: dict[str, Any], history: str, form: str
) -> str:
    edited = ", ".join(esc(field) for field in item["edited_fields"] or [])
    return (
        f'<div class="sub"><a href="/app">← workspace</a> · <a href="/pg/queue">queue</a>'
        f' · {shown(person["company_name"])} · <span class="pill">{shown(person["role"])}</span></div>'
        f'<div class="card"><h2>{shown(item["doc_type"])} · {shown(item["supplier"])}</h2>'
        f'<table><tr><th>Field</th><th>Value</th></tr>'
        f'<tr><td>Tax ID</td><td>{shown(item["tax_id"])}</td></tr>'
        f'<tr><td>Status</td><td>{shown(item["status"])}</td></tr>'
        f'<tr><td>Edited by</td><td>{shown(item["edited_by"])}</td></tr>'
        f'<tr><td>Edited fields</td><td>{edited or "—"}</td></tr>'
        f'</table>{form}</div>'
        f'<h2>History</h2><table><tr><th>Event</th><th>Actor</th><th>When</th></tr>{history}</table>'
    )


@router.post("/queue/{doc_id}/confirm")
async def confirm(doc_id: int, request: Request) -> Any:
    person = session_person(request)
    if person is None:
        return RedirectResponse("/pg/login", status_code=303)
    if not can_confirm(person["role"]):
        raise HTTPException(status_code=403, detail="A viewer cannot confirm documents")

    form = await request.form()
    if not security.valid(request, form.get("csrf")):
        return RedirectResponse(
            f"/pg/queue/{doc_id}?invalid={quote('The page expired. Open the document again.')}",
            status_code=303,
        )
    submitted = {key: str(form.get(key, "")).strip() for key in ("number", "doc_date", "total", "currency")}
    # Read the company's threshold before the write transaction opens: a second
    # connection inside the first one is how a pool with one connection deadlocks.
    threshold = _threshold(person)

    try:
        with db.tenant(person["company_id"]) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(
                """
                SELECT d.id, d.doc_type, d.number, d.doc_date, d.total, d.vat, d.currency, d.status,
                       coalesce(s.name, 'unknown supplier') AS supplier, s.tax_id
                FROM documents d LEFT JOIN suppliers s ON s.id = d.supplier_id
                WHERE d.id = %s FOR UPDATE OF d
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

            effective = {
                "number": submitted["number"] or current["number"],
                "date": submitted["doc_date"] or (
                    current["doc_date"].isoformat() if current["doc_date"] else None
                ),
                "supplier": current["supplier"],
                "tax_id": current["tax_id"],
                # numeric columns arrive as Decimal, which jsonb cannot take:
                # the confirmed snapshot is JSON, so the numbers are floats here.
                "total": _number(submitted["total"]) if submitted["total"] else _number(current["total"]),
                "vat": _number(current["vat"]),
                "currency": submitted["currency"] or current["currency"],
            }
            problem = _invariant_problem(effective, current["doc_type"], changed)
            if problem:
                raise _Refused(problem)

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
                       edited_by = %s,
                       confirmed_at = coalesce(confirmed_at, now()),
                       confirmed_fields = %s
                 WHERE id = %s
                """,
                (
                    submitted["number"],
                    submitted["doc_date"],
                    submitted["total"],
                    submitted["currency"],
                    changed or None,
                    person["email"],
                    Json(effective),
                    doc_id,
                ),
            )

            destination = routing.route(
                current["doc_type"],
                {"total": submitted["total"] or current["total"]},
                threshold=threshold,
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
    except _Refused as refused:
        return RedirectResponse(
            f"/pg/queue/{doc_id}?invalid={quote(str(refused))}", status_code=303
        )
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error

    return RedirectResponse("/pg/queue?ok=1", status_code=303)

# --- what the workspace at /app uses -------------------------------------------
#
# The workspace keeps its upload and extraction on files; only the queue, the
# document panel and the confirm come from the database when a person is signed
# in. The shapes below are the ones the existing interface already expects, so
# the front end needs no knowledge of where the rows came from.


def session_person(request: Request) -> dict[str, Any] | None:
    """The signed-in person behind the request, or None for the file-based demo."""
    return auth.session_person(request)


def _document_fields(parsed: dict[str, Any] | None) -> dict[str, Any]:
    fields = (parsed or {}).get("fields") or {}
    return {key: (value.get("value") if isinstance(value, dict) else value) for key, value in fields.items()}


def _field_confidence(parsed: dict[str, Any] | None, key: str) -> float | None:
    """The confidence the extractor gave this field, or the document's own.

    A hard-coded 1.0 would tell a reviewer the parser was certain about a value
    it may have read from an OCR shadow; the number that was stored is the
    honest one, and a missing number is better than a full one.
    """
    fields = (parsed or {}).get("fields") or {}
    entry = fields.get(key)
    if isinstance(entry, dict) and isinstance(entry.get("confidence"), (int, float)):
        return float(entry["confidence"])
    document_confidence = (parsed or {}).get("confidence")
    if isinstance(document_confidence, (int, float)):
        return float(document_confidence)
    return None


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
    _mark_viewed(person, doc_id)
    rows = _rows(
        person,
        """
        SELECT d.id, d.doc_type, d.number, d.received_at, d.parsed, d.confirmed_fields, d.status,
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
    # A confirmed document shows what the person approved, not what was read.
    values = row.get("confirmed_fields") or parsed.get("fields") or {}
    values = {
        key: (entry.get("value") if isinstance(entry, dict) else entry)
        for key, entry in values.items()
    }
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
                key: {"value": value, "confidence": _field_confidence(parsed, key)}
                for key, value in values.items()
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
    threshold = _threshold(person)
    try:
        with db.tenant(person["company_id"]) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
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

            problem = _invariant_problem(effective, current["doc_type"], edited)
            if problem:
                raise HTTPException(status_code=422, detail=problem)

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
                       edited_by = %s,
                       confirmed_at = coalesce(confirmed_at, now()),
                       confirmed_fields = %s
                 WHERE id = %s
                """,
                (
                    str(submitted.get("number") or ""),
                    str(submitted.get("date") or submitted.get("doc_date") or ""),
                    _number(submitted.get("total")),
                    str(submitted.get("currency") or ""),
                    edited or None,
                    person["email"],
                    Json(effective),
                    int(doc_id),
                ),
            )

            destination = routing.route(current["doc_type"], effective, threshold=threshold)

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
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error

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
