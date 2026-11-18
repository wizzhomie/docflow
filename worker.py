"""The worker: one job at a time, out of the durable queue.

The same code runs inside the web process (the demo does, so a demonstration does
not need a second terminal) and as a separate process:

    python3 -m worker          # one worker, foreground
    python3 -m worker --once   # drain what is ready and exit

Nothing here assumes it is the only worker. Two processes of this module take
different rows because the claim statement says so (`FOR UPDATE SKIP LOCKED` in
`jobqueue.claim`), which is the whole point of having a queue instead of a thread.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from typing import Any

import jobqueue

logger = logging.getLogger('docflow.worker')

Handler = Callable[[dict[str, Any]], None]

# What the worker knows how to do. A handler raises when the job failed; the
# queue decides whether that is a retry or the end of the road.
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register the function that runs one kind of job."""

    def register(function: Handler) -> Handler:
        HANDLERS[kind] = function
        return function

    return register


def worker_id() -> str:
    """Who took the job, so a stuck row says which process to look at."""
    return f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}'


def run_once(identifier: str | None = None) -> bool:
    """Claim and run one job. True when there was work, False when the queue is empty."""
    identifier = identifier or worker_id()
    job = jobqueue.claim(identifier)
    if job is None:
        return False
    kind = job['kind']
    function = HANDLERS.get(kind)
    try:
        if function is None:
            raise LookupError(f'no handler for {kind}')
        function(job)
    except Exception as exc:  # noqa: BLE001 - every failure is the queue's business
        outcome = jobqueue.retry_or_fail(job, f'{type(exc).__name__}: {exc}')
        logger.warning(
            'job %s (%s) %s: %s', job['id'], kind, outcome, exc,
            extra={'company': job['company_id'], 'user': identifier},
        )
    else:
        jobqueue.done(job['id'])
        logger.info(
            'job %s (%s) done', job['id'], kind,
            extra={'company': job['company_id'], 'user': identifier},
        )
    return True


def run_forever(stop: threading.Event, interval: float = 2.0, identifier: str | None = None) -> None:
    """Drain the queue until `stop` is set.

    An empty queue costs one statement every `interval` seconds; a database that
    is down costs an exception, which is waited out rather than logged twice a
    second.
    """
    identifier = identifier or worker_id()
    logger.info('worker %s started', identifier, extra={'user': identifier})
    while not stop.is_set():
        try:
            busy = run_once(identifier)
        except Exception as exc:  # noqa: BLE001 - a worker must outlive its database
            logger.warning('worker %s paused: %s', identifier, exc, extra={'user': identifier})
            stop.wait(max(interval, 10.0))
            continue
        if not busy:
            stop.wait(interval)
    logger.info('worker %s stopped', identifier, extra={'user': identifier})


def start(interval: float = 2.0) -> tuple[threading.Thread, threading.Event]:
    """A worker thread for the web process, with the event that stops it."""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever, args=(stop,), kwargs={'interval': interval},
        name='docflow-worker', daemon=True,
    )
    thread.start()
    return thread, stop


def main(argv: list[str] | None = None) -> int:
    import sys

    import tasks  # noqa: F401 - importing registers the handlers

    # A worker process is a service: its log is the only place that says it
    # started and which job it took, so it configures its own handler instead of
    # relying on whatever imported it.
    logging.basicConfig(
        level=os.environ.get('DOCFLOW_LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        stream=sys.stdout,
    )

    once = '--once' in (argv if argv is not None else sys.argv[1:])
    if once:
        drained = 0
        while run_once():
            drained += 1
        print(f'drained {drained} job(s)')
        return 0
    stop = threading.Event()
    try:
        run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
