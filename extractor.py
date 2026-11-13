import re
import subprocess
from pathlib import Path


MONEY = r"([\d\u00a0\s]+[.,]\d{2})"


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".html", ".htm"}:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
        raw = re.sub(r"</(p|div|tr|h\d|li)>", "\n", raw, flags=re.I)
        raw = re.sub(r"</t[dh]>", "  ", raw, flags=re.I)
        raw = re.sub(r"<[^>]+>", "", raw)
        raw = raw.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
        raw = raw.replace("&amp;", "&").replace("&mdash;", "—")
        return raw
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, check=True, timeout=30,
        )
        text = out.stdout.decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    if not text.strip() and suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            text = ""
    return text


def _clean(text: str) -> str:
    return text.replace("\u00a0", " ").replace("\r", "")


def _money(raw: str | None) -> float | None:
    if not raw:
        return None
    value = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(value), 2)
    except ValueError:
        return None


def _date(raw: str | None) -> str | None:
    if not raw:
        return None
    parts = re.split(r"[.\-/]", raw)
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = "20" + year
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def _field(value, source, confidence):
    return {"value": value, "source": source, "confidence": confidence}


def _last_money(line: str) -> float | None:
    matches = re.findall(MONEY, line)
    return _money(matches[-1]) if matches else None


def parse_invoice(text: str) -> dict:
    t = _clean(text)
    flat = re.sub(r"[ \t]+", " ", t)

    number = date = None
    m = re.search(
        r"Сч[её]т(?:\s+на\s+оплату)?\s*№\s*([A-Za-zА-Яа-я0-9\-/]+)\s*(?:от\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}))?",
        flat, re.I,
    )
    if m:
        number = m.group(1)
        date = _date(m.group(2))

    supplier = inn = kpp = None
    sm = re.search(r"Поставщик[:\s]+(.+)", flat, re.I)
    if sm:
        supplier = re.split(r"\s{2,}|\s+(?:ИНН|КПП|Адрес|Покупатель)\b", sm.group(1).strip())[0].strip(" .,;")

    block = t
    pb = re.search(r"Поставщик[:\s]+(.+?)(?:\n\s*Покупатель|\n\s*Заказчик|\n\s*Плательщик)", t, re.I | re.S)
    if pb:
        block = pb.group(1)
    im = re.search(r"ИНН\s*[:№]?\s*(\d{10,12})", block)
    if im:
        inn = im.group(1)
    km = re.search(r"КПП\s*[:№]?\s*(\d{9})", block)
    if km:
        kpp = km.group(1)

    total = vat = None
    vat_found = False
    for line in t.splitlines():
        low = line.lower()
        if "всего к оплате" in low:
            value = _last_money(line)
            if value is not None:
                total = value
        elif total is None and re.match(r"\s*итого\b", low):
            value = _last_money(line)
            if value is not None:
                total = value
        if "ндс" in low and not vat_found:
            if re.search(r"без\s+ндс", low):
                vat = 0.0
                vat_found = True
            elif "%" in low or "том числе" in low:
                value = _last_money(line)
                if value is not None:
                    vat = value
                    vat_found = True

    if total is None:
        value = _last_money(flat)
        total = value

    currency = "RUB"
    if re.search(r"\b(USD|EUR|\$|€)\b", flat):
        currency = "USD" if re.search(r"USD|\$", flat) else "EUR"

    fields = {
        "number": _field(number, "Счёт на оплату", 0.95 if number else 0.0),
        "date": _field(date, "Счёт на оплату", 0.9 if date else 0.0),
        "supplier": _field(supplier, "Блок «Поставщик»", 0.85 if supplier else 0.0),
        "inn": _field(inn, "Блок «Поставщик»", 0.85 if inn else 0.0),
        "kpp": _field(kpp, "Блок «Поставщик»", 0.7 if kpp else 0.0),
        "total": _field(total, "Итоговая сумма", 0.9 if total is not None else 0.0),
        "vat": _field(vat, "Сумма НДС", 0.75 if vat is not None else 0.0),
        "currency": _field(currency, "Определено по тексту", 0.6),
    }

    found = [f for f in fields.values() if f["confidence"] > 0]
    overall = round(sum(f["confidence"] for f in found) / len(fields), 2)

    return {"fields": fields, "confidence": overall}


def parse_items(text: str) -> list[dict]:
    items = []
    for line in _clean(text).splitlines():
        m = re.match(
            r"\s*\d+[.)]?\s+(.+?)\s{2,}(\d[\d\s]*)\s+" + MONEY + r"\s+" + MONEY + r"\s*$",
            line,
        )
        if m:
            items.append({
                "name": m.group(1).strip(),
                "qty": _money(m.group(2) + ",00") if "," not in m.group(2) else _money(m.group(2)),
                "price": _money(m.group(3)),
                "amount": _money(m.group(4)),
            })
    return items
