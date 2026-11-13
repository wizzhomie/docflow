from pathlib import Path
from weasyprint import HTML

OUT = Path(__file__).parent / "samples"
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


def invoice(num, date, supplier, inn, kpp, address, buyer, buyer_inn, items, vat_rate=20):
    subtotal = sum(i[3] for i in items)
    vat = round(subtotal * vat_rate / (100 + vat_rate), 2) if vat_rate else 0.0
    rows = ""
    for idx, (name, qty, price, amount) in enumerate(items, 1):
        rows += (
            f"<tr><td class='num'>{idx}</td><td>{name}</td>"
            f"<td class='num'>{qty}</td><td class='num'>{fmt(price)}</td>"
            f"<td class='num'>{fmt(amount)}</td></tr>"
        )
    vat_line = f"В том числе НДС {vat_rate}%:" if vat_rate else "Без НДС:"
    vat_val = fmt(vat) if vat_rate else "—"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
  <div class="head">
    <div>
      <div class="muted">Образец документа</div>
      <h1>Счёт на оплату № {num} от {date}</h1>
    </div>
    <div class="bank">
      Банк: АО «Тест-Банк»<br>
      БИК: 044525000<br>
      Р/с: 40702810000000000123
    </div>
  </div>
  <table class="parties">
    <tr>
      <td class="label">Поставщик:</td>
      <td><b>{supplier}</b><br>ИНН {inn}, КПП {kpp}<br>{address}</td>
    </tr>
    <tr>
      <td class="label">Покупатель:</td>
      <td><b>{buyer}</b><br>ИНН {buyer_inn}</td>
    </tr>
  </table>
  <table>
    <thead><tr>
      <th class="num">№</th><th>Наименование работ (услуг)</th>
      <th class="num">Кол-во</th><th class="num">Цена, руб.</th><th class="num">Сумма, руб.</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <table class="total">
    <tr><td>Итого:</td><td class="num">{fmt(subtotal)}</td></tr>
    <tr><td>{vat_line}</td><td class="num">{vat_val}</td></tr>
    <tr class="grand"><td>Всего к оплате:</td><td class="num">{fmt(subtotal)}</td></tr>
  </table>
  <div class="sign">
    <div>Руководитель ______________________</div>
    <div>Главный бухгалтер ______________________</div>
  </div>
</body></html>"""


def fmt(value):
    s = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return s


SAMPLES = [
    dict(
        num="128", date="12.03.2026",
        supplier="ООО «Ромашка»", inn="7701234567", kpp="770101001",
        address="г. Москва, ул. Ленина, д. 10, офис 4",
        buyer="ООО «Вектор»", buyer_inn="7809876543",
        items=[
            ("Разработка корпоративного сайта", 1, 50000.00, 50000.00),
            ("Техническая поддержка, мес.", 3, 10000.00, 30000.00),
        ],
    ),
    dict(
        num="2026-441", date="05.11.2026",
        supplier="ИП Смирнов Алексей Петрович", inn="504712345678", kpp="—",
        address="Московская обл., г. Химки, ул. Мира, д. 7",
        buyer="АО «Северный путь»", buyer_inn="7712345678",
        items=[
            ("Аудит складской логистики", 1, 120000.00, 120000.00),
            ("Консультации по внедрению WMS", 8, 7500.00, 60000.00),
            ("Обучение персонала", 2, 15000.00, 30000.00),
        ],
    ),
    dict(
        num="55", date="28.01.2026",
        supplier="ООО «Чистый дом»", inn="5023456789", kpp="502301001",
        address="г. Красногорск, ул. Садовая, д. 3",
        buyer="ООО «Уют-Сервис»", buyer_inn="5034567890",
        items=[
            ("Уборка офисных помещений, мес.", 6, 25000.00, 150000.00),
        ],
        vat_rate=0,
    ),
    dict(
        num="КП-77", date="19.06.2026",
        supplier="ООО «ТехноСофт»", inn="7723456789", kpp="772301001",
        address="г. Москва, Пресненская наб., д. 12",
        buyer="ООО «Логика»", buyer_inn="7745678901",
        items=[
            ("Лицензия DocFlow Pro, год", 25, 4800.00, 120000.00),
            ("Внедрение и настройка", 1, 45000.00, 45000.00),
        ],
    ),
]

for index, s in enumerate(SAMPLES, 1):
    html = invoice(**s)
    name = f"invoice_{index:02d}_{s['num'].replace('/', '-')}.pdf"
    HTML(string=html).write_pdf(OUT / name)
    print("created", OUT / name)
