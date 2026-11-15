"""Generate one delivery note sample (Видаткова накладна) for the demo.

Run: python3 tools/make_delivery_sample.py

The fiction: ТОВ «Смак-Дистрибуція» receives goods from ТОВ «Терра-Буд». The
document carries full requisites and a total that is neither above the approval
threshold nor a duplicate, so the first upload goes through the normal confirm
path and routes to Accounting — the third document type on the registry.

Created in `samples/`:
* delivery_01_2026-77.pdf — goods, VAT included, total «Всього».
"""

from pathlib import Path

from weasyprint import HTML

BASE = Path(__file__).resolve().parents[1]
SAMPLES = BASE / 'samples'

SUPPLIER = 'ТОВ «Терра-Буд», ЄДРПОУ 33445566, м. Київ'
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
td.k { color: #666; width: 130px; }
table.items { margin-top: 12px; border: 1px solid #bbb; }
table.items th { background: #eee; border: 1px solid #bbb; padding: 5px; font-size: 9.5px; text-align: left; }
table.items td { border: 1px solid #ccc; }
.n { text-align: right; }
.grand { margin-top: 14px; font-size: 13px; font-weight: 700; text-align: right; }
"""


def fmt(value: float) -> str:
    return f'{value:,.2f}'.replace(',', ' ')


def delivery_note() -> str:
    items = [
        ('Цемент М400, мішок 50 кг', 40, 250.0),
        ('Пісок будівельний, т', 10, 900.0),
        ('Доставка на склад', 1, 2000.0),
    ]
    rows = ''.join(
        f'<tr><td>{index}</td><td>{name}</td><td class="n">{quantity}</td>'
        f'<td class="n">{fmt(price)}</td><td class="n">{fmt(quantity * price)}</td></tr>'
        for index, (name, quantity, price) in enumerate(items, start=1)
    )
    total = sum(quantity * price for _, quantity, price in items)
    vat = round(total * 20 / 120, 2)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Видаткова накладна № 2026-77 від 12.11.2026</h1>
<div class="sub">ТОВ «Терра-Буд» · м. Київ</div>
<div class="line"></div>
<table>
  <tr><td class="k">Постачальник:</td><td><b>{SUPPLIER}</b></td></tr>
  <tr><td class="k">Покупець:</td><td>{RECEIVER}</td></tr>
  <tr><td class="k">Адреса:</td><td>м. Київ, вул. Будівельна, 7</td></tr>
  <tr><td class="k">Тел.:</td><td>+380 (44) 555-77-88</td></tr>
  <tr><td class="k">Email:</td><td>warehouse@terra-bud.ua</td></tr>
  <tr><td class="k">Банк:</td><td>АТ «Ощадбанк»</td></tr>
  <tr><td class="k">IBAN:</td><td>UA55 1234 5678 9012 3456 7890 1234 5</td></tr>
  <tr><td class="k">Контактна особа:</td><td>Олена Ткаченко</td></tr>
</table>
<table class="items">
  <tr><th>№</th><th>Найменування</th><th class="n">К-ть</th><th class="n">Ціна</th><th class="n">Сума</th></tr>
  {rows}
</table>
<div class="grand">Всього: {fmt(total)} грн</div>
<div>ПДВ 20%: {fmt(vat)} грн</div>
<div class="sub">Товар отримано повністю, претензій немає.</div>
</body></html>"""


def main() -> int:
    SAMPLES.mkdir(exist_ok=True)
    target = SAMPLES / 'delivery_01_2026-77.pdf'
    HTML(string=delivery_note()).write_pdf(target)
    print('created', target.relative_to(BASE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
