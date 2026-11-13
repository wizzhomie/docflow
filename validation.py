"""Deterministic invariants for Ukrainian invoices.

Validation is pure code: it does not use a model and it never guesses. Its job
is to catch confident errors (wrong total, bad tax id, mismatched VAT) before a
value reaches the CRM.
"""

import re
from datetime import date

CURRENCIES = {'UAH', 'USD', 'EUR'}
TAX_ID_RE = re.compile(r'\d{8}|\d{10,12}')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
IBAN_RE = re.compile(r'UA\d{27}')
PHONE_RE = re.compile(r'\d{11,13}')


def _optional(value, check):
    # Absent requisites are normal; only a malformed value is a problem.
    if value in (None, ''):
        return None
    return check(str(value))


def _valid_date(value) -> bool:
    if not value or not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1.0, abs(b) * 0.01)


def validate(fields: dict, items: list | None = None) -> dict:
    values = {key: field.get('value') for key, field in fields.items()}
    ok: dict[str, bool | None] = {}
    issues: list[str] = []

    ok['number'] = bool(values.get('number'))
    ok['date'] = _valid_date(values.get('date'))
    ok['supplier'] = bool(values.get('supplier'))

    tax_id = values.get('tax_id')
    ok['tax_id'] = bool(tax_id) and bool(TAX_ID_RE.fullmatch(str(tax_id)))

    total = values.get('total')
    total_is_number = isinstance(total, (int, float))
    ok['total'] = total_is_number and total >= 0

    vat = values.get('vat')
    if vat is None:
        ok['vat'] = None
    elif not total_is_number:
        ok['vat'] = False
    else:
        expected = round(total * 20 / 120, 2)
        ok['vat'] = vat == 0 or _close(float(vat), expected)

    ok['currency'] = values.get('currency') in CURRENCIES

    ok['email'] = _optional(values.get('email'), lambda v: bool(EMAIL_RE.fullmatch(v)))
    ok['phone'] = _optional(
        values.get('phone'), lambda v: bool(PHONE_RE.fullmatch(re.sub(r'\D', '', v)))
    )
    ok['iban'] = _optional(
        values.get('iban'), lambda v: bool(IBAN_RE.fullmatch(re.sub(r'\s', '', v).upper()))
    )

    if items:
        parsed_sum = round(sum(item.get('amount') or 0 for item in items), 2)
        if total_is_number and parsed_sum > 0:
            ok['items_sum'] = _close(parsed_sum, float(total))
        else:
            ok['items_sum'] = None
    else:
        ok['items_sum'] = None

    for key, passed in ok.items():
        if passed is False:
            issues.append(f'{key}: invariant failed')

    return {'ok': ok, 'issues': issues}
