from pathlib import Path

from weasyprint import HTML

OUT = Path(__file__).parent / 'samples'
OUT.mkdir(exist_ok=True)

CSS = """
@page { size: A4; margin: 18mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 11px; color: #1a1a1a; }
h1 { font-size: 17px; margin: 0 0 2px; }
.muted { color: #666; }
.head { display: flex; justify-content: space-between; border-bottom: 2px solid #222; padding-bottom: 10px; margin-bottom: 14px; }
.bank { font-size: 9.5px; line-height: 1.5; color: #333; }
.parties { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
.parties td { border: 1px solid #bbb; padding: 8px 10px; vertical-align: top; }
.parties td.label { width: 95px; background: #f7f7f7; font-size: 9px; color: #666; }
.parties b { font-size: 11px; }
table { width: 100%; border-collapse: collapse; margin-top: 6px; }
th { background: #f0f0f0; border: 1px solid #bbb; padding: 6px; font-size: 9.5px; text-align: left; }
td { border: 1px solid #ccc; padding: 6px; }
td.num, th.num { text-align: right; }
tfoot td { font-weight: 600; }
.total { margin-top: 12px; width: 55%; margin-left: auto; }
.total tr td { border: none; padding: 4px 6px; }
.total .grand td { font-size: 13px; font-weight: 700; border-top: 2px solid #222; padding-top: 8px; }
.sign { margin-top: 40px; display: flex; justify-content: space-between; font-size: 10px; }
"""


def invoice(num, date, supplier, tax_id, address, buyer, buyer_tax_id, items, vat_rate=20):
    subtotal = sum(i[3] for i in items)
    vat = round(subtotal * vat_rate / (100 + vat_rate), 2) if vat_rate else 0.0
    rows = ''
    for idx, (name, qty, price, amount) in enumerate(items, 1):
        rows += (
            f"<tr><td class='num'>{idx}</td><td>{name}</td>"
            f"<td class='num'>{qty}</td><td class='num'>{fmt(price)}</td>"
            f"<td class='num'>{fmt(amount)}</td></tr>"
        )
    vat_line = f'ПДВ {vat_rate}%:' if vat_rate else 'Без ПДВ:'
    vat_val = fmt(vat) if vat_rate else '—'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
  <div class="head">
    <div>
      <div class="muted">Зразок документа</div>
      <h1>Рахунок на оплату № {num} від {date}</h1>
    </div>
    <div class="bank">
      Банк: АТ «Тест-Банк»<br>
      МФО: 322313<br>
      Р/р: UA903052992990004149123456789
    </div>
  </div>
  <table class="parties">
    <tr>
      <td class="label">Постачальник:</td>
      <td><b>{supplier}</b><br>ЄДРПОУ/ІПН {tax_id}<br>{address}</td>
    </tr>
    <tr>
      <td class="label">Покупець:</td>
      <td><b>{buyer}</b><br>ЄДРПОУ {buyer_tax_id}</td>
    </tr>
  </table>
  <table>
    <thead><tr>
      <th class="num">№</th><th>Найменування робіт (послуг)</th>
      <th class="num">Кількість</th><th class="num">Ціна, грн.</th><th class="num">Сума, грн.</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <table class="total">
    <tr><td>Разом:</td><td class="num">{fmt(subtotal)}</td></tr>
    <tr><td>{vat_line}</td><td class="num">{vat_val}</td></tr>
    <tr class="grand"><td>Всього до сплати:</td><td class="num">{fmt(subtotal)}</td></tr>
  </table>
  <div class="sign">
    <div>Керівник ______________________</div>
    <div>Головний бухгалтер ______________________</div>
  </div>
</body></html>"""


def fmt(value):
    s = f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
    return s


SAMPLES = [
    {
        'num': '128',
        'date': '12.03.2026',
        'supplier': 'ТОВ «Ромашка»',
        'tax_id': '12345678',
        'address': 'м. Київ, вул. Хрещатик, 10, офіс 4',
        'buyer': 'ТОВ «Вектор»',
        'buyer_tax_id': '87654321',
        'items': [
            ('Розробка корпоративного сайту', 1, 50000.00, 50000.00),
            ('Технічна підтримка, міс.', 3, 10000.00, 30000.00),
        ],
    },
    {
        'num': '2026-441',
        'date': '05.11.2026',
        'supplier': 'ФОП Коваленко Олена Іванівна',
        'tax_id': '1234567890',
        'address': 'Київська обл., м. Бровари, вул. Миру, 7',
        'buyer': 'ТОВ «Північний шлях»',
        'buyer_tax_id': '32109876',
        'items': [
            ('Аудит складської логістики', 1, 120000.00, 120000.00),
            ('Консультації щодо впровадження WMS', 8, 7500.00, 60000.00),
            ('Навчання персоналу', 2, 15000.00, 30000.00),
        ],
    },
    {
        'num': '55',
        'date': '28.01.2026',
        'supplier': 'ТОВ «Чистий дім»',
        'tax_id': '23456789',
        'address': 'м. Львів, вул. Садова, 3',
        'buyer': 'ТОВ «Затишок-Сервіс»',
        'buyer_tax_id': '34567890',
        'items': [
            ('Прибирання офісних приміщень, міс.', 6, 25000.00, 150000.00),
        ],
        'vat_rate': 0,
    },
    {
        'num': 'КП-77',
        'date': '19.06.2026',
        'supplier': 'ТОВ «ТехноСофт»',
        'tax_id': '45678901',
        'address': 'м. Київ, вул. Велика Васильківська, 12',
        'buyer': 'ТОВ «Логіка»',
        'buyer_tax_id': '56789012',
        'items': [
            ('Ліцензія DocFlow Pro, рік', 25, 4800.00, 120000.00),
            ('Впровадження та налаштування', 1, 45000.00, 45000.00),
        ],
    },
]

for index, s in enumerate(SAMPLES, 1):
    html = invoice(**s)
    name = f'invoice_{index:02d}_{s["num"].replace("/", "-")}.pdf'
    HTML(string=html).write_pdf(OUT / name)
    print('created', OUT / name)
