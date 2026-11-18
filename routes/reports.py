"""What the confirmed documents add up to: headline numbers, impact, CSV, roadmap."""
import csv
import io
import json
import os
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import docstore
import impact
import pgapi
import profiles
import settings
from connectors import active as active_connector
from connectors import provider_name

router = APIRouter()


def _confirmed_rows(request: Request) -> list[dict]:
    """Confirmed documents: the database for a signed-in person, files otherwise.

    The shape is the same on purpose — the reports below were written against the
    file records, and a signed-in person must not get a different answer because
    the row happens to live in PostgreSQL.
    """
    person = pgapi.person(request)
    if person is not None:
        return pgapi.history(person)
    return docstore._read_confirmed()


@router.get('/api/documents')
def documents(request: Request, limit: int = 50):
    rows = list(reversed(_confirmed_rows(request)))[:limit]
    # Surface the measured review time per document; legacy rows derive it from
    # the stored timestamps instead of showing an empty cell.
    return [{**row, 'review_seconds': docstore._review_seconds(row)} for row in rows]


@router.get('/api/stats')
def stats(request: Request):
    """Headline numbers for the page.

    They are taken from the impact report instead of being computed again: two
    endpoints that count the same thing differently (six fields here, seven
    there) are a way to be wrong in public.
    """
    rows = _confirmed_rows(request)
    report = impact.build_impact(rows, settings.MANUAL_BASELINE_MINUTES)
    total = len(rows)
    avg_conf = round(sum(r['confidence'] for r in rows) / total, 2) if total else 0.0
    return {
        'documents': report['documents'],
        'edited_fields': report['edited_fields'],
        'accuracy': report['field_accuracy'],
        'avg_confidence': avg_conf,
        'saved_minutes': report['time_saved_minutes'],
        'total_amount': round(sum((r['fields'].get('total') or 0) for r in rows), 2),
    }


@router.get('/api/impact')
def impact_report(request: Request):
    """Automation effect over confirmed documents: auto-pass, review time, risk."""
    return impact.build_impact(_confirmed_rows(request), settings.MANUAL_BASELINE_MINUTES)


@router.get('/api/export.csv')
def export_csv(request: Request):
    rows = _confirmed_rows(request)
    columns = [
        'id',
        'filename',
        'confirmed_at',
        'number',
        'date',
        'supplier',
        'tax_id',
        'total',
        'vat',
        'currency',
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(columns)
    for row in rows:
        values = []
        for col in columns:
            value = row.get(col)
            if value is None:
                value = row['fields'].get(col, '')
            values.append(value)
        writer.writerow(values)
    buffer.seek(0)
    name = f'docflow_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{name}"'},
    )


@router.get('/api/roadmap')
def roadmap():
    """The product path shown on the site: what is done, where we are, what is next."""
    if not settings.ROADMAP.exists():
        return {
            'title': 'Our path',
            'subtitle': '',
            'stages': [],
            'progress': {'done': 0, 'total': 0, 'percent': 0},
        }
    data = json.loads(settings.ROADMAP.read_text(encoding='utf-8'))
    stages = data.get('stages') or []
    done = sum(1 for stage in stages if stage.get('status') == 'done')
    data['progress'] = {
        'done': done,
        'total': len(stages),
        'percent': round(100 * done / len(stages)) if stages else 0,
    }
    return data


@router.get('/api/config')
def config():
    """Deployment facts the UI must show honestly: demo mode and CRM wiring."""
    connector = active_connector()
    return {
        'demo_mode': settings.DEMO_MODE,
        'crm_provider': provider_name(),
        'crm_configured': bool(connector and connector.configured()),
        'manual_minutes_per_document': settings.MANUAL_BASELINE_MINUTES,
        # Presentation defaults come from the document profiles, not from the
        # page: the UI shows what the registered types actually read.
        'default_currency': profiles.default_currency(None),
        'document_types': profiles.catalog(),
        # Optional: URL of the assistant widget script. Empty means the widget is off,
        # which is the public default. A deployment that runs the assistant sets it.
        'assistant_url': os.environ.get('DOCFLOW_ASSISTANT_URL', '').strip(),
    }
