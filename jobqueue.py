"""Durable background work: enqueue here, claim there.

The queue exists because the interesting work in document intake is slow and
flaky — a CRM call that times out, a retry that has to wait an hour — and a web
request is the wrong place to wait. What it replaces was a JSONL file guarded by
a lock in one process: a second worker would have overwritten it, a restart lost
the attempt count, and "how much is stuck" was answered by opening the file.

The rules that make it safe with more than one worker:

- work is claimed with `FOR UPDATE SKIP LOCKED`, inside one statement, so two
  workers asking at the same moment take two different rows instead of colliding;
- the attempt is counted when the row is claimed, not when it fails: a worker that
  dies mid-job leaves the count behind, which is the only evidence it tried;
- a retry is a new `run_after`, not a sleep: the row goes back to the queue and
  any worker may pick it up, which is what makes a second process useful.

The table is platform infrastructure and not row-filtered (see `sql/006_jobs.sql`);
every function here that reads it either names a company or is running as a
worker. The worker sets the tenant from the row before touching tenant tables.
"""

from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

import db

BASE_DELAY_SECONDS = 15
MAX_DELAY_SECONDS = 3600
DEFAULT_MAX_ATTEMPTS = 5

CLAIM_SQL = """
UPDATE jobs
   SET status = 'running',
       locked_by = %s,
       locked_at = now(),
       attempts = attempts + 1,
       updated_at = now()
 WHERE id = (
       SELECT id
         FROM jobs
        WHERE status = 'queued'
          AND run_after <= now()
        ORDER BY run_after, id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
 )
 RETURNING id, company_id, kind, payload, attempts, max_attempts
"""


def enqueue(
    company_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    delay_seconds: float = 0.0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Put work in the queue and return its id. `delay_seconds` postpones the first try."""
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO jobs (company_id, kind, payload, run_after, max_attempts)
            VALUES (%s, %s, %s, now() + make_interval(secs => %s), %s)
            RETURNING id
            """,
            (company_id, kind, Json(payload or {}), float(delay_seconds), max_attempts),
        )
        return cursor.fetchone()[0]


def claim(worker_id: str) -> dict[str, Any] | None:
    """Take the oldest ready job, or None when there is nothing to do."""
    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(CLAIM_SQL, (worker_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def done(job_id: int) -> None:
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE jobs SET status = 'done', locked_by = NULL, locked_at = NULL, "
            "last_error = NULL, updated_at = now() WHERE id = %s",
            (job_id,),
        )


def retry(job_id: int, error: str) -> None:
    """Put the job back with exponential backoff, keeping the error it hit."""
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
               SET status = 'queued',
                   run_after = now() + make_interval(
                       secs => least(%s, %s * power(2, greatest(attempts - 1, 0)))
                   ),
                   locked_by = NULL,
                   locked_at = NULL,
                   last_error = %s,
                   updated_at = now()
             WHERE id = %s
            """,
            (MAX_DELAY_SECONDS, BASE_DELAY_SECONDS, error[:500], job_id),
        )


def failed(job_id: int, error: str) -> None:
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE jobs SET status = 'failed', locked_by = NULL, locked_at = NULL, "
            "last_error = %s, updated_at = now() WHERE id = %s",
            (error[:500], job_id),
        )


def retry_or_fail(job: dict[str, Any], error: str) -> str:
    """A failed attempt: back to the queue, or out of it when attempts are spent."""
    if int(job.get('attempts') or 0) >= int(job.get('max_attempts') or DEFAULT_MAX_ATTEMPTS):
        failed(job['id'], error)
        return 'failed'
    retry(job['id'], error)
    return 'retry'


def for_company(company_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """One company's queue: what the API may show, always filtered by the tenant."""
    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT id, kind, status, attempts, run_after, last_error, created_at
              FROM jobs
             WHERE company_id = %s
             ORDER BY id DESC
             LIMIT %s
            """,
            (company_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def stats() -> dict[str, Any]:
    """Depth of the queue and the age of the oldest piece of ready work.

    "Oldest" is the number that matters: a queue of ten fresh jobs is a healthy
    system, a queue of one job that has waited an hour is an outage nobody has
    noticed yet.
    """
    try:
        with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'queued') AS queued,
                       count(*) FILTER (WHERE status = 'running') AS running,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       coalesce(
                           extract(epoch FROM now() - min(run_after) FILTER (WHERE status = 'queued')),
                           0
                       )::int AS oldest_seconds
                  FROM jobs
                """
            )
            return dict(cursor.fetchone())
    except psycopg2.Error as error:  # the metrics endpoint must not fail without a queue
        return {
            'queued': 0,
            'running': 0,
            'failed': 0,
            'oldest_seconds': 0,
            'error': str(error).strip()[:200],
        }
