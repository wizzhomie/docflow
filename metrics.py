"""What this process is carrying: tenants, queue, pool, requests.

The metrics endpoint exists so that "scalable" is a number somebody can watch
instead of an adjective in a README. Four questions it answers in one call:

- how deep is the background queue, and how old is its oldest job (a queue of ten
  fresh jobs is a healthy system; one job that waited an hour is an outage);
- how many connections are in use out of the pool (the first sign that the process
  is the bottleneck, not the database);
- how many documents each company has (who is actually using the thing);
- how many requests have been served and how many failed.

It is JSON by default, because a person reads it; `?format=prometheus` renders the
same numbers for a scraper, because a machine should not have to parse prose.
"""

from __future__ import annotations

from typing import Any

import db
import jobqueue
import observability


def tenants() -> list[dict[str, Any]]:
    """Every company with its document and queue counts.

    Row-level security is the reason this loops: the platform layer can list the
    companies, but the documents of a company are only visible inside that
    company's tenant, so each one is counted in its own transaction. With two
    companies that is two cheap queries; with ten thousand it would be the wrong
    shape, and the honest fix is a per-tenant counter updated on write.
    """
    try:
        with db.connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT id, name FROM companies ORDER BY id')
            companies = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
    except Exception:  # noqa: BLE001 - metrics must answer without a database too
        return []

    rows: list[dict[str, Any]] = []
    for company in companies:
        counts = {'documents': 0, 'waiting': 0}
        try:
            with db.tenant(company['id']) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*), count(*) FILTER (WHERE status = 'new') FROM documents"
                )
                documents, waiting = cursor.fetchone()
                counts = {'documents': int(documents), 'waiting': int(waiting)}
        except Exception:  # noqa: BLE001
            pass
        rows.append({'company': company['name'], **counts})
    return rows


def snapshot(file_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """The whole picture, as one dictionary."""
    counters = observability.counters()
    return {
        'process': counters,
        'pool': db.status(),
        'queue': jobqueue.stats(),
        'tenants': tenants(),
        'files': file_counts or {},
    }


def prometheus(data: dict[str, Any]) -> str:
    """The same numbers in the line format a scraper expects."""
    samples: list[tuple[str, str, str, Any]] = []

    def add(name: str, value: Any, kind: str = 'gauge', labels: str = '') -> None:
        samples.append((name, kind, labels, value))

    queue = data.get('queue') or {}
    add('docflow_queue_jobs', queue.get('queued', 0), 'gauge', '{status="queued"}')
    add('docflow_queue_jobs', queue.get('running', 0), 'gauge', '{status="running"}')
    add('docflow_queue_jobs', queue.get('failed', 0), 'gauge', '{status="failed"}')
    add('docflow_queue_oldest_seconds', queue.get('oldest_seconds', 0))

    pool = data.get('pool') or {}
    add('docflow_pool_connections', pool.get('in_use', 0), 'gauge', '{state="in_use"}')
    add('docflow_pool_connections', pool.get('idle', 0), 'gauge', '{state="idle"}')

    requests = data.get('process') or {}
    add('docflow_requests_total', requests.get('requests', 0), 'counter')
    add('docflow_request_errors_total', requests.get('server_errors', 0), 'counter',
        '{class="server"}')
    add('docflow_request_errors_total', requests.get('client_errors', 0), 'counter',
        '{class="client"}')
    add('docflow_uptime_seconds', requests.get('uptime_seconds', 0))

    for tenant in data.get('tenants') or []:
        name = str(tenant.get('company', '')).replace('\\', '').replace('"', '')
        add('docflow_documents', tenant.get('documents', 0), 'gauge', f'{{company="{name}"}}')

    files = data.get('files') or {}
    add('docflow_files_waiting', files.get('waiting', 0))
    add('docflow_files_confirmed', files.get('confirmed', 0))

    lines = [
        f'# TYPE {name} {next(sample[1] for sample in samples if sample[0] == name)}'
        for name in dict.fromkeys(sample[0] for sample in samples)
    ]
    lines.extend(f'{name}{labels} {value}' for name, _, labels, value in samples)
    return '\n'.join(lines) + '\n'
