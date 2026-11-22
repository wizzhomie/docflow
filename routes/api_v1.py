"""The machine door: the same pipeline, reached by a key instead of a browser.

An integration is a machine. It cannot hold a session cookie and it must not be
given a person's password, so it authenticates with a key (`apikeys.py`) and the
key names what it may do. It uploads a document, reads the fields the pipeline
extracted — with the confidence and the issues that came with them — and either
polls until a human on its side approves the document or confirms it itself, when
the key carries `documents:confirm`. In that case the record names the key, so an
approval made through the API is still attributable and still went through the
same invariants the review form enforces.

What this door deliberately does not have: its own extraction, its own validation
or its own confirmation. Every route below calls the code the browser calls, so an
integration cannot reach an answer a reviewer could not.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

import apikeys
import docstore
import intake
import pgapi
import routing
import settings
from routes import documents

router = APIRouter(prefix='/api/v1', tags=['api'])

# The external-id index is a lock-protected file: two retries of the same upload
# arriving together must map to one document, and a read-modify-write without the
# lock would let both write.
_IDS_LOCK = threading.Lock()

DOC_ID = re.compile(settings.DOC_ID_PATTERN)


class TextIn(BaseModel):
    """A document that is already text: a platform that reads its own PDFs."""

    text: str
    filename: str = 'document.txt'
    external_id: str | None = None


class ConfirmIn(BaseModel):
    """The values the platform's reviewer approved; empty means "as extracted"."""

    fields: dict[str, Any] | None = None


def _token(request: Request) -> str:
    authorization = request.headers.get('authorization', '')
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return (request.headers.get('x-api-key') or '').strip()


def _key(request: Request) -> dict[str, Any]:
    if not settings.API_ENABLED:
        # A deployment that turned the door off answers as if it had never been
        # built: no probing for which routes exist.
        raise HTTPException(404, 'Not Found')
    key = apikeys.verify(_token(request))
    if key is None:
        raise HTTPException(
            401,
            'Invalid or missing API key',
            headers={'WWW-Authenticate': 'Bearer realm="docflow"'},
        )
    return key


SCOPE_ACTIONS = {
    'documents:write': 'upload documents',
    'documents:read': 'read documents',
    'documents:confirm': 'confirm documents',
}


def _scoped(scope: str):
    """A dependency that requires one scope, so a reader can never confirm."""

    def dependency(key: dict[str, Any] = Depends(_key)) -> dict[str, Any]:
        if scope not in (key.get('scopes') or []):
            raise HTTPException(403, f'This key may not {SCOPE_ACTIONS.get(scope, scope)}')
        return key

    return dependency


def _person(key: dict[str, Any]) -> dict[str, Any]:
    """The identity the database layer sees for a company-bound key.

    A machine is not a user row: the email is the key's id, which is what the
    audit columns and the document events record, and the role is the least one
    that can do the work — the scope on the key is the real gate.
    """
    return {
        'id': None,
        'email': f'api:{key["id"]}',
        'full_name': key.get('label') or 'API client',
        'role': 'reviewer',
        'company_id': apikeys.company_of(key),
        'company_name': '',
    }


def _require_company_store(key: dict[str, Any]) -> None:
    if not pgapi.AVAILABLE:
        raise HTTPException(
            503,
            'This key belongs to a company, and the database layer is not installed '
            'on this instance',
        )


# --- the external-id index ------------------------------------------------------


def _ids_path() -> Path:
    return Path(settings.API_EXTERNAL_IDS)


def _read_ids() -> dict[str, str]:
    try:
        document = json.loads(_ids_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _write_ids(index: dict[str, str]) -> None:
    path = _ids_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _recall(key_id: str, external_id: str) -> str | None:
    return _read_ids().get(f'{key_id}:{external_id}')


def _remember(key_id: str, external_id: str, doc_id: str) -> None:
    with _IDS_LOCK:
        index = _read_ids()
        index[f'{key_id}:{external_id}'] = str(doc_id)
        _write_ids(index)


def _external_id(value: str | None) -> str | None:
    cleaned = (value or '').strip()
    if not cleaned:
        return None
    if len(cleaned) > 200:
        raise HTTPException(422, 'external_id is longer than 200 characters')
    return cleaned


# --- what a caller sees ---------------------------------------------------------


def _fields_from_parsed(parsed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields = {}
    for name, entry in (parsed.get('fields') or {}).items():
        if isinstance(entry, dict):
            fields[name] = {'value': entry.get('value'), 'confidence': entry.get('confidence')}
        else:
            fields[name] = {'value': entry, 'confidence': None}
    return fields


def _route(route: dict[str, Any] | None) -> dict[str, Any] | None:
    if not route:
        return None
    return {
        'destination': route.get('destination'),
        'code': route.get('destination_code') or route.get('code'),
        'reason': route.get('reason'),
    }


def _view_meta(meta: dict[str, Any]) -> dict[str, Any]:
    parsed = meta.get('parsed') or {}
    fields = _fields_from_parsed(parsed)
    values = {name: entry['value'] for name, entry in fields.items()}
    return {
        'id': str(meta.get('id')),
        'status': 'pending',
        'external_id': meta.get('external_id'),
        'filename': meta.get('filename'),
        'uploaded_at': meta.get('uploaded_at'),
        'doc_type': parsed.get('type'),
        'doc_type_label': parsed.get('type_label'),
        'fields': fields,
        'values': values,
        'issues': list(parsed.get('issues') or []),
        'confidence': parsed.get('confidence'),
        'route': _route(routing.route(parsed.get('type'), values)),
        'confirmed_at': None,
    }


def _view_confirmed(row: dict[str, Any], store: str = 'files') -> dict[str, Any]:
    """A confirmed document, whether the answer came from a JSON line or a row.

    The two stores name the document type differently — the file record carries the
    label a reviewer saw and, from this version on, the profile code; the database
    row carries the code — so the view normalises both instead of pretending the
    shapes are the same.
    """
    values = dict(row.get('fields') or {})
    code = row.get('doc_type_code')
    label = row.get('doc_type_label')
    if store == 'postgres':
        code, label = row.get('doc_type'), None
    elif code is None:
        # A record written before the code was stored: the label is what exists.
        label = row.get('doc_type')
    return {
        'id': str(row.get('id')),
        'status': 'confirmed',
        'external_id': row.get('external_id'),
        'filename': row.get('filename'),
        'uploaded_at': row.get('uploaded_at'),
        'doc_type': code,
        'doc_type_label': label,
        'fields': {
            name: {'value': value, 'confidence': None} for name, value in values.items()
        },
        'values': values,
        'issues': list(row.get('issues') or []),
        'confidence': row.get('confidence'),
        'route': _route(
            {
                'destination': row.get('destination'),
                'destination_code': row.get('destination_code'),
                'reason': row.get('route_reason'),
            }
        ),
        'confirmed_at': row.get('confirmed_at'),
    }


def _view_queue(row: dict[str, Any]) -> dict[str, Any]:
    """A row of the PostgreSQL queue: values without per-field confidence.

    The queue is what the workspace lists before review; a field's confidence there
    is the document's, which is what `confidence` reports.
    """
    values = dict(row.get('fields') or {})
    return {
        'id': str(row.get('id')),
        'status': 'pending',
        'external_id': None,
        'filename': row.get('filename'),
        'uploaded_at': row.get('uploaded_at'),
        'doc_type': row.get('doc_type'),
        'doc_type_label': None,
        'fields': {name: {'value': value, 'confidence': None} for name, value in values.items()},
        'values': values,
        'issues': list(row.get('issues') or []),
        'confidence': row.get('confidence'),
        'route': _route(row.get('route')),
        'confirmed_at': None,
    }


def _view_pg(meta: dict[str, Any]) -> dict[str, Any]:
    parsed = meta.get('parsed') or {}
    fields = _fields_from_parsed(parsed)
    values = {name: entry['value'] for name, entry in fields.items()}
    return {
        'id': str(meta.get('id')),
        'status': meta.get('status') or 'pending',
        'external_id': None,
        'filename': meta.get('filename'),
        'uploaded_at': meta.get('uploaded_at'),
        'doc_type': parsed.get('type'),
        'doc_type_label': parsed.get('type_label'),
        'fields': fields,
        'values': values,
        'issues': list(parsed.get('issues') or []),
        'confidence': parsed.get('confidence'),
        # The route depends on the company's approval threshold, which lives in the
        # database layer this module does not read: a number computed here would be
        # right for the default and wrong for the companies that set their own.
        'route': None,
        'confirmed_at': None,
    }


# --- the file store -------------------------------------------------------------


def _meta_of(doc_id: str) -> dict[str, Any] | None:
    path = docstore._meta_path(doc_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _confirmed_row(doc_id: str) -> dict[str, Any] | None:
    for row in docstore._read_confirmed():
        if str(row.get('id')) == str(doc_id):
            return row
    return None


def _owned(meta: dict[str, Any], key: dict[str, Any]) -> bool:
    """A document belongs to the key that uploaded it.

    The file store has no tenants, so the key is the tenant: a document uploaded
    through the form has no `api_key` and is not readable through the API either.
    """
    return meta.get('api_key') == key['id']


def _load_document(key: dict[str, Any], doc_id: str) -> dict[str, Any]:
    doc_id = str(doc_id)
    if not DOC_ID.fullmatch(doc_id):
        raise HTTPException(404, 'Document not found')
    if apikeys.company_of(key) is not None:
        _require_company_store(key)
        return _view_pg(pgapi.document_meta(_person(key), doc_id))
    meta = _meta_of(doc_id)
    if meta is not None and _owned(meta, key):
        row = _confirmed_row(doc_id)
        return _view_confirmed(row) if row is not None else _view_meta(meta)
    # A confirmed document keeps its pending snapshot, but a store that pruned it
    # still answers from the history — the confirmation is the record.
    row = _confirmed_row(doc_id)
    if row is not None and row.get('api_key') == key['id']:
        return _view_confirmed(row)
    raise HTTPException(404, 'Document not found')


# --- routes ---------------------------------------------------------------------


@router.get('/me')
def me(key: dict[str, Any] = Depends(_key)) -> dict[str, Any]:
    """What this key is: the answer a client needs before it has to guess."""
    return {
        'key': key.get('id'),
        'label': key.get('label'),
        'scopes': list(key.get('scopes') or []),
        'company_id': key.get('company_id'),
    }


@router.post('/documents', status_code=201)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    external_id: str | None = Form(None),
    key: dict[str, Any] = Depends(_scoped('documents:write')),
) -> dict[str, Any]:
    """Upload one document; the same extraction the browser's upload runs."""
    external = _external_id(external_id)
    if external:
        existing = _recall(key['id'], external)
        if existing:
            response.status_code = 200
            return {**_load_document(key, existing), 'idempotent': True}
    data = await file.read()
    return await _ingest(key, data, file.filename or 'document', external)


@router.post('/documents/text', status_code=201)
async def upload_text(
    payload: TextIn,
    response: Response,
    key: dict[str, Any] = Depends(_scoped('documents:write')),
) -> dict[str, Any]:
    """A document already in text form: no OCR, the same parser and invariants."""
    external = _external_id(payload.external_id)
    if external:
        existing = _recall(key['id'], external)
        if existing:
            response.status_code = 200
            return {**_load_document(key, existing), 'idempotent': True}
    return await _ingest(
        key, payload.text.encode('utf-8'), payload.filename or 'document.txt', external
    )


async def _ingest(
    key: dict[str, Any], data: bytes, filename: str, external: str | None
) -> dict[str, Any]:
    if not data:
        raise HTTPException(422, 'The document is empty')
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f'The file is larger than {settings.MAX_UPLOAD_MB:g} MB')
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.ALLOWED:
        raise HTTPException(400, 'Supported formats: PDF, TXT, HTML and images (PNG/JPG/TIFF)')
    doc_id = uuid.uuid4().hex[:10]
    target = settings.UPLOADS / f'{doc_id}{suffix}'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    problem = intake._document_problem(target, len(data))
    if problem:
        target.unlink(missing_ok=True)
        raise HTTPException(413, problem[0].upper() + problem[1:])
    extra = {'api_key': key['id'], 'source': 'api'}
    if external:
        extra['external_id'] = external
    person = None if apikeys.company_of(key) is None else _person(key)
    try:
        meta = await intake.process_async(
            doc_id,
            filename,
            target,
            source='api',
            extra=extra,
            # A company-bound document lives in the database row, as it does for
            # the review form: a pending JSON next to it would be a second truth.
            persist=person is None,
        )
    except intake.ExtractionBusy as busy:
        target.unlink(missing_ok=True)
        raise HTTPException(
            503,
            'The extraction queue is busy, try again in a few seconds',
            headers={'Retry-After': '5'},
        ) from busy
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if person is not None:
        _require_company_store(key)
        try:
            meta['id'] = pgapi.store_upload(person, meta, f'data/uploads/{target.name}')
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return _view_pg(pgapi.document_meta(person, str(meta['id'])))
    if external:
        _remember(key['id'], external, str(meta['id']))
    return _view_meta(meta)


@router.get('/documents')
def list_documents(
    status: str = 'pending',
    limit: int = 20,
    key: dict[str, Any] = Depends(_scoped('documents:read')),
) -> dict[str, Any]:
    """The documents this key uploaded, newest first.

    `status=pending` is the queue; `status=confirmed` is what the pipeline already
    answered, which is what a platform polls for after it lets its own reviewer
    approve a document.
    """
    if status not in {'pending', 'confirmed'}:
        raise HTTPException(422, "status must be 'pending' or 'confirmed'")
    limit = max(1, min(int(limit), 100))
    if apikeys.company_of(key) is not None:
        _require_company_store(key)
        person = _person(key)
        if status == 'pending':
            rows = pgapi.queue_documents(person, limit)
            return {'documents': [_view_queue(row) for row in rows]}
        # The history answers oldest last, which is the shape the report reads; an
        # integration polls for what just happened, so it is reversed here.
        rows = list(reversed(pgapi.history(person, limit)))
        return {'documents': [_view_confirmed(row, store='postgres') for row in rows]}
    if status == 'confirmed':
        rows = [row for row in docstore._read_confirmed() if row.get('api_key') == key['id']]
        rows.sort(key=lambda row: row.get('confirmed_at') or '', reverse=True)
        return {'documents': [_view_confirmed(row) for row in rows[:limit]]}
    confirmed_ids = {str(row.get('id')) for row in docstore._read_confirmed()}
    items = []
    for path in settings.PENDING.glob('*.json'):
        meta = _meta_of(path.stem)
        if meta is None or not _owned(meta, key) or str(meta.get('id')) in confirmed_ids:
            continue
        items.append(_view_meta(meta))
    items.sort(key=lambda item: item.get('uploaded_at') or '', reverse=True)
    return {'documents': items[:limit]}


@router.get('/documents/{doc_id}')
def read_document(
    doc_id: str, key: dict[str, Any] = Depends(_scoped('documents:read'))
) -> dict[str, Any]:
    """One document: the fields, what the rules doubted, and where it was routed."""
    return _load_document(key, doc_id)


@router.post('/documents/{doc_id}/confirm')
def confirm_document(
    doc_id: str,
    payload: ConfirmIn | None = None,
    key: dict[str, Any] = Depends(_scoped('documents:confirm')),
) -> dict[str, Any]:
    """Record the approval the platform made, and deliver it like a form press.

    An empty body confirms exactly what was extracted. A value that differs from
    the extraction goes through the same invariants as the review form, so a
    platform cannot confirm an amount its own form would have refused.
    """
    doc_id = str(doc_id)
    if not DOC_ID.fullmatch(doc_id):
        raise HTTPException(404, 'Document not found')
    submitted = dict((payload.fields if payload else None) or {})
    if apikeys.company_of(key) is not None:
        _require_company_store(key)
        person = _person(key)
        result = pgapi.confirm_document(person, doc_id, submitted)
        return {'document': _view_pg(pgapi.document_meta(person, doc_id)), 'confirmation': result}
    with documents.CONFIRM_LOCK:
        meta = _meta_of(doc_id)
        if meta is None or not _owned(meta, key):
            raise HTTPException(404, 'Document not found')
        if _confirmed_row(doc_id) is not None:
            raise HTTPException(409, 'This document is already confirmed')
        result = documents.confirm_in_files(
            doc_id, submitted, session='', confirmed_by=f'api:{key["id"]}'
        )
    return {'document': _view_confirmed(result['record']), 'confirmation': result}
