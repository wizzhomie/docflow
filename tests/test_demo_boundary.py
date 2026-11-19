"""The public demo shows the synthetic showcase and a visitor's own work, nothing else.

The file store has no sign-in and one shared directory, so the boundary is a cookie:
without it a visitor would read the operator's uploads and counters.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
import settings  # noqa: E402

SAMPLE = 'act_01_2026-14.pdf'
INVOICE = (
    'Рахунок на оплату № 2026-501 від 27 січня 2026\n'
    'Постачальник: ТОВ «Смак-Дистрибуція», ЄДРПОУ 41234567\n'
    'Всього: 1 234,56 грн\n'
)


def isolate():
    root = Path(tempfile.mkdtemp())
    settings.UPLOADS = root / 'uploads'
    settings.PENDING = root / 'pending'
    settings.INBOX = root / 'inbox'
    settings.INBOX_DONE = settings.INBOX / 'processed'
    settings.CONFIRMED = root / 'confirmed.jsonl'
    for directory in (settings.UPLOADS, settings.PENDING, settings.INBOX, settings.INBOX_DONE):
        directory.mkdir(parents=True, exist_ok=True)
    settings.CONFIRMED.write_text('', encoding='utf-8')
    settings.DEMO_MODE = True


def seed_showcase() -> None:
    """One labelled synthetic row, the kind the demo is allowed to show."""
    row = {
        'id': 'aaaaaaaaaa',
        'filename': 'showcase.pdf',
        'fields': {'number': '1', 'date': '2026-01-01', 'supplier': 'ТОВ «Демо»',
                   'tax_id': '41234567', 'total': 100.0, 'vat': 20.0, 'currency': 'UAH'},
        'source': 'synthetic',
        'confirmed_at': '2026-01-01T10:00:00',
        'edited_fields': [],
        'confidence': 0.9,
        'issues': [],
    }
    settings.CONFIRMED.write_text(json.dumps(row, ensure_ascii=False) + '\n', encoding='utf-8')


def upload(client: TestClient, name: str = 'invoice.txt', body: str = INVOICE) -> dict:
    return client.post(
        '/api/upload', files={'file': (name, body.encode('utf-8'), 'text/plain')}
    ).json()


def test_a_visitor_does_not_see_another_browser_upload():
    isolate()
    seed_showcase()
    with TestClient(app.app) as first, TestClient(app.app) as second:
        mine = upload(first)
        assert any(item['id'] == mine['id'] for item in first.get('/api/inbox').json())
        assert second.get('/api/inbox').json() == []
        ids = [item['id'] for item in second.get('/api/documents').json()]
        assert mine['id'] not in ids


def test_the_showcase_is_visible_and_counted_but_a_foreign_upload_is_not():
    isolate()
    seed_showcase()
    with TestClient(app.app) as visitor:
        upload(visitor)
        stats = visitor.get('/api/stats').json()
        # The synthetic row is the only confirmed document a visitor may count.
        assert stats['documents'] == 1
        assert visitor.get('/api/impact').json()['documents'] == 1
        rows = visitor.get('/api/documents').json()
        assert [row['id'] for row in rows] == ['aaaaaaaaaa']


def test_the_operators_channels_are_not_readable_from_the_demo():
    isolate()
    with TestClient(app.app) as visitor:
        intake_state = visitor.get('/api/intake').json()
        assert intake_state['waiting'] == []
        assert intake_state['folder'] == ''
        mailbox = visitor.get('/api/mailbox').json()
        assert mailbox['configured'] is False
        assert 'host' not in mailbox and 'user' not in mailbox
        metrics = visitor.get('/api/metrics').json()
        assert metrics['files'] == {'waiting': 0, 'confirmed': 0}
        assert metrics['tenants'] == []


def test_a_visitor_sees_their_own_confirmed_document():
    isolate()
    seed_showcase()
    with TestClient(app.app) as visitor:
        meta = visitor.post(f'/api/samples/{SAMPLE}').json()
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        visitor.post('/api/confirm', json={'id': meta['id'], 'fields': fields})
        ids = [row['id'] for row in visitor.get('/api/documents').json()]
        assert meta['id'] in ids
        assert 'aaaaaaaaaa' in ids
