"""Invariant tree for one document type: Ukrainian delivery note (Видаткова накладна).

The delivery note is the third profile on the registry. It carries the same
requisites as an invoice — supplier, buyer, tax IDs, goods and amounts — but it
documents delivered goods rather than a payment request, so it has its own title
markers and its own way of naming the total («Всього», not «до сплати»). The
market facts come from ``profiles/market_ua.py``, exactly like the invoice and
the act: adding this type needed no change in the parser, the UI or the CRM
connectors.
"""

from profiles.market_ua import (
    COMMENT_LABELS,
    CORE_FIELD_KEYS,
    CURRENCY,
    DATE_PREFIXES,
    DEFAULT_CURRENCY,
    FIELD_KEYS,
    IBAN_CANDIDATE_RE,
    IBAN_RE,
    INVARIANTS,
    LABELS,
    LEGAL_FORMS,
    NUMBER_CHARS,
    NUMBER_MARKERS,
    OCR_LANGS,
    OPTIONAL_FIELD_KEYS,
    PHONE_COUNTRY_CODE,
    PHONE_LOCAL_RE,
    PHONE_RE,
    PHONE_TRUNK_PREFIX,
    TAX_ID_PREFIXES,
    TAX_ID_RE,
    VAT_DETAIL_RE,
    VAT_LABELS,
    VAT_RATE,
    VAT_ZERO_RE,
)

# The profile's public surface. The core reads these through the registry with
# `getattr`, which a linter cannot see; declaring them keeps a formatter from
# deleting the passport the rest of the system depends on.
__all__ = [
    'COMMENT_LABELS',
    'CORE_FIELD_KEYS',
    'CURRENCY',
    'DATE_PREFIXES',
    'DEFAULT_CURRENCY',
    'FIELD_KEYS',
    'IBAN_CANDIDATE_RE',
    'IBAN_RE',
    'INVARIANTS',
    'LABELS',
    'LEGAL_FORMS',
    'NUMBER_CHARS',
    'NUMBER_MARKERS',
    'OCR_LANGS',
    'OPTIONAL_FIELD_KEYS',
    'PHONE_COUNTRY_CODE',
    'PHONE_LOCAL_RE',
    'PHONE_RE',
    'PHONE_TRUNK_PREFIX',
    'TAX_ID_PREFIXES',
    'TAX_ID_RE',
    'VAT_DETAIL_RE',
    'VAT_LABELS',
    'VAT_RATE',
    'VAT_ZERO_RE',
]

CODE = 'delivery_ua'
NAME = 'Ukrainian delivery note (Видаткова накладна)'
DOC_TITLE = 'Видаткова накладна'

TITLE_RE = r'Видаткова\s+накладна'

# Checked on the first lines. The title is specific enough not to collide with
# an invoice or an act.
DETECT_MARKERS = [r'^\s*видаткова\s+накладна', r'видаткова\s+накладна\s*(?:№|n[oоeе]?|м[оo0О])']

# The title already ends in «накладна», so nothing extra precedes the number.
NUMBER_CONTEXT_RE = ''

# The total of a delivery note is «Всього» (sometimes «Всього з ПДВ» или «Сума»).
TOTAL_LABELS = [
    r'всього\s+з\s+пдв',
    r'\bвсього\b',
    r'\bусього\b',
    r'\bразом\b',
    r'\bсума\b',
]
SUBTOTAL_LABELS = [r'ітого', r'итого', r'разом', r'усього', r'всього']

# --- The profile's passport: presentation the core must not decide.

# The fields the review form asks for on this type, and therefore the count the
# accuracy report divides by: the denominator belongs to the document type.
CORE_FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')

UI_LABELS = {
    'number': 'Delivery note number',
    'date': 'Delivery note date',
    'supplier': 'Supplier',
    'tax_id': 'Tax ID (ЄДРПОУ / ІПН)',
    'total': 'Total',
    'vat': 'VAT (ПДВ)',
    'currency': 'Currency',
    'address': 'Address',
    'phone': 'Phone',
    'email': 'Email',
    'bank': 'Bank',
    'iban': 'IBAN',
    'contact_person': 'Contact person',
}

SOURCE_LABELS = {
    'number': 'Delivery note number',
    'date': 'Delivery note date',
    'supplier': 'Supplier block',
    'tax_id': 'ЄДРПОУ / ІПН',
    'total': 'Total',
    'vat': 'VAT (ПДВ)',
    'address': 'Адреса',
    'phone': 'Телефон',
    'bank': 'Банк',
    'contact_person': 'Контактна особа',
}
