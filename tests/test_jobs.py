"""Checks for the durable queue and the worker that drains it.

The queue replaced a file and a lock, so the checks are about what the file could
not do: two workers claiming at the same moment take two different jobs, a failed
job goes back with a delay instead of disappearing, and a job that has used up its
attempts stops being retried instead of looping forever.

Run: python3 tests/test_jobs.py
Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

import jobqueue  # noqa: E402
import worker  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def _company() -> int:
    """A throwaway company: its jobs go with it when it is deleted."""
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id',
            ('Jobs test', f'jobs-{uuid.uuid4().hex[:8]}'),
        )
        return cursor.fetchone()[0]


def _drop(company_id: int) -> None:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def _status(job_id: int) -> dict:
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT status, attempts, last_error, run_after > now() FROM jobs WHERE id = %s',
            (job_id,),
        )
        status, attempts, error, later = cursor.fetchone()
    return {'status': status, 'attempts': attempts, 'error': error, 'later': later}


def _make_ready(job_id: int) -> None:
    """Move a scheduled retry into the past, so a test does not sleep for it."""
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE jobs SET run_after = now() - interval '1 second' WHERE id = %s",
                       (job_id,))


def test_a_job_is_claimed_once():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        job_id = jobqueue.enqueue(company, 'test.noop', {'n': 1})
        claimed = jobqueue.claim('test-worker')
        assert claimed is not None and claimed['id'] == job_id
        assert jobqueue.claim('test-worker') is None, 'the same job was claimed twice'
        assert _status(job_id)['status'] == 'running'
        jobqueue.done(job_id)
        assert _status(job_id)['status'] == 'done'
    finally:
        _drop(company)


def test_two_workers_take_two_jobs_not_one():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        for _ in range(6):
            jobqueue.enqueue(company, 'test.noop')
        barrier = threading.Barrier(6)
        claimed: list[int] = []
        lock = threading.Lock()

        def grab() -> None:
            barrier.wait()
            job = jobqueue.claim(f'worker-{uuid.uuid4().hex[:4]}')
            if job is not None:
                with lock:
                    claimed.append(job['id'])

        threads = [threading.Thread(target=grab) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(claimed) == 6, f'claims collided: {len(claimed)} of 6 jobs taken'
        assert len(set(claimed)) == 6, 'two workers were given the same job'
    finally:
        _drop(company)


def test_a_failed_attempt_comes_back_with_a_delay():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        job_id = jobqueue.enqueue(company, 'test.noop')
        job = jobqueue.claim('test-worker')
        outcome = jobqueue.retry_or_fail(job, 'CRM said no')
        state = _status(job_id)
        assert outcome == 'retry'
        assert state['status'] == 'queued'
        assert state['error'] == 'CRM said no'
        assert state['later'] is True, 'a retry must wait, or it is a busy loop'
        assert jobqueue.claim('test-worker') is None, 'a delayed job was claimed early'

        _make_ready(job_id)
        again = jobqueue.claim('test-worker')
        assert again is not None and again['attempts'] == 2
        jobqueue.done(job_id)
    finally:
        _drop(company)


def test_attempts_are_spent_instead_of_looping_forever():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        job_id = jobqueue.enqueue(company, 'test.noop', max_attempts=2)
        for _ in range(2):
            job = jobqueue.claim('test-worker')
            assert job is not None
            jobqueue.retry_or_fail(job, 'still failing')
            _make_ready(job_id)
        state = _status(job_id)
        assert state['status'] == 'failed'
        assert state['attempts'] == 2
        assert jobqueue.claim('test-worker') is None, 'a dead job was retried again'
    finally:
        _drop(company)


def test_the_queue_is_read_per_company():
    if not POSTGRES_AVAILABLE:
        return
    first = _company()
    second = _company()
    try:
        jobqueue.enqueue(first, 'test.noop', {'who': 'first'})
        jobqueue.enqueue(second, 'test.noop', {'who': 'second'})
        seen = jobqueue.for_company(first)
        assert [job['kind'] for job in seen] == ['test.noop']
        assert len(seen) == 1, 'one company saw another company job'
    finally:
        _drop(first)
        _drop(second)


def test_the_worker_runs_a_job_and_finishes_it():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    ran: list[dict] = []

    @worker.handler('test.record')
    def record(job: dict) -> None:
        ran.append(job)

    try:
        job_id = jobqueue.enqueue(company, 'test.record', {'value': 7})
        assert worker.run_once('test-worker') is True
        assert ran and ran[0]['payload']['value'] == 7
        assert _status(job_id)['status'] == 'done'
    finally:
        worker.HANDLERS.pop('test.record', None)
        _drop(company)


def test_the_worker_keeps_a_failure_for_the_queue_to_retry():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()

    @worker.handler('test.explode')
    def explode(job: dict) -> None:
        raise RuntimeError('the CRM is down')

    try:
        job_id = jobqueue.enqueue(company, 'test.explode')
        assert worker.run_once('test-worker') is True
        state = _status(job_id)
        assert state['status'] == 'queued'
        assert 'the CRM is down' in state['error']
    finally:
        worker.HANDLERS.pop('test.explode', None)
        _drop(company)


def test_the_running_worker_sees_the_handlers_tasks_registers():
    """`python -m worker` runs this module as __main__, so the registry must be shared.

    The first version kept HANDLERS in worker.py; tasks imported it by name and got
    a second copy, so the live worker answered every job with "no handler" while
    the tests passed, because they imported the same module the decorator wrote to.
    """
    import tasks  # noqa: F401 - importing registers the handlers
    import workerkit

    assert worker.HANDLERS is workerkit.HANDLERS
    assert 'crm.deal.push' in workerkit.HANDLERS


def test_an_unknown_kind_is_a_failed_job_not_a_crash():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        job_id = jobqueue.enqueue(company, 'test.nobody.handles.this')
        assert worker.run_once('test-worker') is True
        state = _status(job_id)
        assert state['status'] == 'queued'  # first attempt: retried, with the error kept
        assert 'no handler' in state['error']
    finally:
        _drop(company)


def test_a_long_running_worker_leaves_the_suites_jobs_alone():
    """The live worker must not take a job a running test created for itself."""
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        job_id = jobqueue.enqueue(company, 'test.noop')
        assert jobqueue.claim('live-worker', exclude_prefix='test.') is None
        job = jobqueue.claim('test-worker')
        assert job is not None and job['id'] == job_id
        jobqueue.done(job_id)
    finally:
        _drop(company)


def test_the_queue_reports_its_own_depth():
    if not POSTGRES_AVAILABLE:
        return
    company = _company()
    try:
        before = jobqueue.stats()
        job_id = jobqueue.enqueue(company, 'test.noop')
        during = jobqueue.stats()
        assert during['queued'] == before['queued'] + 1
        assert during['oldest_seconds'] >= 0
        job = jobqueue.claim('test-worker')
        if job is not None and job['id'] != job_id:
            jobqueue.done(job['id'])
            job = jobqueue.claim('test-worker')
        assert job is not None
        jobqueue.done(job_id)
    finally:
        _drop(company)


def main():
    if not POSTGRES_AVAILABLE:
        print('skipped: PostgreSQL is not reachable')
        return
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('queue checks passed')


if __name__ == '__main__':
    main()
