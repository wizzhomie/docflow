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
"""

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

# Order-independent label synonyms.
LABELS = {
    'supplier': [r'виконавець', r'постачальник', r'продавець', r'поставщик'],
    'buyer': [r'замовник', r'заказчик', r'покупець', r'покупатель'],
    'address': [r'адреса', r'адрес'],
    'phone': [r'тел\.?', r'телефон', r'моб\.?'],
    'email': [r'e-?mail', r'ел\.\s*пошта', r'эл\.\s*почта'],
    'bank': [r'банк'],
    'iban': [r'iban', r'р/р', r'розрахунковий\s+рахунок'],
    'contact': [r'контактна\s+особа', r'контактное\s+лицо', r'менеджер'],
}

# Includes OCR look-alikes: tesseract renders «ІПН» as «INH» and «ЄДРПОУ» as
# «ЕДРПОУ» often enough that the tax id must survive the mangling.
TAX_ID_PREFIXES = [r'єдрпоу', r'едрпоу', r'іпн', r'инн', r'inh']

# Invoice number/date markers (OCR-tolerant).
NUMBER_MARKERS = [r'№', r'n[oоeе]?', r'м[оo0О]']
DATE_PREFIXES = [r'від', r'от']

# Grand total vs subtotal labels. An act often names it "вартість робіт".
TOTAL_LABELS = [
    r'(?:всього|усього|загальна)\s+вартість',
    r'вартість\s+робіт',
    r'(?:всього|усього)\s+до\s+сплати',
    r'до\s+сплати',
    r'всего\s+к\s+оплате',
]
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

# Fields that decide whether the document was understood. Optional requisites
# (address, phone, email, bank, IBAN, contact) never lower the overall score:
# their absence is normal, only a malformed value is a problem.
CORE_FIELD_KEYS = ['number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency']
OPTIONAL_FIELD_KEYS = ['address', 'phone', 'email', 'bank', 'iban', 'contact_person']
FIELD_KEYS = CORE_FIELD_KEYS + OPTIONAL_FIELD_KEYS

VAT_RATE = 20

# --- The profile's passport: everything the core must not know about a market.
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

COMMENT_LABELS = {'tax_id': 'ЄДРПОУ/ІПН', 'bank': 'Банк'}

# Language of the documents; the OCR reads the union of all profiles.
OCR_LANGS = 'ukr+eng'

# The act's title already ends in «виконаних робіт», so nothing extra precedes
# the number.
NUMBER_CONTEXT_RE = ''

# Value formats of this market.
TAX_ID_RE = r'\d{8}|\d{10,12}'
IBAN_RE = r'UA\d{27}'
IBAN_CANDIDATE_RE = r'\bUA[\s\d]{26,45}'
PHONE_RE = r'\+?380[\s\-]?\(?\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
PHONE_LOCAL_RE = r'\(?0\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
PHONE_COUNTRY_CODE = '38'
PHONE_TRUNK_PREFIX = '8'

# Names are matched in the CRM after legal forms are removed.
LEGAL_FORMS = ('тов', 'фоп', 'пп', 'дп', 'ат')
NUMBER_CHARS = '0-9a-zа-яіїєґ'

INVARIANTS = [
    'number is present',
    'date is a valid calendar date',
    'supplier is present',
    'tax_id is 8 digits (ЄДРПОУ) or 10-12 digits (ІПН)',
    'total is a non-negative number',
    'vat is 0 or ~20/120 of total when present',
    'currency is one of UAH, USD, EUR',
    'sum(items) approx equals total when items are parsed',
    'email is present or absent, but never malformed',
    'phone is present or absent, but never a random number from the text',
    'iban is UA + 27 digits when present',
]
