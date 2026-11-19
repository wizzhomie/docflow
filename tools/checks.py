"""The registry of scripted checks for the synthetic corpus.

Every finding is supposed to end up here as a check that runs after every pass.
The registry exists so that a defect class found once is caught next time without
a person remembering it — and so the reviewer has something concrete to arbitrate:
a failing check is either a real defect, or a check that is written wrong, or a
check that is too narrow for the rule it claims to test.

A check is a function of `(entry, document) -> bool` where True means "this held".
`entry` is one item of the corpus manifest (what was printed, what was expected),
`document` is the whole `intake._process` result: the parsed fields, the items and
the extracted preview. Checks that need the document text get it
from the parsed preview; checks that need the printed features take them from the
manifest, never from the parser, so a check cannot agree with the bug it is
supposed to catch.

The corpus is git-ignored and generated deterministically, so a check refers to a
manifest entry and a printed string, not to a hand-made file.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

Check = Callable[[dict, dict], bool]


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    invariant: str
    severity: str  # 'high' | 'medium' | 'low'
    check: Check


def _fields(document: dict) -> dict:
    return (document.get('parsed') or {}).get('fields') or {}


def _value(document: dict, name: str):
    return _fields(document).get(name, {}).get('value')


def _digits(value) -> str:
    return re.sub(r'\D', '', str(value or ''))


def _printed(entry: dict) -> dict:
    return entry.get('printed') or {}


def _expected(entry: dict) -> dict:
    return entry.get('expected') or {}


# --- field checks: the parser must return what the generator printed ---------


def number_printed_and_returned(entry: dict, document: dict) -> bool:
    printed = _printed(entry).get('number')
    if not printed:
        return True
    return bool(_value(document, 'number'))


def date_printed_and_returned(entry: dict, document: dict) -> bool:
    printed = _printed(entry).get('date')
    if not printed:
        return True
    return bool(_value(document, 'date'))


def total_equals_items(entry: dict, document: dict) -> bool:
    """An invariant of the document itself: the total is the sum of the lines."""
    items = document.get('items') or []
    total = _value(document, 'total')
    if not items or total is None:
        return True
    try:
        computed = round(sum(float(item['quantity']) * float(item['price']) for item in items), 2)
    except (KeyError, TypeError, ValueError):
        return True
    return abs(float(total) - computed) <= 0.05


def vat_not_above_total(entry: dict, document: dict) -> bool:
    vat, total = _value(document, 'vat'), _value(document, 'total')
    if vat is None or total is None:
        return True
    return float(vat) <= float(total) + 0.05


# --- leak checks: a value must not be taken from the wrong place ------------


def tax_id_not_in_supplier(entry: dict, document: dict) -> bool:
    """The tax id is printed next to the supplier, and used to be swallowed by it."""
    tax_id = _digits(_expected(entry).get('tax_id'))
    supplier = _digits(_value(document, 'supplier'))
    if not tax_id or not supplier:
        return True
    return tax_id not in supplier


def supplier_has_no_requisite_words(entry: dict, document: dict) -> bool:
    supplier = (_value(document, 'supplier') or '').casefold()
    if not supplier:
        return True
    stop = ('єдрпоу', 'едрпоу', 'іпн', 'инн', 'рнокпп', 'iban', 'тел', 'адреса')
    return not any(word in supplier for word in stop)


# --- shape checks: values that are present must have the right shape --------


def number_has_no_spaces(entry: dict, document: dict) -> bool:
    number = _value(document, 'number')
    if not number:
        return True
    return ' ' not in str(number).strip()


def currency_is_uah(entry: dict, document: dict) -> bool:
    expected = _expected(entry).get('currency')
    if not expected:
        return True
    return (_value(document, 'currency') or 'UAH') == expected


def text_was_extracted(entry: dict, document: dict) -> bool:
    """A scan whose OCR produced nothing is a failure even if no field was asked."""
    preview = document.get('text_preview') or ''
    return bool(preview.strip())


def scanned_documents_return_a_number(entry: dict, document: dict) -> bool:
    if not entry.get('scan'):
        return True
    return bool(_value(document, 'number'))


CHECKS: tuple[Entry, ...] = (
    Entry('F1', 'Number is returned when the title shows one',
          'For any document whose title carries a number, the parser returns a number.',
          'high', number_printed_and_returned),
    Entry('F2', 'Date is returned when the title shows one',
          'For any document whose title carries a date, the parser returns a date.',
          'high', date_printed_and_returned),
    Entry('F3', 'Total equals the sum of the lines',
          'For any document with an items table, total = sum(quantity * price).',
          'high', total_equals_items),
    Entry('F4', 'VAT never exceeds the total',
          'For any document with a VAT line, vat <= total.',
          'medium', vat_not_above_total),
    Entry('L1', 'Tax id does not leak into the supplier name',
          'For any document with a tax id next to the supplier, the supplier name '
          'does not contain the digits of the tax id.',
          'high', tax_id_not_in_supplier),
    Entry('L2', 'Supplier name has no requisite labels',
          'For any document, the supplier name does not contain ЄДРПОУ, ІПН, '
          'РНОКПП, IBAN, a phone label or an address label.',
          'high', supplier_has_no_requisite_words),
    Entry('S1', 'A number has no inner spaces',
          'For any document, the returned number is a token, not a phrase.',
          'low', number_has_no_spaces),
    Entry('S2', 'Currency is detected when it is printed',
          'For any document printed in hryvnia, the parser returns UAH.',
          'medium', currency_is_uah),
    Entry('S3', 'Text is extracted from every document',
          'For any document, the parser extracted some text (a scan is not blank).',
          'high', text_was_extracted),
    Entry('S4', 'A scan still yields a number',
          'For any scanned document, the parser returns a number.',
          'medium', scanned_documents_return_a_number),
)


def by_id(check_id: str) -> Entry | None:
    for entry in CHECKS:
        if entry.id == check_id:
            return entry
    return None
