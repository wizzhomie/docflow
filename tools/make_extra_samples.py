"""Generate a few extra invoices for hands-on testing.

Run: python3 tools/make_extra_samples.py

Creates three documents in `samples/` so they appear as chips in the UI:

* invoice_05 — Ukrainian invoice where the supplier line carries the tax id
  inline (the case that used to leak ЄДРПОУ into the supplier name);
* invoice_06 — Russian-language invoice (fallback labels, ИНН);
* invoice_07 — the same Ukrainian invoice saved as an image-only PDF, which is
  what a scan looks like to the parser (forces the OCR path).
"""

import subprocess
from pathlib import Path

from PIL import Image
from weasyprint import HTML

BASE = Path(__file__).resolve().parents[1]
SAMPLES = BASE / 'samples'

CSS = """
@page { size: A4; margin: 20mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 11px; color: #1f1f1f; }
h1 { font-size: 16px; margin: 0 0 4px; }
.sub { color: #777; font-size: 10px; margin-bottom: 14px; }
.line { border-top: 1px solid #999; margin: 10px 0 14px; }
table { width: 100%; border-collapse: collapse; }
td { padding: 4px 6px; vertical-align: top; }
td.k { color: #666; width: 120px; }
table.items { margin-top: 12px; border: 1px solid #bbb; }
table.items th { background: #eee; border: 1px solid #bbb; padding: 5px; font-size: 9.5px; text-align: left; }
table.items td { border: 1px solid #ccc; }
.n { text-align: right; }
.grand { margin-top: 14px; font-size: 13px; font-weight: 700; text-align: right; }
"""


def fmt(value: float) -> str:
    return f'{value:,.2f}'.replace(',', ' ')


def ukrainian_inline_tax() -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Рахунок на оплату № 778 від 21.10.2026</h1>
<div class="sub">ФОП Гриценко Ірина Петрівна · м. Дніпро</div>
<div class="line"></div>
<table>
  <tr><td class="k">Продавець:</td><td><b>ФОП Гриценко Ірина Петрівна, ІПН 2987654321, м. Дніпро, вул. Набережна, 7</b></td></tr>
  <tr><td class="k">Замовник:</td><td>ТОВ «Промінь-Логістик», ЄДРПОУ 40123456</td></tr>
</table>
<table class="items">
  <tr><th>№</th><th>Найменування</th><th class="n">К-ть</th><th class="n">Ціна</th><th class="n">Сума</th></tr>
  <tr><td>1</td><td>Перевезення вантажу Дніпро — Київ</td><td class="n">4</td><td class="n">9 500,00</td><td class="n">38 000,00</td></tr>
  <tr><td>2</td><td>Експедиторські послуги</td><td class="n">1</td><td class="n">4 200,00</td><td class="n">4 200,00</td></tr>
</table>
<div class="grand">Сума до сплати: 42 200,00 грн</div>
<div>ПДВ 20%: 7 033,33 грн</div>
</body></html>"""


def russian_invoice() -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Счёт на оплату № 88 от 05.11.2026</h1>
<div class="sub">ООО «Северный Ветер» · г. Харьков</div>
<div class="line"></div>
<table>
  <tr><td class="k">Поставщик:</td><td><b>ООО «Северный Ветер», ИНН 6449012345</b></td></tr>
  <tr><td class="k">Покупатель:</td><td>ООО «Дельта-Строй», ИНН 6450123456</td></tr>
</table>
<table class="items">
  <tr><th>№</th><th>Наименование</th><th class="n">Кол-во</th><th class="n">Цена</th><th class="n">Сумма</th></tr>
  <tr><td>1</td><td>Металлопрокат, т</td><td class="n">3</td><td class="n">32 000,00</td><td class="n">96 000,00</td></tr>
  <tr><td>2</td><td>Доставка</td><td class="n">1</td><td class="n">6 000,00</td><td class="n">6 000,00</td></tr>
</table>
<div class="grand">Всего к оплате: 102 000,00 грн</div>
<div>НДС 20%: 17 000,00 грн</div>
</body></html>"""


def main() -> int:
    SAMPLES.mkdir(exist_ok=True)

    ukr = SAMPLES / 'invoice_05_778-inline-tax.pdf'
    HTML(string=ukrainian_inline_tax()).write_pdf(ukr)

    ru = SAMPLES / 'invoice_06_ru-88.pdf'
    HTML(string=russian_invoice()).write_pdf(ru)

    # A scan: render the page to an image and wrap it back into a PDF, so the
    # text layer disappears and the OCR path is exercised.
    png = SAMPLES / '.invoice_05_page'
    subprocess.run(
        ['pdftoppm', '-r', '150', '-png', '-singlefile', str(ukr), str(png)],
        check=True,
    )
    image = Image.open(png.with_suffix('.png')).convert('RGB')
    scan = SAMPLES / 'invoice_07_scan-778.pdf'
    image.save(scan, 'PDF', resolution=150)
    png.with_suffix('.png').unlink()

    for path in (ukr, ru, scan):
        print('created', path.relative_to(BASE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
