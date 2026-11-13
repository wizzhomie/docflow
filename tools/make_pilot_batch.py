"""Generate a fresh batch of incoming invoices for a hands-on walkthrough.

Run: python3 tools/make_pilot_batch.py

The fiction: ТОВ «Смак-Дистрибуція» (a distributor) receives four supplier
invoices during one week. Every number, date and tax ID is new, so none of these
documents collides with the regression samples or with the demo history — which
means the first upload of each one goes through the normal confirm path instead
of being blocked as a duplicate.

Created in `samples/`:
* pilot_01_2026-501.pdf — services, VAT included in the total;
* pilot_02_2026-502.pdf — a private entrepreneur (ФОП), inline tax ID;
* pilot_03_2026-503.pdf — goods, larger amount;
* pilot_04_scan_2026-504.pdf — image-only PDF, exercises the OCR path.
"""

import subprocess
from pathlib import Path

from PIL import Image
from weasyprint import HTML

BASE = Path(__file__).resolve().parents[1]
SAMPLES = BASE / 'samples'

RECEIVER = 'ТОВ «Смак-Дистрибуція», ЄДРПОУ 41234567'

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


def invoice(
    *,
    number: str,
    date: str,
    supplier: str,
    tax_label: str,
    tax_id: str,
    city: str,
    items: list[tuple[str, int, float]],
    vat: float,
) -> str:
    total = sum(quantity * price for _, quantity, price in items)
    rows = ''.join(
        f'<tr><td>{index}</td><td>{name}</td><td class="n">{quantity}</td>'
        f'<td class="n">{fmt(price)}</td><td class="n">{fmt(quantity * price)}</td></tr>'
        for index, (name, quantity, price) in enumerate(items, start=1)
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Рахунок на оплату № {number} від {date}</h1>
<div class="sub">{supplier} · {city}</div>
<div class="line"></div>
<table>
  <tr><td class="k">Постачальник:</td><td><b>{supplier}, {tax_label} {tax_id}, {city}</b></td></tr>
  <tr><td class="k">Покупець:</td><td>{RECEIVER}</td></tr>
</table>
<table class="items">
  <tr><th>№</th><th>Найменування</th><th class="n">К-ть</th><th class="n">Ціна</th><th class="n">Сума</th></tr>
  {rows}
</table>
<div class="grand">Сума до сплати: {fmt(total)} грн</div>
<div>ПДВ 20%: {fmt(vat)} грн</div>
</body></html>"""


DOCUMENTS = {
    'pilot_01_2026-501.pdf': dict(
        number='2026-501',
        date='02.11.2026',
        supplier='ТОВ «Терра-Буд»',
        tax_label='ЄДРПОУ',
        tax_id='33445566',
        city='м. Київ',
        items=[('Ремонт складського приміщення, м²', 40, 2500.0), ('Вивіз будівельного сміття', 1, 28000.0)],
        vat=21333.33,
    ),
    'pilot_02_2026-502.pdf': dict(
        number='2026-502',
        date='04.11.2026',
        supplier='ФОП Коваль Олег Іванович',
        tax_label='ІПН',
        tax_id='3210987654',
        city='м. Львів',
        items=[('Перевезення товару Львів — Київ', 2, 9500.0), ('Експедиторські послуги', 1, 6400.0)],
        vat=4233.33,
    ),
    'pilot_03_2026-503.pdf': dict(
        number='2026-503',
        date='06.11.2026',
        supplier='ТОВ «Агро-Південь»',
        tax_label='ЄДРПОУ',
        tax_id='45671234',
        city='м. Херсон',
        items=[('Цукор, кг', 3000, 21.0), ('Олія соняшникова, л', 400, 90.75)],
        vat=16550.0,
    ),
    'pilot_04_scan_2026-504.pdf': dict(
        number='2026-504',
        date='07.11.2026',
        supplier='ТОВ «Світ-Пак»',
        tax_label='ЄДРПОУ',
        tax_id='51234098',
        city='м. Одеса',
        items=[('Пакет паперовий, шт', 2000, 7.0), ('Коробка картонна, шт', 400, 11.5)],
        vat=3100.0,
    ),
}


def main() -> int:
    SAMPLES.mkdir(exist_ok=True)
    created = []
    for name, kwargs in DOCUMENTS.items():
        target = SAMPLES / name
        HTML(string=invoice(**kwargs)).write_pdf(target)
        created.append(target)

    # The last one becomes a scan: no text layer, so only OCR can read it.
    source = SAMPLES / 'pilot_04_scan_2026-504.pdf'
    png = SAMPLES / '.pilot_04_page'
    subprocess.run(['pdftoppm', '-r', '150', '-png', '-singlefile', str(source), str(png)], check=True)
    image = Image.open(png.with_suffix('.png')).convert('RGB')
    image.save(source, 'PDF', resolution=150)
    png.with_suffix('.png').unlink()

    for path in created:
        print('created', path.relative_to(BASE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
