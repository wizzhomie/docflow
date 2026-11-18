"""Deterministic checks for the CRM connector layer.

The Bitrix24 connector is exercised against a local fake of the incoming-webhook
REST protocol, so the HTTP path is real while no portal credential is needed.

Run: python3 tests/test_connectors.py
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import connectors  # noqa: E402
from connectors import bitrix24  # noqa: E402


class FakeBitrix:
    """Minimal Bitrix24 REST fake: records calls and answers from a scene."""

    def __init__(self):
        self.calls = []
        self.scene = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length) or b'{}')
                method = self.path.rsplit('/', 1)[-1].removesuffix('.json')
                fake.calls.append((method, payload))
                answer = fake.scene.get(method, {'result': None})
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/rest/1/token'
        bitrix24.BASE = self.url

    def method_calls(self, name):
        return [payload for method, payload in self.calls if method == name]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


FIELDS = {
    'number': '2026-14',
    'supplier': 'ТОВ «Клін-Сервіс»',
    'tax_id': '33456789',
    'total': 8600.0,
    'currency': 'UAH',
    'date': '2026-11-11',
    'phone': '380442223344',
    'email': 'office@klin-service.ua',
    'address': 'м. Київ, вул. Клінінгова, 5',
    'iban': 'UA881111222233334444555566669',
    'bank': 'АТ «ОТП Банк»',
    'contact_person': 'Наталія Гриб',
}


def test_existing_deal_requires_number_and_tax_id():
    fake = FakeBitrix()
    try:
        fake.scene['crm.deal.list'] = {
            'result': [
                {'ID': '10', 'TITLE': 'Акт №2026-455 — інший', 'COMMENTS': 'Tax ID: 33456789'},
                {'ID': '11', 'TITLE': 'Акт №2026-14 — ТОВ «Клін-Сервіс»', 'COMMENTS': 'Tax ID: 33456789'},
            ]
        }
        found = bitrix24.Bitrix24Connector().existing_deal(FIELDS)
        assert found == {'id': '11', 'title': 'Акт №2026-14 — ТОВ «Клін-Сервіс»'}
    finally:
        fake.close()


def test_existing_deal_ignores_foreign_supplier():
    fake = FakeBitrix()
    try:
        fake.scene['crm.deal.list'] = {
            'result': [{'ID': '12', 'TITLE': 'Акт №2026-14 — інший', 'COMMENTS': 'Tax ID: 99999999'}]
        }
        assert bitrix24.Bitrix24Connector().existing_deal(FIELDS) is None
    finally:
        fake.close()


def test_new_company_carries_requisites():
    fake = FakeBitrix()
    try:
        fake.scene['crm.company.list'] = {'result': []}
        fake.scene['crm.company.add'] = {'result': 55}
        company = bitrix24.Bitrix24Connector().ensure_company(FIELDS)
        assert company == {'id': 55, 'title': 'ТОВ «Клін-Сервіс»'}
        payload = fake.method_calls('crm.company.add')[0]
        assert payload['TITLE'] == 'ТОВ «Клін-Сервіс»'
        assert payload['COMPANY_TYPE'] == 'SUPPLIER'
        assert payload['PHONE'][0]['VALUE'] == '380442223344'
        assert payload['EMAIL'][0]['VALUE'] == 'office@klin-service.ua'
        assert payload['ADDRESS'] == 'м. Київ, вул. Клінінгова, 5'
        assert 'ЄДРПОУ/ІПН: 33456789' in payload['COMMENTS']
        assert 'IBAN: UA881111222233334444555566669' in payload['COMMENTS']
    finally:
        fake.close()


def test_existing_company_is_enriched_but_not_overwritten():
    fake = FakeBitrix()
    try:
        fake.scene['crm.company.list'] = {
            'result': [
                {
                    'ID': '5',
                    'TITLE': 'ТОВ «Клін-Сервіс»',
                    'COMMENTS': 'ЄДРПОУ/ІПН: 33456789',
                    'PHONE': [{'VALUE': '380500000000'}],
                    'EMAIL': [],
                    'ADDRESS': '',
                }
            ]
        }
        bitrix24.Bitrix24Connector().ensure_company(FIELDS)
        payload = fake.method_calls('crm.company.update')[0]
        assert payload['ID'] == '5'
        assert 'PHONE' not in payload, 'a human phone must survive'
        assert payload['EMAIL'][0]['VALUE'] == 'office@klin-service.ua'
        # The tax id is already stored, so the comment is not repeated.
        assert payload['COMMENTS'].count('ЄДРПОУ/ІПН') == 1
    finally:
        fake.close()


def test_contact_is_created_once_and_linked():
    fake = FakeBitrix()
    try:
        fake.scene['crm.contact.list'] = {'result': []}
        fake.scene['crm.contact.add'] = {'result': 77}
        contact = bitrix24.Bitrix24Connector().ensure_contact(FIELDS, {'id': 55})
        assert contact == {'id': 77, 'title': 'Наталія Гриб'}
        payload = fake.method_calls('crm.contact.add')[0]
        assert payload['NAME'] == 'Наталія Гриб'
        assert payload['COMPANY_ID'] == 55
    finally:
        fake.close()


def test_deal_payload_links_company_and_contact():
    fake = FakeBitrix()
    try:
        fake.scene['crm.deal.add'] = {'result': 900}
        deal = bitrix24.Bitrix24Connector().create_deal(
            title='Акт №2026-14 — ТОВ «Клін-Сервіс»',
            fields=FIELDS,
            company={'id': 55},
            contact={'id': 77},
            doc_id='abc123',
        )
        assert deal == {'id': 900, 'title': 'Акт №2026-14 — ТОВ «Клін-Сервіс»'}
        payload = fake.method_calls('crm.deal.add')[0]
        assert payload['TITLE'].startswith('Акт №2026-14')
        assert payload['OPPORTUNITY'] == 8600.0
        assert payload['CURRENCY_ID'] == 'UAH'
        assert payload['COMPANY_ID'] == 55
        assert payload['CONTACT_ID'] == 77
        assert 'Tax ID: 33456789' in payload['COMMENTS']
    finally:
        fake.close()


def test_bitrix_error_is_raised_not_swallowed():
    fake = FakeBitrix()
    try:
        fake.scene['crm.deal.list'] = {'error': 'INVALID_CREDENTIALS', 'error_description': 'bad token'}
        try:
            bitrix24.Bitrix24Connector().existing_deal(FIELDS)
        except RuntimeError as exc:
            assert 'bad token' in str(exc)
        else:
            raise AssertionError('a CRM error must surface')
    finally:
        fake.close()


def test_confirm_writes_through_the_active_connector():
    import tempfile

    from fastapi.testclient import TestClient

    import app as docflow_app
    import crm
    import settings

    root = Path(tempfile.mkdtemp())
    settings.UPLOADS = root / 'uploads'
    settings.PENDING = root / 'pending'
    settings.INBOX = root / 'inbox'
    settings.INBOX_DONE = root / 'inbox' / 'processed'
    settings.CONFIRMED = root / 'confirmed.jsonl'
    for directory in (settings.UPLOADS, settings.PENDING, settings.INBOX, settings.INBOX_DONE):
        directory.mkdir(parents=True, exist_ok=True)
    settings.CONFIRMED.write_text('', encoding='utf-8')
    settings.DEMO_MODE = False

    fake = FakeBitrix()
    connector = bitrix24.Bitrix24Connector()
    fake.scene['crm.deal.list'] = {'result': []}
    fake.scene['crm.company.list'] = {'result': []}
    fake.scene['crm.company.add'] = {'result': 5}
    fake.scene['crm.contact.list'] = {'result': []}
    fake.scene['crm.contact.add'] = {'result': 7}
    fake.scene['crm.deal.add'] = {'result': 9}

    original = crm.active_connector
    crm.active_connector = lambda: connector
    try:
        with TestClient(docflow_app.app) as client:
            meta = client.post('/api/samples/pilot_01_2026-501.pdf').json()
            fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
            result = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields}).json()
            assert result['crm']['provider'] == 'bitrix24'
            assert result['crm']['deal'] == {'id': 9, 'title': 'Рахунок №2026-501 — ТОВ «Терра-Буд»'}
            assert result['crm']['company']['id'] == 5
            assert result['crm']['contact']['id'] == 7
        deal_payload = fake.method_calls('crm.deal.add')[0]
        assert deal_payload['COMPANY_ID'] == 5 and deal_payload['CONTACT_ID'] == 7
        # The destination must travel with the deal, not stay in our database.
        assert 'Routed to: Approval' in deal_payload['COMMENTS']
        assert fake.method_calls('crm.company.add')[0]['TITLE'] == 'ТОВ «Терра-Буд»'
    finally:
        crm.active_connector = original
        fake.close()


def test_provider_can_be_switched_by_environment():
    import os

    previous = os.environ.get('DOCFLOW_CRM')
    try:
        os.environ['DOCFLOW_CRM'] = 'bitrix24'
        assert connectors.provider_name() == 'bitrix24'
        os.environ['DOCFLOW_CRM'] = 'uspacy'
        assert connectors.provider_name() == 'uspacy'
        os.environ['DOCFLOW_CRM'] = 'none'
        assert connectors.provider_name() == 'none'
    finally:
        if previous is None:
            os.environ.pop('DOCFLOW_CRM', None)
        else:
            os.environ['DOCFLOW_CRM'] = previous


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('connector checks passed')


if __name__ == '__main__':
    main()
