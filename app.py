import asyncio
import csv
import io
import json
import os
import shutil
import statistics
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import mailbox
import profiles
import routing
import uspacy
from connectors import active as active_connector
from connectors import provider_name
from extractor import extract_text, parse_invoice, parse_items

BASE = Path(__file__).parent
SAMPLES = BASE / 'samples'
UPLOADS = BASE / 'data' / 'uploads'
PENDING = BASE / 'data' / 'pending'
INBOX = BASE / 'data' / 'inbox'
INBOX_DONE = INBOX / 'processed'
CONFIRMED = BASE / 'data' / 'confirmed.jsonl'
ROADMAP = BASE / 'docs' / 'roadmap.json'


def _html(name: str) -> FileResponse:
    """Serve a page from templates/ without caching: the UI ships new fields
    with every change, and a stale copy silently drops them from the confirm
    payload."""
    return FileResponse(
        BASE / 'templates' / name,
        headers={'Cache-Control': 'no-store, must-revalidate'},
    )
for directory in (UPLOADS, PENDING, INBOX, INBOX_DONE):
    directory.mkdir(parents=True, exist_ok=True)

ALLOWED = {
    '.pdf',
    '.txt',
    '.html',
    '.htm',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.tif',
    '.tiff',
    '.bmp',
}
MANUAL_MINUTES = 3.0
ASSISTED_MINUTES = 0.5

# Manual baseline used by the impact report. It is an explicit assumption, not a
# measurement: the operator states how long one invoice took before DocFlow.
MANUAL_BASELINE_MINUTES = float(os.environ.get('DOCFLOW_MANUAL_MINUTES', MANUAL_MINUTES))

# Demo mode keeps a client demonstration away from the real CRM: Confirm returns
# a simulated deal instead of creating one in Uspacy.
DEMO_MODE = os.environ.get('DOCFLOW_DEMO_MODE', '').strip().lower() in {'1', 'true', 'yes', 'on'}

FIELD_LABELS = {
    'number': 'Invoice number',
    'date': 'Date',
    'supplier': 'Supplier',
    'tax_id': 'Tax ID',
    'total': 'Amount due',
    'vat': 'VAT',
    'currency': 'Currency',
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. The retry queue belongs to the Uspacy connector only.
    if provider_name() == 'uspacy':
        uspacy.start_retry_worker()
    watcher = asyncio.create_task(_folder_watcher())
    mail_watcher = asyncio.create_task(_mailbox_worker())
    try:
        yield
    finally:
        watcher.cancel()
        mail_watcher.cancel()
        uspacy.stop_retry_worker()


app = FastAPI(title='DocFlow', lifespan=lifespan)


class ConfirmPayload(BaseModel):
    id: str
    fields: dict


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _read_confirmed() -> list[dict]:
    if not CONFIRMED.exists():
        return []
    rows = []
    for line in CONFIRMED.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _meta_path(doc_id: str) -> Path:
    return PENDING / f'{doc_id}.json'


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _review_seconds(row: dict) -> float | None:
    """Seconds between upload and confirmation for one record.

    Older records predate the stored `review_seconds` field, so the value is
    derived from the two timestamps instead of re-measuring anything.
    """
    stored = row.get('review_seconds')
    if isinstance(stored, (int, float)):
        return max(0.0, float(stored))
    started = _parse_ts(row.get('uploaded_at'))
    finished = _parse_ts(row.get('confirmed_at'))
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


_PENDING_ISSUES_CACHE: dict[str, tuple[float, list[str]]] = {}


def _issues_for(doc_id: str | None) -> list[str]:
    """Validation issues recorded before confirms started storing them.

    Legacy confirmations have no `issues` field, so the pending snapshot of the
    same document is consulted. The cache is keyed by file mtime so an updated
    snapshot is never reported from stale bytes.
    """
    if not doc_id:
        return []
    path = _meta_path(doc_id)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _PENDING_ISSUES_CACHE.get(doc_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        meta = json.loads(path.read_text(encoding='utf-8'))
        issues = list((meta.get('parsed') or {}).get('issues') or [])
    except (OSError, ValueError):
        issues = []
    _PENDING_ISSUES_CACHE[doc_id] = (mtime, issues)
    return issues


def _row_issues(row: dict) -> list[str]:
    issues = row.get('issues')
    if issues is None:
        return _issues_for(row.get('id'))
    return list(issues)


def _dedupe_key(fields: dict) -> tuple[str, str, str] | None:
    """Identity of one invoice for duplicate suppression.

    Number alone is not unique (monthly invoices reuse it), so the key is
    number + party + date. A missing component means the invoice cannot be
    matched safely, and no suppression happens.
    """
    number = str(fields.get('number') or '').strip().lower()
    party = str(fields.get('tax_id') or fields.get('supplier') or '').strip().lower()
    issued = str(fields.get('date') or '').strip()
    if not number or not party or not issued:
        return None
    return (number, party, issued)


def _local_duplicate(fields: dict, rows: list[dict]) -> dict | None:
    """Find an already confirmed document with the same invoice identity."""
    key = _dedupe_key(fields)
    if key is None:
        return None
    for row in reversed(rows):
        if _dedupe_key(row.get('fields') or {}) == key:
            return row
    return None


def build_impact(rows: list[dict], manual_minutes: float = MANUAL_BASELINE_MINUTES) -> dict:
    """Aggregate confirmed records into an automation-impact report.

    Every number is derived from stored records; the only assumption is the
    manual baseline (`manual_minutes`), which is returned to the caller so the
    UI can show it instead of hiding it. Nothing here re-runs extraction, so
    the report never disagrees with the reviewed documents.
    """
    documents = len(rows)
    ids = {row.get('id') for row in rows if row.get('id')}
    duplicates = documents - len(ids)

    edited_rows = [row for row in rows if row.get('edited_fields')]
    edited_fields = sum(len(row.get('edited_fields') or []) for row in rows)

    corrected: Counter[str] = Counter()
    for row in rows:
        for field in row.get('edited_fields') or []:
            corrected[field] += 1
    top_corrections = [
        {'field': field, 'label': FIELD_LABELS.get(field, field), 'count': count}
        for field, count in corrected.most_common()
    ]

    reviews = [seconds for row in rows if (seconds := _review_seconds(row)) is not None]
    average_review = sum(reviews) / len(reviews) if reviews else 0.0
    median_review = statistics.median(reviews) if reviews else 0.0
    saved_minutes = sum(max(0.0, manual_minutes - seconds / 60) for seconds in reviews)

    tripped: Counter[str] = Counter()
    documents_with_issues = 0
    unreviewed_risk = 0
    for row in rows:
        issues = _row_issues(row)
        if not issues:
            continue
        documents_with_issues += 1
        for issue in issues:
            tripped[issue] += 1
        if not row.get('edited_fields'):
            unreviewed_risk += 1

    synthetic = sum(1 for row in rows if row.get('source') == 'synthetic')

    stamps = [stamp for row in rows if (stamp := _parse_ts(row.get('confirmed_at')))]
    period = {
        'from': min(stamps).isoformat(timespec='seconds') if stamps else None,
        'to': max(stamps).isoformat(timespec='seconds') if stamps else None,
    }

    return {
        'documents': documents,
        'unique_documents': len(ids),
        'duplicate_confirmations': duplicates,
        'auto_pass_documents': documents - len(edited_rows),
        'auto_pass_rate': round((documents - len(edited_rows)) / documents * 100, 1)
        if documents
        else 0.0,
        'edited_documents': len(edited_rows),
        'edited_fields': edited_fields,
        'field_accuracy': round(max(0.0, 1 - edited_fields / max(documents * 7, 1)) * 100, 1),
        'review': {
            'measured_documents': len(reviews),
            'average_seconds': round(average_review, 1),
            'median_seconds': round(median_review, 1),
        },
        'time_saved_minutes': round(saved_minutes, 1),
        'assumptions': {
            'manual_minutes_per_document': manual_minutes,
            'source': 'DOCFLOW_MANUAL_MINUTES',
        },
        'top_corrections': top_corrections,
        'validation': {
            'documents_with_issues': documents_with_issues,
            'unreviewed_risk_documents': unreviewed_risk,
            'top': [{'issue': issue, 'count': count} for issue, count in tripped.most_common()],
        },
        'sources': {
            'synthetic': synthetic,
            'real': documents - synthetic,
            'mixed': 0 < synthetic < documents,
        },
        'period': period,
    }


def _document_title(fields: dict, doc_title: str | None = None) -> str:
    # Unknown legacy documents fall back to a neutral English word, never to a
    # language of one market.
    prefix = doc_title or 'Document'
    return f'{prefix} №{fields.get("number") or "—"} — {fields.get("supplier") or "Document"}'


def _deal_comment(
    fields: dict,
    doc_id: str | None = None,
    route: dict | None = None,
    doc_type: str | None = None,
) -> str:
    parts = ['Imported by DocFlow']
    if fields.get('supplier'):
        parts.append(f'Supplier: {fields["supplier"]}')
    if fields.get('tax_id'):
        parts.append(f'Tax ID: {fields["tax_id"]}')
    if fields.get('date'):
        parts.append(f'Invoice date: {fields["date"]}')
    if fields.get('vat') is not None:
        currency = fields.get('currency') or profiles.default_currency(doc_type)
        parts.append(f'VAT: {fields["vat"]} {currency}')
    if fields.get('address'):
        parts.append(f'Address: {fields["address"]}')
    if fields.get('phone'):
        parts.append(f'Phone: {fields["phone"]}')
    if fields.get('email'):
        parts.append(f'Email: {fields["email"]}')
    if fields.get('bank'):
        parts.append(f'Bank: {fields["bank"]}')
    if fields.get('iban'):
        parts.append(f'IBAN: {fields["iban"]}')
    if fields.get('contact_person'):
        parts.append(f'Contact: {fields["contact_person"]}')
    if route and route.get('destination'):
        parts.append(f"Routed to: {route['destination']} ({route.get('reason', '')})")
    if doc_id:
        parts.append(f'Source file: /files/{doc_id}')
    return '. '.join(parts) + '.'


def _supplier_details(fields: dict, doc_type: str | None = None) -> dict:
    """Map document fields onto the Uspacy company card and its comment block."""
    labels = profiles.comment_labels(doc_type)
    comment_lines = []
    if fields.get('tax_id'):
        comment_lines.append(f'{labels["tax_id"]}: {fields["tax_id"]}')
    if fields.get('iban'):
        comment_lines.append(f'IBAN: {fields["iban"]}')
    if fields.get('bank'):
        comment_lines.append(f'{labels["bank"]}: {fields["bank"]}')
    return {
        'tax_id': fields.get('tax_id'),
        'address': fields.get('address'),
        'phone': fields.get('phone'),
        'email': fields.get('email'),
        'bank': fields.get('bank'),
        'iban': fields.get('iban'),
        'comment_lines': comment_lines,
    }


def _find_or_create_supplier_company(fields: dict, doc_type: str | None = None) -> dict | None:
    """Find or create the supplier company and fill its card from the document."""
    supplier = fields.get('supplier')
    if not supplier:
        return None
    return uspacy.find_or_create_company(supplier, _supplier_details(fields, doc_type))


def _find_or_create_supplier_contact(fields: dict, company: dict | None) -> dict | None:
    """Supplier contact person, linked to the company and later to the deal."""
    name = fields.get('contact_person')
    if not name:
        return None
    return uspacy.find_or_create_contact(
        name,
        (company or {}).get('id'),
        phone=fields.get('phone'),
        email=fields.get('email'),
    )


def _find_existing_deal(fields: dict) -> dict | None:
    """Check whether the active CRM already holds a deal for this document."""
    connector = active_connector()
    if connector is None or not connector.configured():
        return None
    try:
        return connector.existing_deal(fields)
    except Exception:
        return None


def _effective_fields(parsed_values: dict, submitted: dict) -> dict:
    """What the reviewer confirmed: parsed defaults, overridden by the form.

    A missing key means the form did not show that field (an older cached UI, or
    a document type without it), so the parsed value must survive instead of
    silently disappearing from the history and the CRM payload.
    """
    return {**parsed_values, **{key: value for key, value in submitted.items() if value not in (None, '')}}


def _ingest_folder_once() -> list[dict]:
    """Pick up documents dropped into the intake folder.

    A file younger than two seconds is skipped: a copy that is still being
    written must not be parsed half-done. Handled files are moved aside so a
    restart does not process them twice.
    """
    handled: list[dict] = []
    now = time.time()
    for path in sorted(INBOX.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED:
            continue
        try:
            if now - path.stat().st_mtime < 2:
                continue
            doc_id = uuid.uuid4().hex[:10]
            target = UPLOADS / f'{doc_id}{path.suffix.lower()}'
            shutil.copyfile(path, target)
            _process(doc_id, path.name, target, source='folder')
            path.replace(INBOX_DONE / f'{path.stem}-{doc_id}{path.suffix.lower()}')
            handled.append({'id': doc_id, 'filename': path.name})
        except Exception:
            # A broken file must not stop the folder from being watched.
            continue
    return handled


async def _folder_watcher() -> None:
    while True:
        try:
            _ingest_folder_once()
        except Exception:
            pass
        await asyncio.sleep(3)


def _mailbox_poll_once() -> dict:
    """Take the attachments of new mail through the same path as an upload.

    A document that arrives by e-mail is not special: it is recognised, put in
    the review queue and routed exactly like a dropped file. The only difference
    is the provenance kept in the meta file.
    """
    def handler(attachment: dict, message: dict):
        suffix = Path(attachment['filename']).suffix.lower()
        if suffix not in ALLOWED:
            return None
        doc_id = uuid.uuid4().hex[:10]
        target = UPLOADS / f'{doc_id}{suffix}'
        target.write_bytes(attachment['data'])
        _process(
            doc_id,
            attachment['filename'],
            target,
            source='mailbox',
            extra={'email': {
                'from': message.get('from'),
                'subject': message.get('subject'),
                'date': message.get('date'),
                'message_id': message.get('message_id'),
            }},
        )
        return True

    return mailbox.poll(handler)


async def _mailbox_worker() -> None:
    # The IMAP client is blocking; keep it out of the event loop.
    while True:
        try:
            await asyncio.to_thread(_mailbox_poll_once)
        except Exception:
            pass
        await asyncio.sleep(mailbox.settings()['interval'])


def _pending_documents(limit: int = 20) -> list[dict]:
    """Uploaded documents that are still waiting for a decision.

    Confirming does not delete the pending snapshot, so the history is what
    separates "waiting for review" from "already saved".
    """
    confirmed_ids = {row.get('id') for row in _read_confirmed()}
    items = []
    for path in PENDING.glob('*.json'):
        doc_id = path.stem
        if doc_id in confirmed_ids:
            continue
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        parsed = meta.get('parsed') or {}
        items.append(
            {
                'id': doc_id,
                'filename': meta.get('filename'),
                'uploaded_at': meta.get('uploaded_at'),
                'suffix': meta.get('suffix'),
                'source': meta.get('source', 'upload'),
                'issues': list(parsed.get('issues') or []),
                'confidence': parsed.get('confidence'),
                'doc_type': parsed.get('type_label'),
                'route': routing.route(parsed.get('type'), {k: v.get('value') for k, v in (parsed.get('fields') or {}).items()}),
                'fields': {k: v.get('value') for k, v in (parsed.get('fields') or {}).items()},
            }
        )
    items.sort(key=lambda item: item.get('uploaded_at') or '', reverse=True)
    return items[:limit]


def _process(doc_id: str, filename: str, path: Path, source: str = 'upload',
             extra: dict | None = None) -> dict:
    text = extract_text(path)
    items = parse_items(text)
    # First pass to get tax_id/supplier for supplier profile
    parsed_preview = parse_invoice(text, items=items)
    preview_tax_id = parsed_preview['fields'].get('tax_id', {}).get('value')
    preview_supplier = parsed_preview['fields'].get('supplier', {}).get('value')
    # Second pass with supplier profile
    parsed = parse_invoice(text, items=items, tax_id=preview_tax_id, supplier_name=preview_supplier)
    meta = {
        'id': doc_id,
        'filename': filename,
        'uploaded_at': _now(),
        'parsed': parsed,
        'items': items,
        'text_preview': text[:4000],
        'suffix': path.suffix.lower(),
        'source': source,
    }
    if extra:
        meta.update(extra)
    _meta_path(doc_id).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return meta


@app.get('/')
def landing():
    # The showcase page explains the product; the working review UI lives at /app.
    return _html('landing.html')


@app.get('/app')
def index():
    # The UI ships new fields with every change; a stale cached copy silently
    # drops them from the confirm payload, so it must not be cached.
    return _html('index.html')


@app.get('/api/samples')
def samples():
    if not SAMPLES.exists():
        return []
    return [{'name': p.name, 'size': p.stat().st_size} for p in sorted(SAMPLES.glob('*.pdf'))]


@app.post('/api/samples/{name}')
def load_sample(name: str):
    source = SAMPLES / name
    if not source.exists() or source.suffix.lower() != '.pdf':
        raise HTTPException(404, 'Sample not found')
    doc_id = uuid.uuid4().hex[:10]
    target = UPLOADS / f'{doc_id}.pdf'
    shutil.copyfile(source, target)
    return _process(doc_id, name, target, source='sample')


@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or 'doc').suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, 'Supported formats: PDF, TXT, HTML and images (PNG/JPG/TIFF)')
    doc_id = uuid.uuid4().hex[:10]
    target = UPLOADS / f'{doc_id}{suffix}'
    with target.open('wb') as out:
        while chunk := await file.read(1024 * 256):
            out.write(chunk)
    meta = _process(doc_id, file.filename or target.name, target)
    if not meta['text_preview'].strip():
        meta['warning'] = 'No text extracted: the file may be a scan. OCR is not enabled yet.'
    return meta


@app.post('/api/confirm')
def confirm(payload: ConfirmPayload):
    meta_path = _meta_path(payload.id)
    if not meta_path.exists():
        raise HTTPException(404, 'Document not found')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    parsed_values = {k: v['value'] for k, v in meta['parsed']['fields'].items()}
    fields = _effective_fields(parsed_values, payload.fields)
    doc_title = (meta.get('parsed') or {}).get('doc_title')
    doc_type = (meta.get('parsed') or {}).get('type_label')
    profile_code = (meta.get('parsed') or {}).get('type')
    route_info = routing.route((meta.get('parsed') or {}).get('type'), fields)
    edited = [k for k, v in payload.fields.items() if parsed_values.get(k) != v]

    # Duplicate suppression: a local history match needs no CRM call, and it
    # works when the CRM is unreachable or disabled.
    history = _read_confirmed()
    local_duplicate = _local_duplicate(fields, history)
    connector = active_connector()
    crm: dict = {
        'configured': bool(connector and connector.configured()) and not DEMO_MODE,
        'provider': connector.name if connector else 'none',
        'demo': DEMO_MODE,
    }

    if local_duplicate is not None:
        crm['deal'] = {
            'id': local_duplicate.get('crm_deal_id') or f"HISTORY-{local_duplicate.get('id')}",
            'title': f"Already confirmed: {local_duplicate.get('filename')}",
        }
        crm['duplicate'] = True
        crm['duplicate_source'] = 'history'
    elif DEMO_MODE:
        # Nothing leaves the host during a demonstration.
        crm['deal'] = {
            'id': f'DEMO-{payload.id}',
            'title': _document_title(fields, doc_title),
        }
        crm['simulated'] = True
        crm['route'] = route_info
    else:
        existing_deal = _find_existing_deal(fields)
        if existing_deal:
            crm['deal'] = {'id': existing_deal.get('id'), 'title': existing_deal.get('title')}
            crm['duplicate'] = True
            crm['duplicate_source'] = 'crm'
        elif crm['configured']:
            company = contact = None
            company = contact = None
            try:
                company = connector.ensure_company(fields, doc_type=profile_code)
            except Exception as exc:
                # A failed enrichment must be visible, not a silently empty card.
                crm['company_error'] = str(exc)
            try:
                contact = connector.ensure_contact(fields, company)
            except Exception as exc:
                crm['contact_error'] = str(exc)
            try:
                deal = connector.create_deal(
                    title=_document_title(fields, doc_title),
                    fields=fields,
                    company=company,
                    contact=contact,
                    doc_id=payload.id,
                    route=route_info,
                    doc_type=profile_code,
                )
                crm['deal'] = {'id': deal.get('id'), 'title': deal.get('title')}
                crm['route'] = route_info
                crm['company'] = {'id': (company or {}).get('id'), 'title': (company or {}).get('title')}
                crm['contact'] = {'id': (contact or {}).get('id'), 'title': (contact or {}).get('title')}
            except Exception as exc:
                crm['error'] = str(exc)

    confirmed_at = _now()
    review_seconds = _review_seconds(
        {'uploaded_at': meta['uploaded_at'], 'confirmed_at': confirmed_at}
    )
    record = {
        'id': payload.id,
        'filename': meta['filename'],
        'uploaded_at': meta['uploaded_at'],
        'confirmed_at': confirmed_at,
        'review_seconds': int(review_seconds or 0),
        'fields': fields,
        'parsed': parsed_values,
        'edited_fields': edited,
        'confidence': meta['parsed']['confidence'],
        'issues': list(meta['parsed'].get('issues') or []),
        'crm_deal_id': (crm.get('deal') or {}).get('id'),
        'doc_type': doc_type,
        'destination': route_info['destination'],
        'destination_code': route_info['destination_code'],
        'route_reason': route_info['reason'],
        'source': meta.get('source', 'upload'),
    }
    with CONFIRMED.open('a', encoding='utf-8') as out:
        out.write(json.dumps(record, ensure_ascii=False) + '\n')

    return {'ok': True, 'edited_fields': edited, 'record': record, 'crm': crm}


@app.get('/api/inbox')
def inbox(limit: int = 20):
    return _pending_documents(limit)


@app.get('/api/intake')
def intake_state():
    files = sorted(p.name for p in INBOX.iterdir() if p.is_file()) if INBOX.exists() else []
    return {
        'folder': str(INBOX.relative_to(BASE)),
        'waiting': files,
        'processed': len(list(INBOX_DONE.glob('*'))) if INBOX_DONE.exists() else 0,
    }


@app.post('/api/intake/scan')
def intake_scan():
    return {'ingested': _ingest_folder_once()}


@app.get('/api/mailbox')
def mailbox_state():
    return mailbox.status()


@app.post('/api/mailbox/check')
async def mailbox_check():
    result = await asyncio.to_thread(_mailbox_poll_once)
    return {**mailbox.status(), 'last_poll': result}


@app.get('/api/documents/{doc_id}')
def document_meta(doc_id: str):
    path = _meta_path(doc_id)
    if not path.exists():
        raise HTTPException(404, 'Document not found')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/api/documents')
def documents(limit: int = 50):
    rows = list(reversed(_read_confirmed()))[:limit]
    # Surface the measured review time per document; legacy rows derive it from
    # the stored timestamps instead of showing an empty cell.
    return [{**row, 'review_seconds': _review_seconds(row)} for row in rows]


@app.get('/api/stats')
def stats():
    rows = _read_confirmed()
    total = len(rows)
    edited = sum(len(r['edited_fields']) for r in rows)
    field_count = max(total * 6, 1)
    avg_conf = round(sum(r['confidence'] for r in rows) / total, 2) if total else 0.0
    return {
        'documents': total,
        'edited_fields': edited,
        'accuracy': round(max(0.0, 1 - edited / field_count) * 100, 1) if total else 0.0,
        'avg_confidence': avg_conf,
        'saved_minutes': round(total * (MANUAL_MINUTES - ASSISTED_MINUTES), 1),
        'total_amount': round(sum((r['fields'].get('total') or 0) for r in rows), 2),
    }


@app.get('/api/roadmap')
def roadmap():
    """The product path shown on the site: what is done, where we are, what is next."""
    if not ROADMAP.exists():
        return {'title': 'Our path', 'subtitle': '', 'stages': [], 'progress': {'done': 0, 'total': 0, 'percent': 0}}
    data = json.loads(ROADMAP.read_text(encoding='utf-8'))
    stages = data.get('stages') or []
    done = sum(1 for stage in stages if stage.get('status') == 'done')
    data['progress'] = {
        'done': done,
        'total': len(stages),
        'percent': round(100 * done / len(stages)) if stages else 0,
    }
    return data


@app.get('/api/config')
def config():
    """Deployment facts the UI must show honestly: demo mode and CRM wiring."""
    connector = active_connector()
    return {
        'demo_mode': DEMO_MODE,
        'crm_provider': provider_name(),
        'crm_configured': bool(connector and connector.configured()),
        'manual_minutes_per_document': MANUAL_BASELINE_MINUTES,
        # Presentation defaults come from the document profiles, not from the
        # page: the UI shows what the registered types actually read.
        'default_currency': profiles.default_currency(None),
        'document_types': profiles.catalog(),
    }


@app.get('/api/impact')
def impact():
    """Automation effect over confirmed documents: auto-pass, review time, risk."""
    return build_impact(_read_confirmed(), MANUAL_BASELINE_MINUTES)


@app.get('/api/export.csv')
def export_csv():
    rows = _read_confirmed()
    columns = [
        'id',
        'filename',
        'confirmed_at',
        'number',
        'date',
        'supplier',
        'tax_id',
        'total',
        'vat',
        'currency',
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(columns)
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col)
            if value is None:
                value = row['fields'].get(col, '')
            values.append(value)
        writer.writerow(values)
    buffer.seek(0)
    name = f'docflow_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{name}"'},
    )


@app.get('/files/{doc_id}')
def document_file(doc_id: str):
    for candidate in UPLOADS.glob(f'{doc_id}.*'):
        if candidate.suffix != '.json':
            return FileResponse(candidate)
    raise HTTPException(404, 'Not found')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'time': _now(), 'ts': time.time()}
