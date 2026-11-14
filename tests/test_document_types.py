"""Deterministic checks for document types: detection and the title that reaches CRM.

The parser is not told which document it reads — the profile registry decides.
These tests pin that an act is recognised as an act and that the CRM deal is
named "Акт …", not "Рахунок …", end to end through the HTTP API.

Run: python3 tests/test_document_types.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from extractor import parse_invoice  # noqa: E402
from profiles import act_ua, detect_profile, invoice_ua  # noqa: E402


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
    return root


def test_act_is_detected_by_title():
    text = 'Акт виконаних робіт № 2026-14 від 11.11.2026\nВиконавець: ТОВ «Клін-Сервіс»'
    assert detect_profile(text) is act_ua


def test_uppercase_act_title_is_detected():
    assert detect_profile('АКТ НАДАНИХ ПОСЛУГ № 15 від 12.11.2026') is act_ua


def test_invoice_is_still_detected():
    assert detect_profile('Рахунок на оплату № 55 від 10.11.2026') is invoice_ua


def test_unknown_document_falls_back_to_invoice():
    assert detect_profile('Якийсь невідомий документ без заголовка') is invoice_ua


def test_act_fields_are_parsed():
    text = (
        'Акт виконаних робіт № 2026-14 від 11.11.2026\n'
        'Виконавець: ТОВ «Клін-Сервіс», ЄДРПОУ 33456789\n'
        'Замовник: ТОВ «Смак-Дистрибуція», ЄДРПОУ 41234567\n'
        'Вартість робіт: 8 600,00 грн\n'
        'ПДВ 20%: 1 433,33 грн\n'
    )
    parsed = parse_invoice(text)
    values = {key: field['value'] for key, field in parsed['fields'].items()}
    assert parsed['type'] == 'act_ua'
    assert parsed['doc_title'] == 'Акт'
    assert values['supplier'] == 'ТОВ «Клін-Сервіс»'
    assert values['number'] == '2026-14'
    assert values['total'] == 8600.0
    assert parsed['issues'] == []


def test_act_confirm_names_the_deal_as_an_act():
    isolate_storage()
    with TestClient(app.app) as client:
        meta = client.post('/api/samples/act_01_2026-14.pdf').json()
        assert meta['parsed']['type'] == 'act_ua'
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        result = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields}).json()
        assert result['crm']['simulated'] is True
        assert result['crm']['deal']['title'].startswith('Акт №2026-14')
        history = client.get('/api/documents').json()
        assert history[0]['doc_type'].startswith('Ukrainian act')


def test_invoice_confirm_keeps_the_invoice_title():
    isolate_storage()
    with TestClient(app.app) as client:
        meta = client.post('/api/samples/pilot_01_2026-501.pdf').json()
        fields = {key: value['value'] for key, value in meta['parsed']['fields'].items()}
        result = client.post('/api/confirm', json={'id': meta['id'], 'fields': fields}).json()
        assert result['crm']['deal']['title'].startswith('Рахунок №2026-501')


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('document type checks passed')


if __name__ == '__main__':
    main()
