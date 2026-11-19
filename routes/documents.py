"""One document's life over HTTP: upload, review queue, confirmation, file."""
import json
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import crm
import docstore
import intake
import pgapi
import profiles
import routing
import settings
from validation import blocking_issues, field_names, validate

router = APIRouter()


class ConfirmPayload(BaseModel):
    """A confirmation: which document, and the values the reviewer kept.

    The id is validated here, not in the route, because the same string is used
    later to build a file path and to look up a database row: a request naming
    anything but a document id never reaches either.
    """

    id: str = Field(pattern=settings.DOC_ID_PATTERN)
    fields: dict


# One confirmation at a time in the file store: the duplicate check reads the
# history and the record is appended straight after, so two clicks that arrive
# together would both see "no duplicate" and both would be written. PostgreSQL
# needs no such lock — the unique index is the judge there, and the second insert
# fails instead of duplicating.
CONFIRM_LOCK = threading.Lock()


@router.post('/api/upload')
async def upload(request: Request, file: UploadFile = File(...)):
    suffix = Path(file.filename or 'doc').suffix.lower()
    if suffix not in settings.ALLOWED:
        raise HTTPException(400, 'Supported formats: PDF, TXT, HTML and images (PNG/JPG/TIFF)')
    doc_id = uuid.uuid4().hex[:10]
    target = settings.UPLOADS / f'{doc_id}{suffix}'
    written = 0
    try:
        # The size is checked while streaming: a limit applied after the body is
        # on the disk has already paid the cost it was meant to bound.
        with target.open('wb') as out:
            while chunk := await file.read(1024 * 256):
                written += len(chunk)
                if written > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, f'The file is larger than {settings.MAX_UPLOAD_MB:g} MB'
                    )
                out.write(chunk)
        problem = intake._document_problem(target, written)
        if problem:
            raise HTTPException(413, problem[0].upper() + problem[1:])
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    # Resolved before the document is read: a database that is down refuses the
    # upload at once instead of taking the whole document in and then failing.
    person = pgapi.person(request)
    try:
        meta = await intake.process_async(
            doc_id, file.filename or target.name, target, persist=person is None
        )
    except intake.ExtractionBusy as busy:
        target.unlink(missing_ok=True)
        raise HTTPException(
            503,
            'The extraction queue is busy, try again in a few seconds',
            headers={'Retry-After': '5'},
        ) from busy
    except Exception:
        # A refused or broken document is not a file: leaving it behind would be
        # an unreachable upload that still takes disk.
        target.unlink(missing_ok=True)
        raise
    if person is not None:
        # Signed in, the document is a row in the database: the queue, the confirm
        # and the history read that row, not a snapshot next to it.
        try:
            meta['id'] = pgapi.store_upload(person, meta, f'data/uploads/{target.name}')
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except Exception as error:  # noqa: BLE001 - the driver's errors are one class here
            target.unlink(missing_ok=True)
            raise HTTPException(503, f'The document could not be stored: {error}') from error
    if not meta['text_preview'].strip():
        meta['warning'] = 'No text extracted: the file may be a scan. OCR is not enabled yet.'
    return meta


@router.post('/api/confirm')
def confirm(payload: ConfirmPayload, request: Request):
    """Confirm one reviewed document: PostgreSQL when signed in, files otherwise."""
    person = pgapi.person(request)
    if person is not None:
        return pgapi.confirm_document(person, payload.id, payload.fields)
    with CONFIRM_LOCK:
        return _confirm_in_files(payload)


def _confirm_in_files(payload: ConfirmPayload) -> dict:
    meta_path = docstore._meta_path(payload.id)
    if not meta_path.exists():
        raise HTTPException(404, 'Document not found')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    parsed_values = {k: v['value'] for k, v in meta['parsed']['fields'].items()}
    fields = crm._effective_fields(parsed_values, payload.fields)
    doc_title = (meta.get('parsed') or {}).get('doc_title')
    doc_type = (meta.get('parsed') or {}).get('type_label')
    profile_code = (meta.get('parsed') or {}).get('type')
    route_info = routing.route((meta.get('parsed') or {}).get('type'), fields)
    edited = [k for k, v in payload.fields.items() if parsed_values.get(k) != v]

    # Extraction was validated when it happened; the values a person typed into
    # the form never were, and a confident error is the one that reaches the CRM.
    check = validate(
        {key: {'value': value} for key, value in fields.items()},
        profile=profiles.get_profile(profile_code),
    )
    # Only what the reviewer changed can be refused: a value the parser left
    # missing is already reported as an issue, and a form cannot fix a field it
    # does not show. A typo in a field the person edited is different — that one
    # is exactly the confident error the pipeline is built to stop.
    blocking = [issue for issue in blocking_issues(check) if issue.split(':')[0] in edited]
    if blocking:
        labels = profiles.ui_labels(profile_code)
        names = [labels.get(name, name.replace('_', ' ')) for name in field_names(blocking)]
        raise HTTPException(422, 'Fix before confirming: ' + ', '.join(names))
    validation_warnings = [
        issue
        for issue in check['issues']
        if issue.split(':')[0] in edited and issue not in blocking
    ]

    crm_result = crm.resolve_deal(
        doc_id=payload.id,
        fields=fields,
        doc_title=doc_title,
        profile_code=profile_code,
        route_info=route_info,
        history=docstore._read_confirmed(),
        demo=settings.DEMO_MODE,
    )

    confirmed_at = docstore._now()
    review_seconds = docstore._review_seconds(
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
        'crm_deal_id': (crm_result.get('deal') or {}).get('id'),
        'doc_type': doc_type,
        'destination': route_info['destination'],
        'destination_code': route_info['destination_code'],
        'route_reason': route_info['reason'],
        'source': meta.get('source', 'upload'),
        'validation_warnings': validation_warnings,
    }
    with settings.CONFIRMED.open('a', encoding='utf-8') as out:
        out.write(json.dumps(record, ensure_ascii=False) + '\n')

    return {
        'ok': True,
        'edited_fields': edited,
        'record': record,
        'crm': crm_result,
        'validation': {'blocking': [], 'warnings': validation_warnings},
    }


@router.get('/api/session')
def session(request: Request):
    """Who the workspace thinks it is talking to (the file-based demo: nobody)."""
    person = pgapi.person(request)
    if person is None:
        return {'signed_in': False, 'mode': 'files'}
    return {
        'signed_in': True,
        'mode': 'postgres',
        'person': person['full_name'],
        'email': person['email'],
        'company': person['company_name'],
        'role': person['role'],
        'can_confirm': person['role'] in ('owner', 'reviewer'),
    }


@router.get('/api/inbox')
def inbox(request: Request, limit: int = 20):
    person = pgapi.person(request)
    if person is not None:
        return pgapi.queue_documents(person, limit)
    return docstore._pending_documents(limit)


@router.get('/api/documents/{doc_id}')
def document_meta(doc_id: str, request: Request):
    person = pgapi.person(request)
    if person is not None:
        return pgapi.document_meta(person, doc_id)
    path = docstore._meta_path(doc_id)
    if not path.exists():
        raise HTTPException(404, 'Document not found')
    return json.loads(path.read_text(encoding='utf-8'))


@router.get('/files/{doc_id}')
def document_file(doc_id: str, request: Request):
    """The original upload, by id.

    Two kinds of id reach this route: a ten-character id from the file store, and
    an integer from the database when a person is signed in. Neither becomes a
    path without a check — the integer is looked up inside the person's tenant,
    the hex id is matched against the known extensions instead of a pattern that
    would resolve `..` segments and serve whatever it found.
    """
    person = pgapi.person(request)
    if person is not None and doc_id.isdigit():
        stored = pgapi.document_file_path(person, doc_id, settings.UPLOADS)
        if stored is not None:
            return FileResponse(stored)
        raise HTTPException(404, 'Not found')
    if not settings.DEMO_ID_RE.fullmatch(doc_id):
        raise HTTPException(404, 'Not found')
    for suffix in settings.ALLOWED:
        candidate = settings.UPLOADS / f'{doc_id}{suffix}'
        if candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404, 'Not found')
