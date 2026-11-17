"""Read-only reports from the practice PostgreSQL database.

Pilot wiring: the review workflow still runs on files; these endpoints answer
questions straight from the `docflow_dev` schema so the two can be compared.
Connection uses the OS user via peer authentication on the local socket.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg2
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/api/pg", tags=["pg"])
DSN = os.environ.get("DOCFLOW_PG_DSN", "dbname=docflow_dev")


def _rows(sql: str) -> list[dict[str, Any]]:
    try:
        with psycopg2.connect(DSN) as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql)
                return [dict(row) for row in cursor.fetchall()]
    except psycopg2.Error as error:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error


@router.get("/summary")
def summary() -> dict[str, Any]:
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
        """
    )
    stats = _rows(
        """
        SELECT count(*) AS documents,
               count(*) FILTER (WHERE edited_fields IS NOT NULL) AS edited
        FROM documents
        """
    )[0]
    types = _rows(
        """
        SELECT doc_type, count(*) AS documents, coalesce(sum(total), 0) AS total
        FROM documents
        GROUP BY doc_type
        ORDER BY total DESC
        """
    )
    return {"suppliers": suppliers, "stats": stats, "types": types}


@router.get("/", response_class=HTMLResponse)
def page() -> str:
    """One readable page with the same numbers, for the browser."""
    data = summary()
    rows = "".join(
        f"<tr><td>{item['supplier']}</td><td>{item['documents']}</td>"
        f"<td>{float(item['total']):,.0f}</td></tr>".replace(",", " ")
        for item in data["suppliers"]
    )
    types = "".join(
        f"<tr><td>{item['doc_type']}</td><td>{item['documents']}</td>"
        f"<td>{float(item['total']):,.0f}</td></tr>".replace(",", " ")
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
<div class="sub">docflow_dev · {stats['documents']} documents, {stats['edited']} edited by a human</div>
<table><tr><th>Supplier</th><th>Documents</th><th>Total, UAH</th></tr>{rows}</table>
<table><tr><th>Document type</th><th>Documents</th><th>Total, UAH</th></tr>{types}</table>
</body></html>"""
