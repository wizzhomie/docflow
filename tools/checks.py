"""The registry of scripted checks for the synthetic corpus.

Every finding is supposed to end up here as a check that runs after every pass, so
that a defect class found once is caught next time without a person remembering it.

A check is a function of `(entry, document) -> True | False | None`:
- `True`  — the invariant held;
- `False` — it did not;
- `None`  — not applicable to this document (no VAT line, no tax id printed, no
  text twin). The distinction matters: a check that passes on a document where it
  has nothing to test is the false green the reviewer keeps finding.

`entry` is one item of the corpus manifest: what was printed and what was expected.
`document` is the whole `intake._process` result. Checks take expected values from
the manifest, never from the parser, so a check cannot agree with the bug it is
supposed to catch.

Check ids are stable; a finding that no check covers gets the next free id.
Removed ids are not reused (see the history at the bottom).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

Check = Callable[[dict, dict], bool | None]


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


def _printed(entry: dict) -> dict:
    return entry.get('printed') or {}


def _expected(entry: dict) -> dict:
    return entry.get('expected') or {}


def _digits(value) -> str:
    return re.sub(r'\D', '', str(value or ''))


def norm_number(value) -> str:
    return re.sub(r'[\s\u00a0]+', '', str(value or '')).casefold()


def norm_date(value) -> str:
    """ISO for the comparison; `dd.mm.yyyy` and `dd.mm.yy` are accepted shapes."""
    text = str(value or '').strip()
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', text):
        day, month, year = text.split('.')
        return f'{year}-{month}-{day}'
    if re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', text):
        day, month, year = text.split('.')
        return f'20{year}-{month}-{day}'
    return text


def norm_text(value) -> str:
    text = str(value or '')
    text = text.replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"')
    text = text.replace('’', "'").replace('`', "'")
    return re.sub(r'\s+', ' ', text).strip().casefold()


def close(left, right, tolerance: float = 0.05) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


# --- field checks: the returned value must be the printed one ----------------


def number_matches(entry: dict, document: dict) -> bool | None:
    printed = _printed(entry).get('number')
    if not printed:
        return None
    return norm_number(_value(document, 'number')) == norm_number(printed)


def date_matches(entry: dict, document: dict) -> bool | None:
    expected = _expected(entry).get('date')
    if not expected:
        return None
    return norm_date(_value(document, 'date')) == expected


def vat_is_below_total(entry: dict, document: dict) -> bool | None:
    """Only documents that print VAT are asked; a no-VAT invoice has no VAT line."""
    if not _expected(entry).get('vat'):
        return None
    vat, total = _value(document, 'vat'), _value(document, 'total')
    if vat is None or total is None:
        return False
    return float(vat) < float(total)


def tax_id_matches(entry: dict, document: dict) -> bool | None:
    expected = _expected(entry).get('tax_id')
    if not expected:
        return None
    return _digits(_value(document, 'tax_id')) == _digits(expected)


def total_matches(entry: dict, document: dict) -> bool | None:
    expected = _expected(entry).get('total')
    if expected is None:
        return None
    if not close(_value(document, 'total'), expected):
        return False
    # A total that equals the VAT line is the wrong number taken from the right
    # document: cheap to catch here, expensive to catch in a CRM.
    vat = _value(document, 'vat')
    if vat is not None and _expected(entry).get('vat'):
        return float(_value(document, 'total')) > float(vat)
    return True


def supplier_matches(entry: dict, document: dict) -> bool | None:
    expected = _expected(entry).get('supplier')
    if not expected:
        return None
    return norm_text(_value(document, 'supplier')) == norm_text(expected)


# --- leak checks: a value must not be taken from the wrong place --------------


def tax_id_not_in_supplier(entry: dict, document: dict) -> bool | None:
    tax_id = _digits(_expected(entry).get('tax_id'))
    if not tax_id:
        return None
    # A supplier name with no digits at all is the normal case and passes: the
    # check asks whether the tax id that was printed near the supplier ended up
    # inside the name.
    return tax_id not in _digits(_value(document, 'supplier'))


def supplier_has_no_requisites(entry: dict, document: dict) -> bool | None:
    """A requisites label with a value, not a bare word: «Телеканал» is a name."""
    supplier = _value(document, 'supplier')
    if not supplier:
        return None
    text = norm_text(supplier)
    patterns = (
        r'(?:єдрпоу|едрпоу|іпн|инн|рнокпп)\s*\d',
        r'\bua\s?\d{2}[\s\d]{20,}',
        r'(?:тел|телефон|моб)\.?\s*[:+]?\s*\d',
        r'адрес[аи]?\s*:',
    )
    return not any(re.search(pattern, text) for pattern in patterns)


# --- shape checks: text that must be there ------------------------------------


def text_mentions_number(entry: dict, document: dict) -> bool | None:
    printed = _digits(_printed(entry).get('number'))
    preview = _digits(document.get('text_preview'))
    if not printed:
        return None
    return printed in preview


def scan_matches_twin(entry: dict, document: dict) -> bool | None:
    """A scan of a known text document must read the same values as the text."""
    twin = entry.get('_twin')
    if not twin:
        return None
    names = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat')
    for name in names:
        left, right = _value(document, name), _value(twin, name)
        if name in ('supplier',):
            if norm_text(left) != norm_text(right):
                return False
        elif name == 'date':
            if norm_date(left) != norm_date(right):
                return False
        elif name == 'total':
            if not close(left, right):
                return False
        elif name == 'vat':
            if left is None and right is None:
                continue
            if not close(left, right):
                return False
        elif norm_number(left) != norm_number(right):
            return False
    return True


CHECKS: tuple[Entry, ...] = (
    Entry('F1', 'Number is the printed number',
          'For any document whose title carries a number, the parser returns that '
          'number, not merely some number.',
          'high', number_matches),
    Entry('F2', 'Date is the printed date',
          'For any document whose title carries a date — numeric or with a month '
          'name — the parser returns that date.',
          'high', date_matches),
    Entry('F4', 'VAT is below the total it belongs to',
          'For any document that prints a VAT line, the returned VAT is present '
          'and smaller than the returned total.',
          'high', vat_is_below_total),
    Entry('F5', 'Tax id is the printed tax id',
          'For any document that prints a tax id (ЄДРПОУ, ІПН, РНОКПП), the parser '
          'returns those digits.',
          'high', tax_id_matches),
    Entry('F6', 'Total is the printed amount due',
          'For any document that prints a total, the parser returns that amount, '
          'and it is larger than the VAT line when the document has one.',
          'high', total_matches),
    Entry('L1', 'Tax id does not leak into the supplier name',
          'For any document with a tax id next to the supplier, the supplier name '
          'does not contain the digits of the tax id.',
          'high', tax_id_not_in_supplier),
    Entry('L2', 'Supplier name has no requisite labels with values',
          'For any document, the supplier name does not contain a tax-id label '
          'with digits, an IBAN, a phone label with digits or an address label.',
          'high', supplier_has_no_requisites),
    Entry('L3', 'Supplier name is the printed name',
          'For any document, the returned supplier equals the printed name after '
          'case, quote and whitespace normalisation — an OCR look-alike such as '
          'ТОВ → TOB is a miss, not a match.',
          'medium', supplier_matches),
    Entry('S3', 'Extracted text contains the printed number',
          'For any document with a number, the extracted text contains those '
          'digits — a blank or truncated extraction is caught, not just an empty '
          'preview.',
          'medium', text_mentions_number),
    Entry('S5', 'A scan reads what its text twin reads',
          'For any scanned document that has a text twin, the two parses return '
          'the same values.',
          'medium', scan_matches_twin),
)

# Retired on purpose (pass 0 arbitration, `checks-arbitration.md`):
# - F3: compared `items` with `item['quantity']`, which is not the key the parser
#   returns (`qty`), so it silently passed everything; the rule it wanted lives in
#   F6, which takes the expected total from the manifest instead of recomputing it.
# - S1: "a number has no spaces" could not fail — the number regex cannot produce
#   one.
# - S2: "currency is UAH" was satisfied by the parser default and by `or 'UAH'`;
#   the corpus prints hryvnia only, so the check could not tell detection from
#   default. A currency check needs a corpus with a second currency.
# - S4: "a scan yields a number" was a subset of F1; S5 compares the whole parse
#   with the text twin instead.


def by_id(check_id: str) -> Entry | None:
    for entry in CHECKS:
        if entry.id == check_id:
            return entry
    return None
