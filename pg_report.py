"""Read-only reports from the practice PostgreSQL database.

Pilot wiring: the review workflow still runs on files; these endpoints answer
questions straight from the `docflow_dev` schema so the two can be compared.
Connection uses the OS user via peer authentication on the local socket.

Every query runs inside one tenant. The tenant is not named in the SQL: the
connection says who it is serving, and the row-level policies in the database
hide everything else. `GET /api/pg/summary?company=2` answers as that company.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg2
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor

import db
from webutil import esc, shown

router = APIRouter(prefix="/api/pg", tags=["pg"])
DEFAULT_COMPANY = int(os.environ.get("DOCFLOW_COMPANY_ID", "1"))


def _rows(sql: str, company: int | None = None) -> list[dict[str, Any]]:
    tenant = DEFAULT_COMPANY if company is None else company
    try:
        with db.tenant(tenant) as connection, connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error


@router.get("/companies")
def companies() -> list[dict[str, Any]]:
    """The platform layer: which companies exist. Not filtered by any policy."""
    return _rows("SELECT id, name, slug FROM companies ORDER BY id", company=1)


@router.get("/summary")
def summary(company: int = Query(DEFAULT_COMPANY, ge=1)) -> dict[str, Any]:
    """Supplier totals, document counts and the manual-edit marker."""
    suppliers = _rows(
        """
        SELECT s.name AS supplier,
               count(d.id) AS documents,
               coalesce(sum(d.total), 0) AS total
        FROM suppliers s
        LEFT JOIN documents d ON d.supplier_id = s.id
        GROUP BY s.name
        ORDER BY total DESC
        """,
        company,
    )
    stats = _rows(
        """
        SELECT count(*) AS documents,
               count(*) FILTER (WHERE edited_fields IS NOT NULL) AS edited
        FROM documents
        """,
        company,
    )[0]
    types = _rows(
        """
        SELECT doc_type, count(*) AS documents, coalesce(sum(total), 0) AS total
        FROM documents
        GROUP BY doc_type
        ORDER BY total DESC
        """,
        company,
    )
    return {"suppliers": suppliers, "stats": stats, "types": types}


def _total(value: Any) -> str:
    """An amount for the table: grouped with spaces, never a locale surprise."""
    try:
        return esc(f"{float(value or 0):,.0f}".replace(",", " "))
    except (TypeError, ValueError):
        return esc(value)


@router.get("/", response_class=HTMLResponse)
def page(company: int = Query(DEFAULT_COMPANY, ge=1)) -> str:
    """One readable page with the same numbers, for the browser."""
    data = summary(company)
    known = companies()
    current = next((item for item in known if item['id'] == company), None)
    label = current['name'] if current else f'company {company}'
    switcher = ' · '.join(
        f'<a href="?company={int(item["id"])}">{esc(item["name"])}</a>'
        if item['id'] != company
        else f'<strong>{esc(item["name"])}</strong>'
        for item in known
    )
    rows = "".join(
        f"<tr><td>{shown(item['supplier'])}</td><td>{esc(item['documents'])}</td>"
        f"<td>{_total(item['total'])}</td></tr>"
        for item in data["suppliers"]
    )
    types = "".join(
        f"<tr><td>{shown(item['doc_type'])}</td><td>{esc(item['documents'])}</td>"
        f"<td>{_total(item['total'])}</td></tr>"
        for item in data["types"]
    )
    stats = data["stats"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>DocFlow · PostgreSQL report</title>
<style>
body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:40px;background:#faf9f7;color:#1d1b1a}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b6560;margin-bottom:28px}}
table{{border-collapse:collapse;width:100%;max-width:720px;margin-bottom:32px}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid #e8e4df;font-size:14px}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b6560}}
td:nth-child(2),td:nth-child(3),th:nth-child(2),th:nth-child(3){{text-align:right}}
.pill{{display:inline-block;background:#efece7;border-radius:999px;padding:2px 10px;font-size:12px;margin-left:6px}}
</style></head><body>
<h1>PostgreSQL report</h1>
<div class="sub">{esc(label)} · {esc(stats['documents'])} documents, {esc(stats['edited'])} edited by a human<br><span class="pill">tenant</span> {switcher}</div>
<table><tr><th>Supplier</th><th>Documents</th><th>Total, UAH</th></tr>{rows}</table>
<table><tr><th>Document type</th><th>Documents</th><th>Total, UAH</th></tr>{types}</table>
</body></html>"""
