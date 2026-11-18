"""Invariant tree for one document type: Ukrainian act of completed work (Акт).

This module is the single source of knowledge for the document type. Extraction
is anchor-based and order-independent; layout variations are represented by
synonyms and optional blocks rather than by assuming a fixed visual order.

Levels of the invariant tree:
1. document  - title markers, required fields
2. blocks    - label anchors (supplier, buyer, tax id, totals, VAT)
3. fields    - label synonyms (Постачальник/Продавець/Виконавець, ...)
4. values    - date/money/tax-id formats, currency markers
5. noise     - common OCR misreads (№ -> N/Mo/Ne, spaces inside dates)

The market facts behind levels 2-4 come from ``profiles/market_ua.py`` and are
shared with the invoice and the delivery note. This module only states what
makes an act an act.
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

CODE = 'act_ua'
NAME = 'Ukrainian act of completed work (Акт виконаних робіт)'
DOC_TITLE = 'Акт'

# The number sits after the act's full name, e.g. «Акт виконаних робіт № 2026-14».
TITLE_RE = (
    r'Акт(?:\s+(?:виконаних|наданих|приймання|здачі-приймання)?'
    r'(?:\s+(?:робіт|послуг))?)?'
)

# Markers that decide which profile owns a document. Checked on the first lines.
DETECT_MARKERS = [r'^\s*акт\b', r'акт\s+(?:виконаних|здачі|приймання)', r'акт\s+наданих']

# The act's title already ends in «виконаних робіт», so nothing extra precedes
# the number.
NUMBER_CONTEXT_RE = ''

# Grand total vs subtotal labels. An act often names it "вартість робіт".
TOTAL_LABELS = [
    r'(?:всього|усього|загальна)\s+вартість',
    r'вартість\s+робіт',
    r'(?:всього|усього)\s+до\s+сплати',
    r'до\s+сплати',
    r'всего\s+к\s+оплате',
]
SUBTOTAL_LABELS = [r'ітого', r'итого', r'разом', r'усього', r'всього']

# --- The profile's passport: presentation the core must not decide.

# The fields the review form asks for on this type, and therefore the count the
# accuracy report divides by: the denominator belongs to the document type.
CORE_FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')

UI_LABELS = {
    'number': 'Act number',
    'date': 'Act date',
    'supplier': 'Executor',
    'tax_id': 'Tax ID (ЄДРПОУ / ІПН)',
    'total': 'Amount due',
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
    'number': 'Act number',
    'date': 'Act date',
    'supplier': 'Executor block',
    'tax_id': 'ЄДРПОУ / ІПН',
    'total': 'Amount due',
    'vat': 'VAT (ПДВ)',
    'address': 'Адреса',
    'phone': 'Телефон',
    'bank': 'Банк',
    'contact_person': 'Контактна особа',
}
