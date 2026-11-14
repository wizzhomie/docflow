"""Bitrix24 connector over an incoming webhook.

Bitrix24 has no REST API key requirement for the incoming-webhook scenario: the
client creates a webhook URL in the portal settings and DocFlow posts CRM method
calls to it. That is the cheapest way for a small business to wire a prototype
into its existing portal, and it needs no OAuth application.

Configuration:
    BITRIX24_WEBHOOK_URL=https://<portal>.bitrix24.ua/rest/1/<token>/
    DOCFLOW_CRM=bitrix24
"""

import os

import requests

from connectors.base import Connector
from connectors.matching import digits, has_number, name_forms

BASE = os.environ.get('BITRIX24_WEBHOOK_URL', '').strip()
_session = requests.Session()


def _call(method: str, payload: dict | None = None) -> dict:
    if not BASE:
        raise RuntimeError('BITRIX24_WEBHOOK_URL is not configured')
    url = f"{BASE.rstrip('/')}/{method}.json"
    res = _session.post(url, json=payload or {}, timeout=20)
    res.raise_for_status()
    data = res.json()
    if isinstance(data, dict) and data.get('error'):
        raise RuntimeError(f"Bitrix24 {method}: {data.get('error_description') or data['error']}")
    return data


def _phone(value):
    return [{'VALUE': value, 'VALUE_TYPE': 'WORK'}] if value else None


def _email(value):
    return [{'VALUE': value, 'VALUE_TYPE': 'WORK'}] if value else None


class Bitrix24Connector(Connector):
    name = 'bitrix24'
    display_name = 'Bitrix24'

    def configured(self) -> bool:
        return bool(BASE)

    def existing_deal(self, fields: dict) -> dict | None:
        number = fields.get('number')
        if not number:
            return None
        data = _call('crm.deal.list', {'filter': {'%TITLE': number}, 'select': ['ID', 'TITLE', 'COMMENTS']})
        for deal in data.get('result') or []:
            if not has_number(deal.get('TITLE'), number):
                continue
            tax_id = digits(fields.get('tax_id'))
            if tax_id and tax_id not in digits(deal.get('COMMENTS')):
                continue
            return {'id': deal.get('ID'), 'title': deal.get('TITLE')}
        return None

    def ensure_company(self, fields: dict) -> dict | None:
        supplier = fields.get('supplier')
        if not supplier:
            return None
        wanted = name_forms(supplier)
        data = _call(
            'crm.company.list',
            {'filter': {'%TITLE': supplier[:40]}, 'select': ['ID', 'TITLE', 'COMMENTS', 'PHONE', 'EMAIL', 'ADDRESS']},
        )
        for company in data.get('result') or []:
            if name_forms(company.get('TITLE')) & wanted:
                return self._enrich_company(company, fields)

        payload = {
            'TITLE': supplier,
            'COMPANY_TYPE': 'SUPPLIER',
            'COMMENTS': self._comments(fields),
        }
        if fields.get('phone'):
            payload['PHONE'] = _phone(fields['phone'])
        if fields.get('email'):
            payload['EMAIL'] = _email(fields['email'])
        if fields.get('address'):
            payload['ADDRESS'] = fields['address']
        created = _call('crm.company.add', payload)
        return {'id': created.get('result'), 'title': supplier}

    def _comments(self, fields: dict) -> str:
        lines = []
        if fields.get('tax_id'):
            lines.append(f'ЄДРПОУ/ІПН: {fields["tax_id"]}')
        if fields.get('iban'):
            lines.append(f'IBAN: {fields["iban"]}')
        if fields.get('bank'):
            lines.append(f'Банк: {fields["bank"]}')
        return '\n'.join(lines)

    def _enrich_company(self, company: dict, fields: dict) -> dict:
        """Fill only empty fields; never overwrite what a human entered."""
        payload = {'ID': company.get('ID')}
        if fields.get('phone') and not company.get('PHONE'):
            payload['PHONE'] = _phone(fields['phone'])
        if fields.get('email') and not company.get('EMAIL'):
            payload['EMAIL'] = _email(fields['email'])
        if fields.get('address') and not company.get('ADDRESS'):
            payload['ADDRESS'] = fields['address']
        comments = (company.get('COMMENTS') or '').strip()
        missing = [
            line
            for line in self._comments(fields).splitlines()
            if line and line not in comments
        ]
        if missing:
            payload['COMMENTS'] = (comments + '\n' if comments else '') + '\n'.join(missing)
        if len(payload) > 1:
            _call('crm.company.update', payload)
        return {'id': company.get('ID'), 'title': company.get('TITLE')}

    def ensure_contact(self, fields: dict, company: dict | None) -> dict | None:
        name = fields.get('contact_person')
        if not name:
            return None
        company_id = (company or {}).get('id')
        wanted = name_forms(name)
        data = _call('crm.contact.list', {'filter': {'%NAME': name[:40]}, 'select': ['ID', 'NAME', 'COMPANY_ID']})
        for contact in data.get('result') or []:
            if not (name_forms(contact.get('NAME')) & wanted):
                continue
            if company_id and str(contact.get('COMPANY_ID')) != str(company_id):
                continue
            return {'id': contact.get('ID'), 'title': name}
        payload = {'NAME': name}
        if company_id:
            payload['COMPANY_ID'] = company_id
        if fields.get('phone'):
            payload['PHONE'] = _phone(fields['phone'])
        if fields.get('email'):
            payload['EMAIL'] = _email(fields['email'])
        created = _call('crm.contact.add', payload)
        return {'id': created.get('result'), 'title': name}

    def create_deal(self, *, title: str, fields: dict, company, contact, doc_id) -> dict:
        payload = {'TITLE': title}
        total = fields.get('total')
        if total not in (None, ''):
            payload['OPPORTUNITY'] = round(float(total), 2)
        payload['CURRENCY_ID'] = fields.get('currency') or 'UAH'
        comment_lines = ['Imported by DocFlow']
        if fields.get('supplier'):
            comment_lines.append(f"Supplier: {fields['supplier']}")
        if fields.get('tax_id'):
            comment_lines.append(f"Tax ID: {fields['tax_id']}")
        if fields.get('date'):
            comment_lines.append(f"Invoice date: {fields['date']}")
        if fields.get('iban'):
            comment_lines.append(f"IBAN: {fields['iban']}")
        if doc_id:
            comment_lines.append(f'Source file: /files/{doc_id}')
        payload['COMMENTS'] = '. '.join(comment_lines) + '.'
        if (company or {}).get('id'):
            payload['COMPANY_ID'] = company['id']
        if (contact or {}).get('id'):
            payload['CONTACT_ID'] = contact['id']
        created = _call('crm.deal.add', payload)
        return {'id': created.get('result'), 'title': title}
