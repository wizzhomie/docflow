"""Liveness, wiring and the metrics scraper reads."""
import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

import db
import docstore
import observability
import pgapi

router = APIRouter()


@router.get('/api/health')
def health():
    return {'status': 'ok', 'time': docstore._now(), 'ts': time.time()}


@router.get('/api/health/detail')
def health_detail():
    """What the service has wired: the log file and error reporting."""
    detail = {'status': 'ok', 'observability': observability.status()}
    detail['pool'] = db.status()
    if pgapi.auth is not None:
        detail['auth'] = pgapi.auth.status()
    return detail


@router.get('/api/metrics')
def metrics_snapshot(request: Request, format: str = 'json'):
    """What this process is carrying: the queue, the pool, the tenants, the requests.

    JSON for a person, `?format=prometheus` for a scraper. Without a database the
    answer still comes back: the file store is the product, and a metrics endpoint
    that fails is worse than one that says zero.
    """
    files = docstore._file_counts()
    try:
        import metrics as metrics_module
    except ImportError:  # pragma: no cover - the driver is optional
        data = {
            'process': observability.counters(),
            'pool': {},
            'queue': {},
            'tenants': [],
            'files': files,
        }
        if format == 'prometheus':
            return PlainTextResponse(
                f'docflow_files_waiting {files["waiting"]}\n'
                f'docflow_files_confirmed {files["confirmed"]}\n'
            )
        return data
    data = metrics_module.snapshot(files)
    if pgapi.person(request) is None:
        # Queues, tenants and file counts are the operator's working picture; a
        # visitor may see that the process is healthy, not what it is carrying.
        data = {
            **data,
            'queue': {},
            'deals': {},
            'tenants': [],
            'files': {'waiting': 0, 'confirmed': 0},
        }
    if format == 'prometheus':
        return PlainTextResponse(metrics_module.prometheus(data))
    return data


@router.get('/api/demo/error')
def demo_error():
    """A deliberate failure: taken on purpose to see it in the log and the tracker."""
    raise RuntimeError('deliberate demo error: this is what a captured exception looks like')
