"""Load the demo files into the practice PostgreSQL schema.

Reads data/pending/*.json and data/confirmed.jsonl, writes staging CSVs and a
SQL script that fills suppliers, documents, document_items, deals and
document_events in docflow_dev. Run from the project root:

    python3 tools/import_demo_to_pg.py && psql -d docflow_dev -f /tmp/import_pg/import.sql
"""

from __future__ import annotations

import csv
import glob
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("/tmp/import_pg")
OUT.mkdir(exist_ok=True)


def value(field):
    """Pending fields look like {"value": ..., "confidence": ...}; history is flat."""
    return field.get("value") if isinstance(field, dict) else field


def doc_type_from_filename(name: str) -> str:
    if "invoice" in name:
        return "invoice_ua"
    if "act" in name:
        return "act_ua"
    if "delivery" in name:
        return "delivery_ua"
    return "unknown"


def main() -> None:
    pairs: list[tuple[str, str]] = []
    documents: list[dict] = []
    items: list[dict] = []
    deals: list[dict] = []
    events: list[dict] = []

    for path in sorted(glob.glob(str(ROOT / "data/pending/*.json"))):
        doc = json.loads(Path(path).read_text())
        parsed = doc.get("parsed", {})
        fields = parsed.get("fields", {})
        name = value(fields.get("supplier"))
        tax = value(fields.get("tax_id"))
        if not name:
            continue
        pairs.append((name, tax or ""))
        key = doc["id"]
        documents.append({
            "import_key": key,
            "supplier_name": name,
            "supplier_tax": tax or "",
            "doc_type": parsed.get("type") or doc_type_from_filename(doc.get("filename", "")),
            "number": value(fields.get("number")),
            "doc_date": value(fields.get("date")),
            "received_at": doc.get("uploaded_at"),
            "total": value(fields.get("total")),
            "vat": value(fields.get("vat")),
            "currency": value(fields.get("currency")) or "UAH",
            "status": "new",
            "file_path": f"data/uploads/{key}.pdf",
            "parsed": json.dumps(parsed, ensure_ascii=False),
            "edited_fields": "",
            "edited_at": "",
            "edited_by": "",
        })
        for index, item in enumerate(doc.get("items") or [], start=1):
            items.append({
                "doc_key": key,
                "position_no": index,
                "name": item.get("name"),
                "unit": item.get("unit"),
                "qty": item.get("qty"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "vat_rate": item.get("vat_rate"),
            })
        events.append({
            "doc_key": key,
            "event_type": "uploaded",
            "actor": doc.get("source"),
            "payload": json.dumps({"file": doc.get("filename")}, ensure_ascii=False),
            "created_at": doc.get("uploaded_at"),
        })

    for line in (ROOT / "data/confirmed.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        fields = rec.get("fields") or {}
        name = fields.get("supplier")
        tax = fields.get("tax_id")
        if not name:
            continue
        pairs.append((name, tax or ""))
        key = rec["id"]
        edited = rec.get("edited_fields") or []
        documents.append({
            "import_key": key,
            "supplier_name": name,
            "supplier_tax": tax or "",
            "doc_type": doc_type_from_filename(rec.get("filename", "")),
            "number": fields.get("number"),
            "doc_date": fields.get("date"),
            "received_at": rec.get("uploaded_at"),
            "total": fields.get("total"),
            "vat": fields.get("vat"),
            "currency": fields.get("currency") or "UAH",
            "status": "confirmed",
            "file_path": f"data/history/{rec.get('filename')}",
            "parsed": json.dumps({
                "fields": fields,
                "issues": rec.get("issues") or [],
                "confidence": rec.get("confidence"),
            }, ensure_ascii=False),
            "edited_fields": "{" + ",".join(edited) + "}" if edited else "",
            "edited_at": rec.get("confirmed_at") if edited else "",
            "edited_by": "",
        })
        if rec.get("crm_deal_id"):
            deals.append({
                "doc_key": key,
                "status": "done",
                "tech_status": "created",
                "external_id": rec["crm_deal_id"],
                "attempts": 1,
                "last_error": "",
                "created_at": rec.get("confirmed_at"),
                "updated_at": rec.get("confirmed_at"),
            })
        events.append({
            "doc_key": key,
            "event_type": "confirmed",
            "actor": "reviewer",
            "payload": json.dumps({
                "issues": rec.get("issues") or [],
                "review_seconds": rec.get("review_seconds"),
            }, ensure_ascii=False),
            "created_at": rec.get("confirmed_at"),
        })

    # The synthetic history reuses tax IDs across different names, so a tax ID is
    # kept only when it is unambiguous both ways; otherwise the supplier is known
    # by name and the tax ID is unknown.
    taxes_of: dict[str, set] = {}
    names_of_tax: dict[str, set] = {}
    for name, tax in pairs:
        if tax:
            taxes_of.setdefault(name, set()).add(tax)
            names_of_tax.setdefault(tax, set()).add(name)
    suppliers = {
        name: {
            "name": name,
            "tax_id": (next(iter(taxes_of[name]))
                       if len(taxes_of.get(name, ())) == 1
                       and len(names_of_tax.get(next(iter(taxes_of[name])), ())) == 1
                       else ""),
        }
        for name, _ in pairs
    }

    def write_csv(name: str, rows: list[dict], fields: list[str]) -> None:
        with open(OUT / name, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in fields})

    write_csv("suppliers.csv", list(suppliers.values()), ["name", "tax_id"])
    write_csv("documents.csv", documents, [
        "import_key", "supplier_name", "supplier_tax", "doc_type", "number", "doc_date",
        "received_at", "total", "vat", "currency", "status", "file_path", "parsed",
        "edited_fields", "edited_at", "edited_by",
    ])
    write_csv("items.csv", items, [
        "doc_key", "position_no", "name", "unit", "qty", "price", "amount", "vat_rate",
    ])
    write_csv("deals.csv", deals, [
        "doc_key", "status", "tech_status", "external_id", "attempts", "last_error",
        "created_at", "updated_at",
    ])
    write_csv("events.csv", events, ["doc_key", "event_type", "actor", "payload", "created_at"])

    print(
        f"suppliers {len(suppliers)}, documents {len(documents)}, items {len(items)}, "
        f"deals {len(deals)}, events {len(events)} -> {OUT}"
    )


if __name__ == "__main__":
    main()
