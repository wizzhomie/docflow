import csv
import io
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from extractor import extract_text, parse_invoice, parse_items

BASE = Path(__file__).parent
SAMPLES = BASE / "samples"
UPLOADS = BASE / "data" / "uploads"
PENDING = BASE / "data" / "pending"
CONFIRMED = BASE / "data" / "confirmed.jsonl"
for directory in (UPLOADS, PENDING):
    directory.mkdir(parents=True, exist_ok=True)

ALLOWED = {".pdf", ".txt", ".html", ".htm"}
MANUAL_MINUTES = 3.0
ASSISTED_MINUTES = 0.5

app = FastAPI(title="DocFlow")


class ConfirmPayload(BaseModel):
    id: str
    fields: dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_confirmed() -> list[dict]:
    if not CONFIRMED.exists():
        return []
    rows = []
    for line in CONFIRMED.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _meta_path(doc_id: str) -> Path:
    return PENDING / f"{doc_id}.json"


def _process(doc_id: str, filename: str, path: Path) -> dict:
    text = extract_text(path)
    parsed = parse_invoice(text)
    items = parse_items(text)
    meta = {
        "id": doc_id,
        "filename": filename,
        "uploaded_at": _now(),
        "parsed": parsed,
        "items": items,
        "text_preview": text[:4000],
        "suffix": path.suffix.lower(),
    }
    _meta_path(doc_id).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


@app.get("/")
def index():
    return FileResponse(BASE / "templates" / "index.html")


@app.get("/api/samples")
def samples():
    if not SAMPLES.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(SAMPLES.glob("*.pdf"))]


@app.post("/api/samples/{name}")
def load_sample(name: str):
    source = SAMPLES / name
    if not source.exists() or source.suffix.lower() != ".pdf":
        raise HTTPException(404, "Sample not found")
    doc_id = uuid.uuid4().hex[:10]
    target = UPLOADS / f"{doc_id}.pdf"
    shutil.copyfile(source, target)
    return _process(doc_id, name, target)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "doc").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "Поддерживаются PDF, TXT и HTML")
    doc_id = uuid.uuid4().hex[:10]
    target = UPLOADS / f"{doc_id}{suffix}"
    with target.open("wb") as out:
        while chunk := await file.read(1024 * 256):
            out.write(chunk)
    meta = _process(doc_id, file.filename or target.name, target)
    if not meta["text_preview"].strip():
        meta["warning"] = "Текст не извлечён: возможно, это скан. OCR пока не подключён."
    return meta


@app.post("/api/confirm")
def confirm(payload: ConfirmPayload):
    meta_path = _meta_path(payload.id)
    if not meta_path.exists():
        raise HTTPException(404, "Документ не найден")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    parsed_values = {k: v["value"] for k, v in meta["parsed"]["fields"].items()}
    edited = [k for k, v in payload.fields.items() if parsed_values.get(k) != v]

    record = {
        "id": payload.id,
        "filename": meta["filename"],
        "uploaded_at": meta["uploaded_at"],
        "confirmed_at": _now(),
        "fields": payload.fields,
        "parsed": parsed_values,
        "edited_fields": edited,
        "confidence": meta["parsed"]["confidence"],
    }
    with CONFIRMED.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"ok": True, "edited_fields": edited, "record": record}


@app.get("/api/documents")
def documents(limit: int = 50):
    return list(reversed(_read_confirmed()))[:limit]


@app.get("/api/stats")
def stats():
    rows = _read_confirmed()
    total = len(rows)
    edited = sum(len(r["edited_fields"]) for r in rows)
    field_count = max(total * 6, 1)
    avg_conf = round(sum(r["confidence"] for r in rows) / total, 2) if total else 0.0
    return {
        "documents": total,
        "edited_fields": edited,
        "accuracy": round(max(0.0, 1 - edited / field_count) * 100, 1) if total else 0.0,
        "avg_confidence": avg_conf,
        "saved_minutes": round(total * (MANUAL_MINUTES - ASSISTED_MINUTES), 1),
        "total_amount": round(sum((r["fields"].get("total") or 0) for r in rows), 2),
    }


@app.get("/api/export.csv")
def export_csv():
    rows = _read_confirmed()
    columns = ["id", "filename", "confirmed_at", "number", "date", "supplier", "inn", "kpp", "total", "vat", "currency"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(columns)
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col)
            if value is None:
                value = row["fields"].get(col, "")
            values.append(value)
        writer.writerow(values)
    buffer.seek(0)
    name = f"docflow_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/files/{doc_id}")
def document_file(doc_id: str):
    for candidate in UPLOADS.glob(f"{doc_id}.*"):
        if candidate.suffix != ".json":
            return FileResponse(candidate)
    raise HTTPException(404, "Not found")


@app.get("/api/health")
def health():
    return {"status": "ok", "time": _now(), "ts": time.time()}
