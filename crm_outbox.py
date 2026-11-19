"""The outbox between a confirmation and the CRM.

A confirmation used to do everything in the request: write the document, write the
deal, and call the CRM while the person waited. If the call timed out the deal was
already committed and nobody would ever send it again; the schema carried
`tech_status`, `attempts` and `last_error` for exactly this reason, but nothing
wrote them.

Now the confirmation writes one more row in the same transaction — a `crm.deal.push`
job — and the worker owns the call. That gives three properties the request could
not have:

- the deal and the work exist together: a crash after the commit leaves work for
  the worker rather than a deal stuck in `pending`;
- a timeout is retried with the queue's backoff, and the attempt is visible on the
  row (`tech_status = 'sending'` means "a worker may have sent it");
- a lost answer is not a duplicate: when the state is uncertain the worker asks the
  CRM whether the deal already exists before it creates another one.

`tech_status` is the state machine: `pending` (queued), `sending` (a worker is
talking to the CRM), `created` (done), `timeout` (retrying after no answer), `error`
(attempts spent, needs a person).
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json, RealDictCursor

import connectors
import crm
import db
import jobqueue
import settings

KIND = 'crm.deal.push'
log = logging.getLogger('docflow.outbox')


def _payload(company_id: int, document: dict, deal_id: int, fields: dict,
             route: dict | None) -> dict[str, Any]:
    return {
        'company_id': int(company_id),
        'document_id': int(document['id']),
        'deal_id': int(deal_id),
        'doc_type': document.get('doc_type'),
        'destination': (route or {}).get('destination'),
        'reason': (route or {}).get('reason'),
        'title': crm._document_title(fields),
        'fields': fields,
    }


def enqueue(cursor, company_id: int, document: dict, deal_id: int, fields: dict,
            route: dict | None) -> int:
    """Queue the CRM call on the confirmation's own transaction."""
    return jobqueue.enqueue_in(
        cursor, int(company_id), KIND, _payload(company_id, document, deal_id, fields, route)
    )


def push(job: dict[str, Any]) -> None:
    """Send one confirmed deal, recording the outcome on the row.

    A handler raises when the job failed, so the queue retries it; the row keeps
    the reason and the attempt that produced it.
    """
    payload = job.get('payload') or {}
    company_id = int(job.get('company_id') or payload.get('company_id'))
    deal_id = payload.get('deal_id')
    if not company_id or not deal_id:
        raise ValueError('job has no company or deal')

    state = _begin(company_id, deal_id)
    if state is None:
        log.warning('deal %s is gone; dropping the job', deal_id)
        return
    if state['tech_status'] == 'created':
        log.info('deal %s is already in the CRM; nothing to do', deal_id)
        return

    attempt = state['attempts']
    uncertain = state['previous'] in ('sending', 'timeout')
    try:
        result = _send(payload, uncertain=uncertain)
    except Exception as error:  # noqa: BLE001 - the queue decides whether to retry
        _failed(company_id, deal_id, attempt, error, job)
        raise
    _created(company_id, deal_id, attempt, result)


def _begin(company_id: int, deal_id: int) -> dict[str, Any] | None:
    """Lock the deal, mark it `sending` and count the attempt."""
    with db.tenant(company_id) as connection, connection.cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute(
            'SELECT id, tech_status, attempts FROM deals WHERE id = %s FOR UPDATE',
            (deal_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if row['tech_status'] == 'created':
            return {'tech_status': 'created', 'attempts': row['attempts'], 'previous': 'created'}
        cursor.execute(
            """
            UPDATE deals
               SET tech_status = 'sending', attempts = attempts + 1, last_error = NULL
             WHERE id = %s
            RETURNING attempts
            """,
            (deal_id,),
        )
        return {
            'tech_status': 'sending',
            'previous': row['tech_status'],
            'attempts': cursor.fetchone()['attempts'],
        }


def _send(payload: dict[str, Any], uncertain: bool) -> dict[str, Any]:
    fields = payload.get('fields') or {}
    if settings.DEMO_MODE:
        return {
            'external_id': f"DEMO-DB-{payload['deal_id']}",
            'title': payload.get('title'),
            'simulated': True,
        }

    connector = connectors.active()
    if connector is None or not connector.configured():
        raise RuntimeError('no CRM connector is configured')

    # A worker that died mid-call leaves `sending` behind, and a timeout may still
    # have created the deal. Asking first is what makes the retry safe.
    existing = connector.existing_deal(fields)
    if existing:
        return {
            'external_id': existing.get('id'),
            'title': existing.get('title') or payload.get('title'),
            'duplicate': True,
            'uncertain': uncertain,
        }

    company = contact = None
    notes: dict[str, Any] = {}
    try:
        company = connector.ensure_company(fields, doc_type=payload.get('doc_type'))
    except Exception as error:  # noqa: BLE001 - the deal is still worth creating
        notes['company_error'] = str(error)
    try:
        contact = connector.ensure_contact(fields, company)
    except Exception as error:  # noqa: BLE001
        notes['contact_error'] = str(error)
    deal = connector.create_deal(
        title=payload.get('title') or crm._document_title(fields),
        fields=fields,
        company=company,
        contact=contact,
        doc_id=str(payload.get('document_id')),
        route={'destination': payload.get('destination'), 'reason': payload.get('reason')},
        doc_type=payload.get('doc_type'),
    )
    return {'external_id': deal.get('id'), 'title': deal.get('title'), **notes}


def _created(company_id: int, deal_id: int, attempt: int, result: dict[str, Any]) -> None:
    with db.tenant(company_id) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE deals
               SET tech_status = 'created', external_id = %s, attempts = %s, last_error = NULL
             WHERE id = %s
            """,
            (str(result.get('external_id') or ''), attempt, deal_id),
        )
        cursor.execute(
            'SELECT document_id FROM deals WHERE id = %s', (deal_id,)
        )
        row = cursor.fetchone()
        document_id = row[0] if row else None
        if document_id is not None:
            cursor.execute(
                "UPDATE documents SET status = 'sent' WHERE id = %s AND status = 'confirmed'",
                (document_id,),
            )
            cursor.execute(
                """
                INSERT INTO document_events
                       (company_id, document_id, event_type, actor, payload)
                VALUES (%s, %s, 'deal_created', 'worker', %s)
                """,
                (
                    company_id,
                    document_id,
                    Json({
                        'external_id': str(result.get('external_id') or ''),
                        'duplicate': bool(result.get('duplicate')),
                        'simulated': bool(result.get('simulated')),
                        'attempts': attempt,
                    }),
                ),
            )


def _failed(company_id: int, deal_id: int, attempt: int, error: Exception,
            job: dict[str, Any]) -> None:
    attempts = int(job.get('attempts') or attempt)
    limit = int(job.get('max_attempts') or jobqueue.DEFAULT_MAX_ATTEMPTS)
    dead = attempts >= limit
    status = 'error' if dead else ('timeout' if isinstance(error, TimeoutError) else 'pending')
    with db.tenant(company_id) as connection, connection.cursor() as cursor:
        cursor.execute(
            'UPDATE deals SET tech_status = %s, last_error = %s WHERE id = %s',
            (status, str(error)[:500], deal_id),
        )
        if dead:
            cursor.execute(
                """
                INSERT INTO document_events
                       (company_id, document_id, event_type, actor, payload)
                SELECT company_id, document_id, 'deal_failed', 'worker', %s
                  FROM deals WHERE id = %s
                """,
                (Json({'error': str(error)[:500], 'attempts': attempts}), deal_id),
            )
    log.warning('deal %s not sent (attempt %s/%s): %s', deal_id, attempts, limit, error)
