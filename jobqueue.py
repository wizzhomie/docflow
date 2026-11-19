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
          {exclude}
        ORDER BY run_after, id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
 )
 RETURNING id, company_id, kind, payload, attempts, max_attempts
"""

# Kinds that belong to the suite. A long-running worker skips them, so running
# the tests next to the live worker does not hand the test's job to production.
TEST_KIND_PREFIX = 'test.'


def enqueue_in(
    cursor,
    company_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    delay_seconds: float = 0.0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Put work in the queue on the caller's transaction.

    A confirmation writes the document, the deal and the work that sends the deal in
    one commit: either all of it exists or none of it does, so a crash can never
    leave a confirmed deal that nobody will ever send.
    """
    cursor.execute(
        """
        INSERT INTO jobs (company_id, kind, payload, run_after, max_attempts)
        VALUES (%s, %s, %s, now() + make_interval(secs => %s), %s)
        RETURNING id
        """,
        (company_id, kind, Json(payload or {}), float(delay_seconds), max_attempts),
    )
    row = cursor.fetchone()
    return row['id'] if isinstance(row, dict) else row[0]


def enqueue(
    company_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    delay_seconds: float = 0.0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Put work in the queue and return its id. `delay_seconds` postpones the first try."""
    with db.connection() as connection, connection.cursor() as cursor:
        return enqueue_in(cursor, company_id, kind, payload, delay_seconds, max_attempts)


def reap_stale(older_than_seconds: float = 900.0) -> int:
    """Return jobs whose worker died back to the queue.

    The attempt is kept: a job that killed its worker once must not be retried
    forever, so the count that leads to a dead job is preserved.
    """
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
               SET status = 'queued',
                   locked_by = NULL,
                   locked_at = NULL,
                   run_after = now(),
                   updated_at = now()
             WHERE status = 'running'
               AND locked_at < now() - make_interval(secs => %s)
            """,
            (float(older_than_seconds),),
        )
        return cursor.rowcount


def claim(worker_id: str, exclude_prefix: str | None = None) -> dict[str, Any] | None:
    """Take the oldest ready job, or None when there is nothing to do.

    `exclude_prefix` exists for the long-running worker: it leaves the suite's
    jobs (`test.…`) alone, so a test that checks the queue is not racing the live
    worker for its own row.
    """
    sql = CLAIM_SQL.format(exclude='AND kind NOT LIKE %s' if exclude_prefix else '')
    params: tuple[Any, ...] = (worker_id,)
    if exclude_prefix:
        params += (f'{exclude_prefix}%',)
    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(sql, params)
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
    except Exception as error:  # noqa: BLE001 - the metrics endpoint must answer without a database
        # `db.connection` raises an HTTPException (503) when there is no database,
        # which is not a psycopg2 error: catching only the driver's class let the
        # endpoint answer 503 instead of an empty queue.
        return {
            'queued': 0,
            'running': 0,
            'failed': 0,
            'oldest_seconds': 0,
            'error': str(error).strip()[:200],
        }
