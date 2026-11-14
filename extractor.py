import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from profiles import DEFAULT_PROFILE, detect_profile
from profiles.suppliers import get_supplier_profile
from validation import validate

MONEY = r'([\d\u00a0\s]+[.,]\d{2})'

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}
OCR_LANGS = 'ukr+eng'


def _build_patterns(
    tax_id: str | None,
    supplier_name: str | None,
    document_profile=DEFAULT_PROFILE,
) -> dict:
    """Build regex patterns from document profile + supplier profile."""
    profile = get_supplier_profile(tax_id, supplier_name)
    labels = profile['labels']
    tax_prefixes = profile['tax_prefixes']
    overrides = profile['overrides']

    supplier_labels = '|'.join(labels['supplier'])
    buyer_labels = '|'.join(labels['buyer'])
    tax_prefixes_re = '|'.join(tax_prefixes)
    # Stop words for supplier name extraction: only buyer labels (next block)
    supplier_stop = '|'.join(labels['buyer'])
    # Full stop for block extraction
    block_stop = '|'.join(
        labels['supplier'] + labels['buyer'] + tax_prefixes + labels.get('address', [])
    )
    address_labels = '|'.join(labels.get('address', []))
    phone_labels = '|'.join(labels.get('phone', []))
    email_labels = '|'.join(labels.get('email', []))
    bank_labels = '|'.join(labels.get('bank', []))
    iban_labels = '|'.join(labels.get('iban', []))
    contact_labels = '|'.join(labels.get('contact', []))
    total_re = '|'.join(document_profile.TOTAL_LABELS)
    subtotal_re = '|'.join(document_profile.SUBTOTAL_LABELS)
    vat_re = '|'.join(document_profile.VAT_LABELS)
    number_re = (
        document_profile.TITLE_RE
        + r'(?:\s+на\s+оплату)?\s*(?:'
        + '|'.join(document_profile.NUMBER_MARKERS)
        + r')\s*'
        r'([A-Za-zА-Яа-яІіЇїЄєҐґ0-9\-/]+)'
        r'(?:\s*(?:' + '|'.join(document_profile.DATE_PREFIXES) + r')\s*'
        r'(\d{1,2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{2,4}))?'
    )

    return {
        'supplier_labels': supplier_labels,
        'buyer_labels': buyer_labels,
        'tax_prefixes': tax_prefixes_re,
        'supplier_stop': supplier_stop,
        'block_stop': block_stop,
        'total_re': total_re,
        'subtotal_re': subtotal_re,
        'vat_re': vat_re,
        'number_re': number_re,
        'currency': overrides.get('currency', document_profile.DEFAULT_CURRENCY),
        'vat_rate': overrides.get('vat_rate', document_profile.VAT_RATE),
        'vat_zero_re': document_profile.VAT_ZERO_RE,
        'vat_detail_re': document_profile.VAT_DETAIL_RE,
        'address_labels': address_labels,
        'phone_labels': phone_labels,
        'email_labels': email_labels,
        'bank_labels': bank_labels,
        'iban_labels': iban_labels,
        'contact_labels': contact_labels,
    }


def _ocr_image(path: Path) -> str:
    if not shutil.which('tesseract'):
        return ''
    try:
        out = subprocess.run(
            [
                'tesseract',
                str(path),
                'stdout',
                '-l',
                OCR_LANGS,
                '--psm',
                '4',
                '-c',
                'preserve_interword_spaces=1',
            ],
            capture_output=True,
            timeout=120,
        )
        return out.stdout.decode('utf-8', errors='ignore')
    except Exception:
        return ''


def _ocr_pdf(path: Path) -> str:
    if not shutil.which('tesseract') or not shutil.which('pdftoppm'):
        return ''
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / 'page')
        try:
            subprocess.run(
                ['pdftoppm', '-r', '300', '-png', str(path), prefix],
                capture_output=True,
                check=True,
                timeout=180,
            )
        except Exception:
            return ''
        pages = sorted(Path(tmp).glob('page*.png'))
        return '\n'.join(_ocr_image(page) for page in pages)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md'}:
        return path.read_text(encoding='utf-8', errors='ignore')
    if suffix in {'.html', '.htm'}:
        raw = path.read_text(encoding='utf-8', errors='ignore')
        raw = re.sub(r'<style.*?</style>', ' ', raw, flags=re.S | re.I)
        raw = re.sub(r'<script.*?</script>', ' ', raw, flags=re.S | re.I)
        raw = re.sub(r'<br\s*/?>', '\n', raw, flags=re.I)
        raw = re.sub(r'</(p|div|tr|h\d|li)>', '\n', raw, flags=re.I)
        raw = re.sub(r'</t[dh]>', '  ', raw, flags=re.I)
        raw = re.sub(r'<[^>]+>', '', raw)
        raw = raw.replace('&nbsp;', ' ').replace('&laquo;', '«').replace('&raquo;', '»')
        raw = raw.replace('&', '&').replace('&mdash;', '—')
        return raw
    if suffix in IMAGE_SUFFIXES:
        return _ocr_image(path)
    try:
        out = subprocess.run(
            ['pdftotext', '-layout', '-enc', 'UTF-8', str(path), '-'],
            capture_output=True,
            check=True,
            timeout=30,
        )
        text = out.stdout.decode('utf-8', errors='ignore')
    except Exception:
        text = ''
    if not text.strip() and suffix == '.pdf':
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = '\n'.join((page.extract_text() or '') for page in reader.pages)
        except Exception:
            text = ''
    if not text.strip() and suffix == '.pdf':
        text = _ocr_pdf(path)
    return text


def _clean(text: str) -> str:
    return text.replace('\u00a0', ' ').replace('\r', '')


def _num(raw: str | None) -> float | None:
    if not raw:
        return None
    value = raw.replace('\u00a0', '').replace(' ', '').replace(',', '.')
    try:
        return float(value)
    except ValueError:
        return None


def _money(raw: str | None) -> float | None:
    value = _num(raw)
    return round(value, 2) if value is not None else None


def _date(raw: str | None) -> str | None:
    if not raw:
        return None
    parts = re.split(r'[.\-/]', raw.replace(' ', ''))
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = '20' + year
    return f'{year}-{month.zfill(2)}-{day.zfill(2)}'



def _tidy_supplier(value: str | None, tax_prefixes: str) -> str | None:
    """Cut a trailing tax identifier from the supplier name.

    Supplier labels run to the end of the line, so ``ТОВ «Чистий дім», ЄДРПОУ
    23456789`` arrives as one string. The tax id is a separate field and must
    not leak into the name, otherwise a confident wrong supplier reaches CRM.
    """
    if not value:
        return None
    cut = re.split(rf'[,;]?\s*(?:{tax_prefixes})\b', value, maxsplit=1, flags=re.I)[0]
    cleaned = re.sub(r'\s{2,}', ' ', cut).strip(' .,;:-')
    return cleaned or None


def _field(value, source, confidence):
    return {'value': value, 'source': source, 'confidence': confidence}


def _last_money(line: str) -> float | None:
    matches = re.findall(MONEY, line)
    return _money(matches[-1]) if matches else None



EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'\+?380[\s\-]?\(?\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
PHONE_LOCAL_RE = re.compile(r'\(?0\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
IBAN_CANDIDATE_RE = re.compile(r'\bUA[\s\d]{26,45}')


def _digits(raw: str) -> str:
    return re.sub(r'\D', '', raw or '')


def _label_value(text: str, labels: str) -> str | None:
    """Value that follows an explicit label on the same line, or None."""
    match = re.search(rf'(?:{labels})\s*[:\-]?\s*([^\n]+)', text, re.I)
    if not match:
        return None
    value = match.group(1).strip(' .,;:-')
    return value or None


def _extract_phone(text: str) -> str | None:
    """Only a labelled phone or an explicit +380 number; never a random digit run."""
    for line in text.splitlines():
        if re.search(r'тел\.?|телефон|моб\.?', line, re.I):
            for pattern in (PHONE_RE, PHONE_LOCAL_RE):
                match = pattern.search(line)
                if match:
                    phone = _normalise_phone(match.group(0))
                    if phone:
                        return phone
    match = re.search(r'\+380[\s\-]?\(?\d{2,3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', text)
    return _normalise_phone(match.group(0)) if match else None


def _normalise_phone(raw: str) -> str | None:
    digits = _digits(raw)
    if digits.startswith('8') and len(digits) == 11:
        digits = '3' + digits
    elif digits.startswith('0'):
        digits = '38' + digits
    return digits if 11 <= len(digits) <= 13 else None


def _extract_iban(text: str) -> str | None:
    # A Ukrainian IBAN is UA plus 27 digits; grouping spaces differ per document,
    # so the whitespace is stripped before the length is checked.
    for match in IBAN_CANDIDATE_RE.finditer(text.upper()):
        candidate = re.sub(r'\s', '', match.group(0))
        if len(candidate) == 29 and candidate[2:].isdigit():
            return candidate
    return None


def _extract_email(text: str) -> str | None:
    match = EMAIL_RE.search(text)
    return match.group(0).strip('.,;') if match else None


def parse_invoice(
    text: str,
    items: list[dict] | None = None,
    tax_id: str | None = None,
    supplier_name: str | None = None,
    profile=None,
) -> dict:
    profile = profile or detect_profile(text)
    t = _clean(text)
    flat = re.sub(r'[ \t]+', ' ', t)

    # First pass: extract tax_id and supplier to load supplier profile
    temp_patterns = _build_patterns(None, None, profile)
    supplier = tax_id = None
    sm = re.search(rf'(?:{temp_patterns["supplier_labels"]})\s*[:\s]\s*(.+)', flat, re.I)
    if sm:
        supplier = re.split(
            rf'\s{{2,}}|\s+(?:{temp_patterns["supplier_stop"]})\b',
            sm.group(1).strip(),
            flags=re.I,
        )[0].strip(' .,;')
        supplier = _tidy_supplier(supplier, temp_patterns['tax_prefixes'])
    block = t
    pb = re.search(
        rf'(?:{temp_patterns["supplier_labels"]})[:\s]+(.+?)(?:\n\s*(?:{temp_patterns["buyer_labels"]}))',
        t,
        re.I | re.S,
    )
    if pb:
        block = pb.group(1)
    tm = re.search(rf'(?:{temp_patterns["tax_prefixes"]})[^\d\n]{{0,15}}(\d{{8,12}})', block, re.I)
    if tm:
        tax_id = tm.group(1)

    # Second pass: with supplier-specific patterns
    patterns = _build_patterns(tax_id, supplier, profile)

    number = date = None
    m = re.search(patterns['number_re'], flat, re.I)
    if m:
        number = m.group(1)
        date = _date(m.group(2))

    if date is None:
        dm = re.search(r'(?<!\d)(\d{1,2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{2,4})(?!\d)', flat)
        if dm:
            date = _date(dm.group(1))

    supplier = tax_id = None
    sm = re.search(rf'(?:{patterns["supplier_labels"]})\s*[:\s]\s*(.+)', flat, re.I)
    if sm:
        supplier = re.split(
            rf'\s{{2,}}|\s+(?:{patterns["supplier_stop"]})\b',
            sm.group(1).strip(),
            flags=re.I,
        )[0].strip(' .,;')
        supplier = _tidy_supplier(supplier, patterns['tax_prefixes'])

    block = t
    pb = re.search(
        rf'(?:{patterns["supplier_labels"]})[:\s]+(.+?)(?:\n\s*(?:{patterns["buyer_labels"]}))',
        t,
        re.I | re.S,
    )
    if pb:
        block = pb.group(1)
    tm = re.search(rf'(?:{patterns["tax_prefixes"]})[^\d\n]{{0,15}}(\d{{8,12}})', block, re.I)
    if tm:
        tax_id = tm.group(1)

    total = vat = None
    vat_found = False
    for line in t.splitlines():
        low = line.lower()
        if (
            re.search(rf'(?:{patterns["total_re"]})', low)
            or total is None
            and re.match(rf'\s*(?:{patterns["subtotal_re"]})\b', low)
        ):
            value = _last_money(line)
            if value is not None:
                total = value
        if re.search(rf'(?:{patterns["vat_re"]})', low) and not vat_found:
            if re.search(patterns['vat_zero_re'], low):
                vat = 0.0
                vat_found = True
            else:
                # OCR routinely turns «20%» into «2090», so an amount on a VAT
                # line counts even without the detail marker.
                value = _last_money(line)
                if value is not None:
                    vat = value
                    vat_found = True

    if total is None:
        total = _last_money(flat)

    currency = patterns['currency']
    for code, markers in profile.CURRENCY.items():
        if any(re.search(marker, flat, re.I) for marker in markers):
            currency = code
            break

    phone = _extract_phone(t)
    email = _extract_email(t)
    iban = _extract_iban(t)
    bank = _label_value(t, patterns['bank_labels'])
    address = _label_value(t, patterns['address_labels'])
    contact_person = _label_value(t, patterns['contact_labels'])

    fields = {
        'number': _field(number, 'Invoice number', 0.95 if number else 0.0),
        'date': _field(date, 'Invoice date', 0.9 if date else 0.0),
        'supplier': _field(supplier, 'Supplier block', 0.85 if supplier else 0.0),
        'tax_id': _field(tax_id, 'ЄДРПОУ / ІПН', 0.85 if tax_id else 0.0),
        'total': _field(total, 'Amount due', 0.9 if total is not None else 0.0),
        'vat': _field(vat, 'VAT (ПДВ)', 0.75 if vat is not None else 0.0),
        'currency': _field(currency, 'Detected from text', 0.6),
        'address': _field(address, 'Адреса', 0.7 if address else 0.0),
        'phone': _field(phone, 'Телефон', 0.8 if phone else 0.0),
        'email': _field(email, 'Email', 0.8 if email else 0.0),
        'bank': _field(bank, 'Банк', 0.65 if bank else 0.0),
        'iban': _field(iban, 'IBAN', 0.8 if iban else 0.0),
        'contact_person': _field(contact_person, 'Контактна особа', 0.65 if contact_person else 0.0),
    }

    validation = validate(fields, items)
    for key, passed in validation['ok'].items():
        if passed is False:
            if key == 'items_sum':
                for field_key in ('total', 'vat'):
                    fields[field_key]['confidence'] = round(
                        fields[field_key]['confidence'] * 0.5, 2
                    )
            elif key in fields:
                fields[key]['confidence'] = round(fields[key]['confidence'] * 0.5, 2)

    core_confidences = [fields[key]['confidence'] for key in profile.CORE_FIELD_KEYS]
    overall = round(sum(core_confidences) / len(core_confidences), 2)

    return {
        'fields': fields,
        'confidence': overall,
        'type': profile.CODE,
        'issues': validation['issues'],
        'type_label': profile.NAME,
        'doc_title': profile.DOC_TITLE,
    }


def parse_items(text: str) -> list[dict]:
    items = []
    for line in _clean(text).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = re.split(r'\s{2,}', stripped)
        if len(parts) >= 5 and re.fullmatch(r'\d+[.)]?', parts[0]):
            qty = _num(parts[-3])
            price = _money(parts[-2])
            amount = _money(parts[-1])
            name = ' '.join(parts[1:-3]).strip()
            if name and qty is not None and price is not None and amount is not None:
                items.append(
                    {
                        'name': name,
                        'qty': qty,
                        'price': price,
                        'amount': amount,
                    }
                )
                continue
        m = re.match(
            r'\s*\d+[.)]?\s+(?P<name>.+?)\s{2,}'
            r'(?P<qty>\d+(?:[.,]\d+)?)\s{2,}'
            r'(?P<price>[\d\s\u00a0]+[.,]\d{2})\s{2,}'
            r'(?P<amount>[\d\s\u00a0]+[.,]\d{2})\s*$',
            line,
        )
        if m:
            items.append(
                {
                    'name': m.group('name').strip(),
                    'qty': _num(m.group('qty')),
                    'price': _money(m.group('price')),
                    'amount': _money(m.group('amount')),
                }
            )
    return items
