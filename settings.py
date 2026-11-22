"""Every knob of the demo in one place.

Paths, limits and the demo switch are read from here at the moment they are used,
never copied into another module: a test that redirects storage (or turns demo
mode off) points at one module and the whole application follows.
"""
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
SAMPLES = BASE / 'samples'
UPLOADS = BASE / 'data' / 'uploads'
PENDING = BASE / 'data' / 'pending'
INBOX = BASE / 'data' / 'inbox'
INBOX_DONE = INBOX / 'processed'
CONFIRMED = BASE / 'data' / 'confirmed.jsonl'

ALLOWED = {
    '.pdf',
    '.txt',
    '.html',
    '.htm',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.tif',
    '.tiff',
    '.bmp',
}

# Manual baseline used by the impact report. It is an explicit assumption, not a
# measurement: the operator states how long one invoice took before DocFlow.
MANUAL_MINUTES = 3.0
MANUAL_BASELINE_MINUTES = float(os.environ.get('DOCFLOW_MANUAL_MINUTES', MANUAL_MINUTES))

# Demo mode keeps a client demonstration away from the real CRM: Confirm returns
# a simulated deal instead of creating one in Uspacy.
DEMO_MODE = os.environ.get('DOCFLOW_DEMO_MODE', '').strip().lower() in {'1', 'true', 'yes', 'on'}

# A document id is also the name of a file, so the shapes it may take are stated
# once, here, and used wherever the id becomes a path or a query parameter: ten
# hex characters from the uploader, or the plain integer the PostgreSQL pilot
# hands out. `glob` used to build the file path from the raw string, which meant
# `/files/../..` could walk out of the uploads folder.
DEMO_ID_RE = re.compile(r'[0-9a-f]{10}')
DOC_ID_PATTERN = r'^(?:[0-9a-f]{10}|\d{1,10})$'

# Bounds on what one document may cost. These are cost limits, not validation
# rules: a 900-page PDF is a valid document that would occupy the process for
# minutes, and a limit enforced after the work has happened is not a limit.
MAX_UPLOAD_MB = float(os.environ.get('DOCFLOW_MAX_UPLOAD_MB', '20'))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_PDF_PAGES = int(os.environ.get('DOCFLOW_MAX_PDF_PAGES', '200'))

# Reading a text PDF is milliseconds; reading a scan is not. OCR renders every
# page at 300 dpi and runs Tesseract on it, so the page count of a PDF is not
# the cost that matters — the number of pages that reach OCR is. The fields an
# invoice or an act carries live on the first pages, and a scan deeper than this
# is either a bundle or a mistake, not a document a reviewer will read here.
MAX_OCR_PAGES = int(os.environ.get('DOCFLOW_MAX_OCR_PAGES', '10'))
MAX_OCR_SECONDS = float(os.environ.get('DOCFLOW_MAX_OCR_SECONDS', '180'))

# Extraction is CPU work in a CPython process: two at a time keeps the box
# responsive, and a third waits instead of stacking GIL-bound work. A request
# that cannot get a slot in this many seconds is refused with 503 and a
# Retry-After, so a burst of uploads slows the queue instead of the endpoint.
EXTRACTIONS = int(os.environ.get('DOCFLOW_MAX_EXTRACTIONS', '2'))
EXTRACTION_WAIT_SECONDS = float(os.environ.get('DOCFLOW_EXTRACTION_WAIT', '10'))

# The machine door: an integration is not a browser, so it carries a key instead
# of a session cookie. The key file keeps digests only (`apikeys.py`), and the
# external-id index makes an upload retried by the integration the same document
# rather than a second one.
API_ENABLED = os.environ.get('DOCFLOW_API', 'on').strip().lower() not in {
    '0',
    'false',
    'off',
    'no',
}
API_KEYS = BASE / 'data' / 'api_keys.json'
API_EXTERNAL_IDS = BASE / 'data' / 'api_external_ids.json'

# The meaning layer: a model points at the line a value is printed on, the code
# copies it. `off` is the deterministic pipeline, `shadow` records what the model
# would have said without changing an answer, `on` uses a pointer only when the
# text it names is really present. A model that is down or slow changes nothing:
# the pointer is optional and the parser keeps its value.
MEANING = os.environ.get('DOCFLOW_MEANING', 'off').strip().lower()
if MEANING not in {'off', 'shadow', 'on'}:
    MEANING = 'off'

FIELD_LABELS = {
    'number': 'Invoice number',
    'date': 'Date',
    'supplier': 'Supplier',
    'tax_id': 'Tax ID',
    'total': 'Amount due',
    'vat': 'VAT',
    'currency': 'Currency',
}


def ensure_directories() -> None:
    for directory in (UPLOADS, PENDING, INBOX, INBOX_DONE):
        directory.mkdir(parents=True, exist_ok=True)
