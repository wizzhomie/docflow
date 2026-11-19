"""The two pages and the sample documents they load."""
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

import demo
import settings
from intake import _process

router = APIRouter()


def _html(name: str) -> FileResponse:
    """Serve a page from templates/ without caching: the UI ships new fields
    with every change, and a stale copy silently drops them from the confirm
    payload."""
    return FileResponse(
        settings.BASE / 'templates' / name,
        headers={'Cache-Control': 'no-store, must-revalidate'},
    )


@router.get('/')
def landing():
    # The showcase page explains the product; the working review UI lives at /app.
    return _html('landing.html')


@router.get('/app')
def index():
    # The UI ships new fields with every change; a stale cached copy silently
    # drops them from the confirm payload, so it must not be cached.
    return _html('index.html')


@router.get('/api/samples')
def samples():
    if not settings.SAMPLES.exists():
        return []
    return [
        {'name': p.name, 'size': p.stat().st_size}
        for p in sorted(settings.SAMPLES.glob('*.pdf'))
    ]


@router.post('/api/samples/{name}')
def load_sample(name: str, request: Request, response: Response):
    source = settings.SAMPLES / name
    if not source.exists() or source.suffix.lower() != '.pdf':
        raise HTTPException(404, 'Sample not found')
    doc_id = uuid.uuid4().hex[:10]
    target = settings.UPLOADS / f'{doc_id}.pdf'
    shutil.copyfile(source, target)
    # A sample is the visitor's own document for the rest of their demo session:
    # it appears in their queue and history, not in somebody else's.
    session = demo.session_id(request) or demo.new_session()
    meta = _process(doc_id, name, target, source='sample', extra={'session': session})
    response.set_cookie(
        demo.COOKIE, session, max_age=demo.MAX_AGE, httponly=True, samesite='lax'
    )
    return meta
