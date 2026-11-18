"""Connections, tenants and transactions, in one place.

Two rules are enforced here because breaking either is invisible until it is an
incident:

- **a connection goes back to the pool clean.** A connection that still carries
  `app.company_id` from the previous request would hand the next request another
  tenant's rows, and the row-level policies would happily allow it — they were
  told the wrong company. So the tenant is set with `SET LOCAL`, inside a
  transaction, and the transaction ends before the connection is reused.
- **one request, one transaction.** A page that reads four things sees one
  consistent database, and a failed write leaves nothing half-written. The
  context manager commits on success and rolls back on any exception, so a route
  cannot forget.

The pool is `psycopg2.pool`, which ships with the driver: the demo needs no new
dependency to stop opening a connection per query.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from fastapi import HTTPException

try:  # the file-based demo runs without the driver installed
    import psycopg2
    from psycopg2 import pool as pg_pool
except ImportError:  # pragma: no cover - depends on the environment
    psycopg2 = None  # type: ignore[assignment]
    pg_pool = None  # type: ignore[assignment]

DSN = os.environ.get("DOCFLOW_PG_DSN", "dbname=docflow_dev")
POOL_MIN = int(os.environ.get("DOCFLOW_PG_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("DOCFLOW_PG_POOL_MAX", "8"))

_lock = threading.Lock()
_pool: pg_pool.ThreadedConnectionPool | None = None
_open = 0  # connections currently handed out, for the metrics endpoint


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if pg_pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable")
    with _lock:
        if _pool is None:
            try:
                _pool = pg_pool.ThreadedConnectionPool(POOL_MIN, POOL_MAX, DSN)
            except Exception as error:  # noqa: BLE001
                raise HTTPException(
                    status_code=503, detail="PostgreSQL is unavailable"
                ) from error
        return _pool


def _acquire() -> psycopg2.extensions.connection:
    global _open
    try:
        connection = _get_pool().getconn()
    except Exception as error:  # noqa: BLE001 - a broken pool is 503 either way
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from error
    with _lock:
        _open += 1
    return connection


def _release(connection: psycopg2.extensions.connection) -> None:
    global _open
    with _lock:
        _open = max(0, _open - 1)
    # Last line of defence: never hand back a connection that is still in a
    # transaction, whatever the caller did with it.
    with suppress(Exception):  # best effort: a broken connection is being dropped
        connection.rollback()
    with suppress(Exception):
        _get_pool().putconn(connection)


@contextmanager
def connection() -> Iterator[psycopg2.extensions.connection]:
    """A pooled connection in one transaction: commits on success, rolls back on error."""
    connection = _acquire()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        _release(connection)


@contextmanager
def tenant(company_id: int | str) -> Iterator[psycopg2.extensions.connection]:
    """A connection whose transaction is scoped to one company.

    The company is set with `SET LOCAL`, so it dies with the transaction: the
    connection that goes back to the pool carries no tenant at all.
    """
    with connection() as scoped:
        with scoped.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
        yield scoped


def close_pool() -> None:
    """Drop every pooled connection: for tests and for a clean shutdown."""
    global _pool
    with _lock:
        pool = _pool
        _pool = None
    if pool is not None:
        with suppress(Exception):  # closing a broken pool is best-effort
            pool.closeall()


def status() -> dict[str, Any]:
    """What the pool looks like right now, for /api/metrics and a health check."""
    pool = _pool
    idle = len(getattr(pool, "_pool", [])) if pool is not None else 0
    in_use = len(getattr(pool, "_used", {})) if pool is not None else 0
    return {
        "min": POOL_MIN,
        "max": POOL_MAX,
        "in_use": in_use or _open,
        "idle": idle,
        "created": pool is not None,
    }
