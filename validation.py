"""Deterministic invariants for a document type.

Validation is pure code: it does not use a model and it never guesses. Its job
is to catch confident errors (wrong total, bad tax id, mismatched VAT) before a
value reaches the CRM. The value formats belong to the document profile: the
core asks for the tax-id shape, the IBAN shape, the currency list and the VAT
rate instead of assuming one market.
"""

import re
from datetime import date

from profiles import DEFAULT_PROFILE

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
PHONE_DIGITS_RE = re.compile(r'\d{11,13}')

# Which invariants refuse a confirmation and which only warn. A value that is
# not what it claims to be (a tax id that is not a tax id, a date that is not a
# date) would corrupt every record downstream, so it must be fixed before the
# deal is written. A VAT that disagrees with the amount can be legitimate — a
# different rate, a rounding rule — so it is reported and stored, not refused.
BLOCKING_FIELDS = frozenset(
    {'number', 'date', 'supplier', 'tax_id', 'total', 'currency', 'email', 'phone', 'iban'}
)


def blocking_issues(result: dict) -> list[str]:
    """The issues that must stop a write, out of a `validate` result."""
    return [issue for issue in result['issues'] if issue.split(':')[0] in BLOCKING_FIELDS]


def field_names(issues: list[str]) -> list[str]:
    """The field names behind issue strings, for a message a person can act on."""
    names: list[str] = []
    for issue in issues:
        name = issue.split(':')[0]
        if name not in names:
            names.append(name)
    return names


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


def validate(fields: dict, items: list | None = None, profile=None) -> dict:
    profile = profile or DEFAULT_PROFILE
    values = {key: field.get('value') for key, field in fields.items()}
    ok: dict[str, bool | None] = {}
    issues: list[str] = []

    ok['number'] = bool(values.get('number'))
    ok['date'] = _valid_date(values.get('date'))
    ok['supplier'] = bool(values.get('supplier'))

    tax_id = values.get('tax_id')
    ok['tax_id'] = bool(tax_id) and bool(re.fullmatch(profile.TAX_ID_RE, str(tax_id)))

    total = values.get('total')
    total_is_number = isinstance(total, (int, float))
    ok['total'] = total_is_number and total >= 0

    vat = values.get('vat')
    if vat is None:
        ok['vat'] = None
    elif not total_is_number:
        ok['vat'] = False
    else:
        vat_rate = profile.VAT_RATE
        expected = round(total * vat_rate / (100 + vat_rate), 2)
        ok['vat'] = vat == 0 or _close(float(vat), expected)

    # A total that is not above its own VAT line is the classic "copied the wrong
    # number from the right document": the VAT line looks like a total to any
    # reader, human or model. Zero VAT is fine; equal or negative is not.
    if total_is_number and isinstance(vat, (int, float)) and vat > 0:
        ok['total'] = ok['total'] and float(total) > float(vat)

    ok['currency'] = values.get('currency') in set(profile.CURRENCY)

    ok['email'] = _optional(values.get('email'), lambda v: bool(EMAIL_RE.fullmatch(v)))
    ok['phone'] = _optional(
        values.get('phone'), lambda v: bool(PHONE_DIGITS_RE.fullmatch(re.sub(r'\D', '', v)))
    )
    ok['iban'] = _optional(
        values.get('iban'),
        lambda v: bool(re.fullmatch(profile.IBAN_RE, re.sub(r'\s', '', v).upper())),
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
