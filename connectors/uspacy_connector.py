"""Uspacy adapter: the first CRM, kept behind the connector interface."""

import uspacy

from connectors.base import Connector


class UspacyConnector(Connector):
    name = 'uspacy'
    display_name = 'Uspacy'

    def configured(self) -> bool:
        return uspacy.configured()

    def existing_deal(self, fields: dict) -> dict | None:
        number = fields.get('number')
        if not number:
            return None
        return uspacy.find_deal_by_invoice(number, fields.get('tax_id'))

    def ensure_company(self, fields: dict) -> dict | None:
        supplier = fields.get('supplier')
        if not supplier:
            return None
        # Imported lazily: app owns the invoice-to-CRM mapping.
        from app import _supplier_details

        return uspacy.find_or_create_company(supplier, _supplier_details(fields))

    def ensure_contact(self, fields: dict, company: dict | None) -> dict | None:
        name = fields.get('contact_person')
        if not name:
            return None
        return uspacy.find_or_create_contact(
            name,
            (company or {}).get('id'),
            phone=fields.get('phone'),
            email=fields.get('email'),
        )

    def create_deal(self, *, title: str, fields: dict, company, contact, doc_id, route=None) -> dict:
        from app import _deal_comment

        deal = uspacy.create_deal(
            title=title,
            amount=fields.get('total'),
            currency=fields.get('currency') or 'UAH',
            comments=_deal_comment(fields, doc_id, route),
            company_id=(company or {}).get('id'),
            contact_ids=[contact['id']] if contact and contact.get('id') else None,
        )
        return {'id': deal.get('id'), 'title': deal.get('title')}
