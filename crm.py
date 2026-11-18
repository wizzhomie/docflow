"""What a confirmed document becomes in the CRM, and how duplicates are stopped.

Document-specific wording (titles, the comment block, the supplier card) lives
here because it is the same for every connector; the connector itself only knows
its API. Duplicate suppression is local first: a history match needs no network
call and keeps working when the CRM is unreachable or switched off.
"""
import profiles
import uspacy
from connectors import active as active_connector


def _document_title(fields: dict, doc_title: str | None = None) -> str:
    # Unknown legacy documents fall back to a neutral English word, never to a
    # language of one market.
    prefix = doc_title or 'Document'
    return f'{prefix} №{fields.get("number") or "—"} — {fields.get("supplier") or "Document"}'


def _deal_comment(
    fields: dict,
    doc_id: str | None = None,
    route: dict | None = None,
    doc_type: str | None = None,
) -> str:
    parts = ['Imported by DocFlow']
    if fields.get('supplier'):
        parts.append(f'Supplier: {fields["supplier"]}')
    if fields.get('tax_id'):
        parts.append(f'Tax ID: {fields["tax_id"]}')
    if fields.get('date'):
        parts.append(f'Invoice date: {fields["date"]}')
    if fields.get('vat') is not None:
        currency = fields.get('currency') or profiles.default_currency(doc_type)
        parts.append(f'VAT: {fields["vat"]} {currency}')
    if fields.get('address'):
        parts.append(f'Address: {fields["address"]}')
    if fields.get('phone'):
        parts.append(f'Phone: {fields["phone"]}')
    if fields.get('email'):
        parts.append(f'Email: {fields["email"]}')
    if fields.get('bank'):
        parts.append(f'Bank: {fields["bank"]}')
    if fields.get('iban'):
        parts.append(f'IBAN: {fields["iban"]}')
    if fields.get('contact_person'):
        parts.append(f'Contact: {fields["contact_person"]}')
    if route and route.get('destination'):
        parts.append(f"Routed to: {route['destination']} ({route.get('reason', '')})")
    if doc_id:
        parts.append(f'Source file: /files/{doc_id}')
    return '. '.join(parts) + '.'


def _supplier_details(fields: dict, doc_type: str | None = None) -> dict:
    """Map document fields onto the Uspacy company card and its comment block."""
    labels = profiles.comment_labels(doc_type)
    comment_lines = []
    if fields.get('tax_id'):
        comment_lines.append(f'{labels["tax_id"]}: {fields["tax_id"]}')
    if fields.get('iban'):
        comment_lines.append(f'IBAN: {fields["iban"]}')
    if fields.get('bank'):
        comment_lines.append(f'{labels["bank"]}: {fields["bank"]}')
    return {
        'tax_id': fields.get('tax_id'),
        'address': fields.get('address'),
        'phone': fields.get('phone'),
        'email': fields.get('email'),
        'bank': fields.get('bank'),
        'iban': fields.get('iban'),
        'comment_lines': comment_lines,
    }


def _find_or_create_supplier_company(fields: dict, doc_type: str | None = None) -> dict | None:
    """Find or create the supplier company and fill its card from the document."""
    supplier = fields.get('supplier')
    if not supplier:
        return None
    return uspacy.find_or_create_company(supplier, _supplier_details(fields, doc_type))


def _find_or_create_supplier_contact(fields: dict, company: dict | None) -> dict | None:
    """Supplier contact person, linked to the company and later to the deal."""
    name = fields.get('contact_person')
    if not name:
        return None
    return uspacy.find_or_create_contact(
        name,
        (company or {}).get('id'),
        phone=fields.get('phone'),
        email=fields.get('email'),
    )


def _find_existing_deal(fields: dict) -> dict | None:
    """Check whether the active CRM already holds a deal for this document."""
    connector = active_connector()
    if connector is None or not connector.configured():
        return None
    try:
        return connector.existing_deal(fields)
    except Exception:
        return None


def _effective_fields(parsed_values: dict, submitted: dict) -> dict:
    """What the reviewer confirmed: parsed defaults, overridden by the form.

    A missing key means the form did not show that field (an older cached UI, or
    a document type without it), so the parsed value must survive instead of
    silently disappearing from the history and the CRM payload.
    """
    return {
        **parsed_values,
        **{key: value for key, value in submitted.items() if value not in (None, '')},
    }


def _dedupe_key(fields: dict) -> tuple[str, str, str] | None:
    """Identity of one invoice for duplicate suppression.

    Number alone is not unique (monthly invoices reuse it), so the key is
    number + party + date. A missing component means the invoice cannot be
    matched safely, and no suppression happens.
    """
    number = str(fields.get('number') or '').strip().lower()
    party = str(fields.get('tax_id') or fields.get('supplier') or '').strip().lower()
    issued = str(fields.get('date') or '').strip()
    if not number or not party or not issued:
        return None
    return (number, party, issued)


def _local_duplicate(fields: dict, rows: list[dict]) -> dict | None:
    """Find an already confirmed document with the same invoice identity."""
    key = _dedupe_key(fields)
    if key is None:
        return None
    for row in reversed(rows):
        if _dedupe_key(row.get('fields') or {}) == key:
            return row
    return None


def resolve_deal(
    doc_id: str,
    fields: dict,
    doc_title: str | None,
    profile_code: str | None,
    route_info: dict,
    history: list[dict],
    demo: bool,
) -> dict:
    """Where this confirmation goes: history, a simulated deal, or the CRM.

    The answer always has the same shape — `configured`, `provider`, `demo` and
    either a `deal` or an `error` — so one card in the UI renders every case,
    including the one where the provider is down.
    """
    connector = active_connector()
    crm: dict = {
        'configured': bool(connector and connector.configured()) and not demo,
        'provider': connector.name if connector else 'none',
        'demo': demo,
    }

    local_duplicate = _local_duplicate(fields, history)
    if local_duplicate is not None:
        crm['deal'] = {
            'id': local_duplicate.get('crm_deal_id') or f"HISTORY-{local_duplicate.get('id')}",
            'title': f"Already confirmed: {local_duplicate.get('filename')}",
        }
        crm['duplicate'] = True
        crm['duplicate_source'] = 'history'
    elif demo:
        # Nothing leaves the host during a demonstration.
        crm['deal'] = {'id': f'DEMO-{doc_id}', 'title': _document_title(fields, doc_title)}
        crm['simulated'] = True
        crm['route'] = route_info
    else:
        existing_deal = _find_existing_deal(fields)
        if existing_deal:
            crm['deal'] = {'id': existing_deal.get('id'), 'title': existing_deal.get('title')}
            crm['duplicate'] = True
            crm['duplicate_source'] = 'crm'
        elif crm['configured']:
            company = contact = None
            try:
                company = connector.ensure_company(fields, doc_type=profile_code)
            except Exception as exc:
                # A failed enrichment must be visible, not a silently empty card.
                crm['company_error'] = str(exc)
            try:
                contact = connector.ensure_contact(fields, company)
            except Exception as exc:
                crm['contact_error'] = str(exc)
            try:
                deal = connector.create_deal(
                    title=_document_title(fields, doc_title),
                    fields=fields,
                    company=company,
                    contact=contact,
                    doc_id=doc_id,
                    route=route_info,
                    doc_type=profile_code,
                )
                crm['deal'] = {'id': deal.get('id'), 'title': deal.get('title')}
                crm['route'] = route_info
                crm['company'] = {
                    'id': (company or {}).get('id'),
                    'title': (company or {}).get('title'),
                }
                crm['contact'] = {
                    'id': (contact or {}).get('id'),
                    'title': (contact or {}).get('title'),
                }
            except Exception as exc:
                crm['error'] = str(exc)
    return crm
