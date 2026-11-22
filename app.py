"""The application object, and nothing else.

Every route group lives in `routes/`, every knob in `settings.py`, the file store
in `docstore.py`. This module starts the background work (the folder watcher, the
mailbox poller, the job worker), and mounts the optional PostgreSQL layer. Helpers
live in the modules above; there is deliberately no re-export layer here.

Start: `python3 -m uvicorn app:app` — the object below is the whole entry point.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import db
import intake
import observability
import pgapi
import settings
import uspacy
from connectors import provider_name

settings.ensure_directories()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. The retry queue belongs to the Uspacy connector only.
    if provider_name() == 'uspacy':
        uspacy.start_retry_worker()
    watcher = asyncio.create_task(intake._folder_watcher())
    mail_watcher = asyncio.create_task(intake._mailbox_worker())
    # The background worker runs in this process so a demonstration does not need
    # a second terminal; `python3 -m worker` runs the same code as another process.
    worker_stop = None
    if os.environ.get('DOCFLOW_JOBS', 'on').strip().lower() not in {'0', 'false', 'off', 'no'}:
        try:
            import tasks  # noqa: F401  (importing registers the job handlers)
            import worker

            _, worker_stop = worker.start()
        except ImportError:  # pragma: no cover - depends on the environment
            worker_stop = None
    try:
        yield
    finally:
        watcher.cancel()
        mail_watcher.cancel()
        uspacy.stop_retry_worker()
        if worker_stop is not None:
            worker_stop.set()
        # The pool belongs to the process: closing it here means a reload or a
        # restart does not leave sessions behind on the database.
        db.close_pool()


app = FastAPI(title='DocFlow', lifespan=lifespan)
logger = logging.getLogger('docflow')


async def _database_unavailable(request: Request, error: Exception) -> JSONResponse:
    """The database went away under a request: 503, not a stack trace."""
    logger.warning('database unavailable on %s: %s', request.url.path, error)
    return JSONResponse(
        {'detail': 'PostgreSQL is unavailable'},
        status_code=503,
        headers={'Retry-After': '5'},
    )


for _connection_error in db.CONNECTION_ERRORS:
    app.add_exception_handler(_connection_error, _database_unavailable)

OBSERVABILITY = observability.install(app)

from routes import api_v1 as api_v1_routes  # noqa: E402
from routes import documents, ops, pages, reports  # noqa: E402
from routes import intake as intake_routes  # noqa: E402

for router in (
    pages.router,
    documents.router,
    intake_routes.router,
    reports.router,
    ops.router,
    api_v1_routes.router,
):
    app.include_router(router)

for router in pgapi.routers():
    app.include_router(router)
