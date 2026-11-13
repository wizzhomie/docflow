import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from profiles import invoice_ua as base_profile
from profiles.suppliers import get_supplier_profile
from validation import validate

MONEY = r'([\d\u00a0\s]+[.,]\d{2})'

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}
OCR_LANGS = 'ukr+eng'


def _build_patterns(tax_id: str | None, supplier_name: str | None) -> dict:
    """Build regex patterns from base + supplier profile."""
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
    total_re = '|'.join(base_profile.TOTAL_LABELS)
    subtotal_re = '|'.join(base_profile.SUBTOTAL_LABELS)
    vat_re = '|'.join(base_profile.VAT_LABELS)
    number_re = (
        base_profile.TITLE_RE
        + r'(?:\s+на\s+оплату)?\s*(?:'
        + '|'.join(base_profile.NUMBER_MARKERS)
        + r')\s*'
        r'([A-Za-zА-Яа-яІіЇїЄєҐґ0-9\-/]+)'
        r'(?:\s*(?:' + '|'.join(base_profile.DATE_PREFIXES) + r')\s*'
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
        'currency': overrides.get('currency', base_profile.DEFAULT_CURRENCY),
        'vat_rate': overrides.get('vat_rate', base_profile.VAT_RATE),
        'vat_zero_re': base_profile.VAT_ZERO_RE,
        'vat_detail_re': base_profile.VAT_DETAIL_RE,
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


def parse_invoice(
    text: str,
    items: list[dict] | None = None,
    tax_id: str | None = None,
    supplier_name: str | None = None,
) -> dict:
    t = _clean(text)
    flat = re.sub(r'[ \t]+', ' ', t)

    # First pass: extract tax_id and supplier to load supplier profile
    temp_patterns = _build_patterns(None, None)
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
    patterns = _build_patterns(tax_id, supplier)

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
            elif re.search(patterns['vat_detail_re'], low):
                value = _last_money(line)
                if value is not None:
                    vat = value
                    vat_found = True

    if total is None:
        total = _last_money(flat)

    currency = patterns['currency']
    for code, markers in base_profile.CURRENCY.items():
        if any(re.search(marker, flat, re.I) for marker in markers):
            currency = code
            break

    fields = {
        'number': _field(number, 'Invoice number', 0.95 if number else 0.0),
        'date': _field(date, 'Invoice date', 0.9 if date else 0.0),
        'supplier': _field(supplier, 'Supplier block', 0.85 if supplier else 0.0),
        'tax_id': _field(tax_id, 'ЄДРПОУ / ІПН', 0.85 if tax_id else 0.0),
        'total': _field(total, 'Amount due', 0.9 if total is not None else 0.0),
        'vat': _field(vat, 'VAT (ПДВ)', 0.75 if vat is not None else 0.0),
        'currency': _field(currency, 'Detected from text', 0.6),
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

    overall = round(sum(f['confidence'] for f in fields.values()) / len(fields), 2)

    return {
        'fields': fields,
        'confidence': overall,
        'type': base_profile.CODE,
        'issues': validation['issues'],
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
