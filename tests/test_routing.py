"""Deterministic checks for document routing.

Routing is policy: a document type and an amount decide the destination, and the
rule must always explain itself. These tests keep the rule table and the
explanation honest, both as a function and through the HTTP confirm flow.

Run: python3 tests/test_routing.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import routing  # noqa: E402


def isolate_storage():
    root = Path(tempfile.mkdtemp())
    app.UPLOADS = root / 'uploads'
    app.PENDING = root / 'pending'
    app.INBOX = root / 'inbox'
    app.INBOX_DONE = app.INBOX / 'processed'
    app.CONFIRMED = root / 'confirmed.jsonl'
    for directory in (app.UPLOADS, app.PENDING, app.INBOX, app.INBOX_DONE):
        directory.mkdir(parents=True, exist_ok=True)
    app.CONFIRMED.write_text('', encoding='utf-8')
    app.DEMO_MODE = True


def test_invoice_goes_to_payments():
    result = routing.route('invoice_ua', {'total': 8600})
    assert result['destination'] == 'Payments'
    assert result['destination_code'] == 'payments'
    assert 'payment' in result['reason']


def test_act_goes_to_accounting():
    result = routing.route('act_ua', {'total': 18600})
    assert result['destination'] == 'Accounting'
    assert 'act' in result['reason']


def test_unknown_document_type_falls_back_to_crm():
    result = routing.route('something_else', {'total': 100})
    assert result['destination'] == 'CRM'
    assert result['reason']


def test_large_amount_needs_approval():
    result = routing.route('invoice_ua', {'total': routing.APPROVAL_THRESHOLD + 1})
    assert result['destination'] == 'Approval'
    assert 'approval threshold' in result['reason']


def test_threshold_boundary_is_inclusive():
    result = routing.route('act_ua', {'total': routing.APPROVAL_THRESHOLD})
    assert result['destination'] == 'Approval'


def test_amount_text_with_spaces_is_understood():
    result = routing.route('invoice_ua', {'total': '128 000,00'})
    assert result['destination'] == 'Approval'


def test_missing_amount_keeps_the_type_route():
    assert routing.route('invoice_ua', {'total': None})['destination'] == 'Payments'


def test_confirm_records_and_explains_the_route():
    isolate_storage()
    with TestClient(app.app) as client:
        meta = client.post('/api/samples/act_01_2026-14.pdf').json()
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        result = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields}).json()
        assert result['crm']['route']['destination'] == 'Accounting'
        assert result['record']['destination'] == 'Accounting'
        assert result['record']['route_reason']
        history = client.get('/api/documents').json()
        assert history[0]['destination'] == 'Accounting'


def test_invoice_over_threshold_is_marked_for_approval():
    isolate_storage()
    with TestClient(app.app) as client:
        meta = client.post('/api/samples/pilot_01_2026-501.pdf').json()
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        result = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields}).json()
        # pilot_01 is 128 000 UAH, above the 100 000 approval threshold.
        assert result['record']['destination'] == 'Approval'


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('routing checks passed')


if __name__ == '__main__':
    main()
