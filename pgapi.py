"""The PostgreSQL layer, mounted only when its driver is installed.

The demo on this server runs on files alone; where psycopg2 and the database
modules exist, the same routes answer from rows instead. Keeping the optional
import in one module means every route asks a single object whether a signed-in
person is present, and no route has to know which deployment it runs in.
"""
from fastapi import HTTPException

try:
    import auth
    from pg_documents import document_file_path, history, store_upload
    from pg_queue import confirm_document, document_meta, queue_documents, session_person
    from pg_queue import router as queue_router
    from pg_report import router as report_router

    AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    auth = None
    AVAILABLE = False

    def session_person(request):  # noqa: ANN001, ARG001
        return None

    def _unavailable(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise HTTPException(503, 'The database layer is not installed')

    store_upload = _unavailable
    document_file_path = _unavailable
    history = _unavailable
    confirm_document = _unavailable
    document_meta = _unavailable
    queue_documents = _unavailable
    queue_router = None
    report_router = None


def person(request):
    """The signed-in person, or None for the file-based demo."""
    return session_person(request)


def routers() -> list:
    """The routers that only exist next to a database."""
    if not AVAILABLE:
        return []
    return [auth.router, report_router, queue_router]
