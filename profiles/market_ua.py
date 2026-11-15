"""Market facts shared by every Ukrainian document profile.

A profile describes one document type; this module describes the market those
types belong to. Everything here is true for an invoice, an act and a delivery
note: the currency, the VAT vocabulary, the tax-ID and IBAN formats, the phone
formats, the legal forms the CRM matching strips from a company name, the OCR
language and the label vocabulary.

A second market gets its own module; the document types that belong to it import
their shared facts from there, and the core still knows nothing about countries.
"""

# --- currency and VAT -------------------------------------------------------

CURRENCY = {
    'USD': [r'\bUSD\b', r'\$'],
    'EUR': [r'\bEUR\b', r'€'],
    'UAH': [r'\bгрн\b', r'₴', r'\bUAH\b', r'гривн'],
}
DEFAULT_CURRENCY = 'UAH'
VAT_RATE = 20

VAT_LABELS = [r'пдв', r'ндс']
VAT_ZERO_RE = r'без\s+(?:пдв|ндс)'
VAT_DETAIL_RE = r'%|тому\s+числі|в\s+том\s+числе'

# --- what a document of this market looks like ------------------------------

OCR_LANGS = 'ukr+eng'

NUMBER_MARKERS = [r'№', r'n[oоeе]?', r'м[оo0О]']
DATE_PREFIXES = [r'від', r'от']

# Order-independent label synonyms. One vocabulary for the whole market: a
# supplier block may be titled «Постачальник», «Продавець» or «Виконавець»
# depending on the document type, and the buyer may be «Покупець» or «Замовник».
LABELS = {
    'supplier': [r'постачальник', r'продавець', r'виконавець', r'поставщик'],
    'buyer': [
        r'покупець',
        r'покупатель',
        r'замовник',
        r'заказчик',
        r'платник',
        r'плательщик',
        r'отримувач',
    ],
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

# --- value formats ----------------------------------------------------------

TAX_ID_RE = r'\d{8}|\d{10,12}'
IBAN_RE = r'UA\d{27}'
IBAN_CANDIDATE_RE = r'\bUA[\s\d]{26,45}'
PHONE_RE = r'\+?380[\s\-]?\(?\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
PHONE_LOCAL_RE = r'\(?0\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
PHONE_COUNTRY_CODE = '38'
PHONE_TRUNK_PREFIX = '8'

# --- CRM matching and presentation ------------------------------------------

# Names are matched in the CRM after legal forms are removed.
LEGAL_FORMS = ('тов', 'фоп', 'пп', 'дп', 'ат', 'ооо')
NUMBER_CHARS = '0-9a-zа-яіїєґ'

COMMENT_LABELS = {'tax_id': 'ЄДРПОУ/ІПН', 'bank': 'Банк'}

# Fields that decide whether the document was understood. Optional requisites
# (address, phone, email, bank, IBAN, contact) never lower the overall score:
# their absence is normal, only a malformed value is a problem.
CORE_FIELD_KEYS = ['number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency']
OPTIONAL_FIELD_KEYS = ['address', 'phone', 'email', 'bank', 'iban', 'contact_person']
FIELD_KEYS = CORE_FIELD_KEYS + OPTIONAL_FIELD_KEYS

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
