"""Invariant tree for one document type: Ukrainian invoice (Рахунок на оплату).

This module is the single source of knowledge for the document type. Extraction
is anchor-based and order-independent; layout variations are represented by
synonyms and optional blocks rather than by assuming a fixed visual order.

Levels of the invariant tree:
1. document  - title markers, required fields
2. blocks    - label anchors (supplier, buyer, tax id, totals, VAT)
3. fields    - label synonyms (Постачальник/Продавець/Виконавець, ...)
4. values    - date/money/tax-id formats, currency markers
5. noise     - common OCR misreads (№ -> N/Mo/Ne, spaces inside dates)
"""

CODE = 'invoice_ua'
NAME = 'Ukrainian invoice (Рахунок на оплату)'

TITLE_RE = r'(?:Рахунок|Сч[её]т)'

# Order-independent label synonyms.
LABELS = {
    'supplier': [r'постачальник', r'продавець', r'виконавець', r'поставщик'],
    'buyer': [r'покупець', r'покупатель', r'замовник', r'заказчик', r'платник', r'плательщик'],
    'address': [r'адреса', r'адрес'],
}

# Includes OCR look-alikes: tesseract renders «ІПН» as «INH» and «ЄДРПОУ» as
# «ЕДРПОУ» often enough that the tax id must survive the mangling.
TAX_ID_PREFIXES = [r'єдрпоу', r'едрпоу', r'іпн', r'инн', r'inh']

# Invoice number/date markers (OCR-tolerant).
NUMBER_MARKERS = [r'№', r'n[oоeе]?', r'м[оo0О]']
DATE_PREFIXES = [r'від', r'от']

# Grand total vs subtotal labels.
TOTAL_LABELS = [r'(?:всього|усього)\s+до\s+сплати', r'до\s+сплати', r'всего\s+к\s+оплате']
SUBTOTAL_LABELS = [r'ітого', r'итого', r'разом', r'усього', r'всього']

VAT_LABELS = [r'пдв', r'ндс']
VAT_ZERO_RE = r'без\s+(?:пдв|ндс)'
VAT_DETAIL_RE = r'%|тому\s+числі|в\s+том\s+числе'

# Currency markers, checked in order. Default for the target market is UAH.
CURRENCY = {
    'USD': [r'\bUSD\b', r'\$'],
    'EUR': [r'\bEUR\b', r'€'],
    'UAH': [r'\bгрн\b', r'₴', r'\bUAH\b', r'гривн'],
}
DEFAULT_CURRENCY = 'UAH'

FIELD_KEYS = ['number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency']

VAT_RATE = 20

INVARIANTS = [
    'number is present',
    'date is a valid calendar date',
    'supplier is present',
    'tax_id is 8 digits (ЄДРПОУ) or 10-12 digits (ІПН)',
    'total is a non-negative number',
    'vat is 0 or ~20/120 of total when present',
    'currency is one of UAH, USD, EUR',
    'sum(items) approx equals total when items are parsed',
]
