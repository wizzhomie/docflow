"""Where a confirmed document goes next.

Routing is a policy, not document knowledge: it is deterministic, it explains
itself, and it lives outside the profiles so the destination can change without
touching how a document is read. A rule returns the destination plus the reason,
and the reason is what the operator sees — a route nobody can explain is a route
nobody trusts.
"""

import os

# A large invoice should not silently join the payment queue: it needs a person
# to approve it. The threshold is a business assumption, so it is configurable.
APPROVAL_THRESHOLD = float(os.environ.get('DOCFLOW_APPROVAL_THRESHOLD', '100000'))

DESTINATIONS = {
    'payments': 'Payments',
    'accounting': 'Accounting',
    'approval': 'Approval',
    'crm': 'CRM',
}

_BY_DOCUMENT_TYPE = {
    'invoice_ua': ('payments', 'invoice goes to the payment queue'),
    'act_ua': ('accounting', 'an act closes completed work, so accounting owns it'),
    'delivery_ua': ('accounting', 'a delivery note confirms goods received, so accounting books it'),
}


def _total(fields: dict) -> float | None:
    value = fields.get('total')
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(' ', '').replace(',', '.'))
    except (TypeError, ValueError):
        return None


def route(doc_type: str | None, fields: dict) -> dict:
    """Return {'destination': label, 'destination_code', 'reason'}."""
    total = _total(fields)
    if total is not None and total >= APPROVAL_THRESHOLD:
        return {
            'destination': DESTINATIONS['approval'],
            'destination_code': 'approval',
            'reason': f'amount {total:,.2f} is above the approval threshold {APPROVAL_THRESHOLD:,.2f}',
        }

    code, reason = _BY_DOCUMENT_TYPE.get(doc_type or '', ('crm', 'no specific process for this document type'))
    return {'destination': DESTINATIONS[code], 'destination_code': code, 'reason': reason}
