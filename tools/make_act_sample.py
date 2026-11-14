"""Generate act-of-completed-work samples for the second document type.

Run: python3 tools/make_act_sample.py

Creates two documents in `samples/`:

* act_01_2026-14.pdf — a clean украинский «Акт виконаних робіт» with supplier
  requisites, so the profile is not only detected but fills the CRM card;
* act_02_scan_2026-15.pdf — the same kind of act saved as an image-only PDF,
  which forces the OCR path for a non-invoice document.

Both have unique numbers and tax IDs, so a first upload goes through the normal
confirm path instead of duplicate protection.
"""

import subprocess
from pathlib import Path

from PIL import Image
from weasyprint import HTML

BASE = Path(__file__).resolve().parents[1]
SAMPLES = BASE / 'samples'

BUYER = 'ТОВ «Смак-Дистрибуція», ЄДРПОУ 41234567'

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


def act(*, number, date, executor, tax_label, tax_id, address, phone, email, bank, iban, contact, items, vat):
    total = sum(quantity * price for _, quantity, price in items)
    rows = ''.join(
        f'<tr><td>{index}</td><td>{name}</td><td class="n">{quantity}</td>'
        f'<td class="n">{fmt(price)}</td><td class="n">{fmt(quantity * price)}</td></tr>'
        for index, (name, quantity, price) in enumerate(items, start=1)
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Акт виконаних робіт № {number} від {date}</h1>
<div class="sub">{executor} · {address}</div>
<div class="line"></div>
<table>
  <tr><td class="k">Виконавець:</td><td><b>{executor}, {tax_label} {tax_id}</b></td></tr>
  <tr><td class="k">Замовник:</td><td>{BUYER}</td></tr>
  <tr><td class="k">Адреса:</td><td>{address}</td></tr>
  <tr><td class="k">Тел.:</td><td>{phone}</td></tr>
  <tr><td class="k">Email:</td><td>{email}</td></tr>
  <tr><td class="k">Банк:</td><td>{bank}</td></tr>
  <tr><td class="k">IBAN:</td><td>{iban}</td></tr>
  <tr><td class="k">Контактна особа:</td><td>{contact}</td></tr>
</table>
<table class="items">
  <tr><th>№</th><th>Найменування робіт</th><th class="n">К-ть</th><th class="n">Ціна</th><th class="n">Сума</th></tr>
  {rows}
</table>
<div class="grand">Вартість робіт: {fmt(total)} грн</div>
<div>ПДВ 20%: {fmt(vat)} грн</div>
</body></html>"""


DOCUMENTS = {
    'act_01_2026-14.pdf': dict(
        number='2026-14',
        date='11.11.2026',
        executor='ТОВ «Клін-Сервіс»',
        tax_label='ЄДРПОУ',
        tax_id='33456789',
        address='м. Київ, вул. Клінінгова, 5',
        phone='+380 (44) 222-33-44',
        email='office@klin-service.ua',
        bank='АТ «ОТП Банк»',
        iban='UA88 1111 2222 3333 4444 5555 6666 9',
        contact='Наталія Гриб',
        items=[('Прибирання офісу, м²', 120, 45.0), ('Мийка вікон, м²', 40, 80.0)],
        vat=1433.33,
    ),
    'act_02_scan_2026-15.pdf': dict(
        number='2026-15',
        date='12.11.2026',
        executor='ТОВ «Ремонт-Груп»',
        tax_label='ЄДРПОУ',
        tax_id='44556677',
        address='м. Дніпро, вул. Робоча, 9',
        phone='+380 (56) 777-88-99',
        email='info@remont-group.ua',
        bank='АТ «Креді Агріколь»',
        iban='UA22 1111 2222 3333 4444 5555 6666 4',
        contact='Віктор Демченко',
        items=[('Поточний ремонт стелі, м²', 60, 300.0)],
        vat=3000.0,
    ),
}


def main() -> int:
    SAMPLES.mkdir(exist_ok=True)
    created = []
    for name, kwargs in DOCUMENTS.items():
        path = SAMPLES / name
        HTML(string=act(**kwargs)).write_pdf(path)
        created.append(path)

    scan = SAMPLES / 'act_02_scan_2026-15.pdf'
    png = SAMPLES / '.act_02_page'
    subprocess.run(['pdftoppm', '-r', '150', '-png', '-singlefile', str(scan), str(png)], check=True)
    image = Image.open(png.with_suffix('.png')).convert('RGB')
    image.save(scan, 'PDF', resolution=150)
    png.with_suffix('.png').unlink()

    for path in created:
        print('created', path.relative_to(BASE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
