"""Generate a synthetic corpus of Ukrainian documents for an independent check.

Run: python3 tools/make_synthetic_corpus.py [--count 45] [--out data/synthetic-corpus]

Why this exists: the regression fixtures in `tests/document_cases.json` were
written next to the parser and tuned with it, so they answer "did the parser
change" and not "does it read documents it has never seen". Real invoices from a
live company would answer the second question best; there are none available for
a portfolio demo, and generating them with a model costs money. This generator is
the honest middle: the layout, wording, requisites and values are drawn from
pools by code, and the parser has never seen the result. The limitation is stated
in every report: these are variations of one distribution, not real scans of real
invoices.

The output is a manifest with the expected values next to the PDFs, so the
evaluation needs no manual labelling. Everything is deterministic: the same seed
gives the same corpus, which is what makes a number in a report reproducible.
Generated documents and the manifest live under `data/synthetic-corpus/`, which
is git-ignored — they are runtime data, not part of the product.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageFilter
from weasyprint import HTML

BASE = Path(__file__).resolve().parents[1]
DEFAULT_OUT = BASE / 'data' / 'synthetic-corpus'
SEED = 20261119

RECEIVER = 'ТОВ «Смак-Дистрибуція», ЄДРПОУ 41234567'

SURNAMES = ['Гриценко', 'Мельник', 'Ковальчук', 'Шевченко', 'Бондаренко', 'Ткачук',
            'Мороз', 'Лисенко', 'Савченко', 'Руденко', 'Марченко', 'Поліщук']
# First name with its patronymic: a document says «ФОП Ковальчук Марія Петрівна»,
# not «Марія Петрович», and a corpus should not need the parser to forgive that.
PEOPLE = [
    ('Ірина', 'Петрівна'), ('Олег', 'Іванович'), ('Наталія', 'Сергіївна'),
    ('Андрій', 'Миколайович'), ('Марія', 'Петрівна'), ('Сергій', 'Олегович'),
    ('Оксана', 'Андріївна'), ('Дмитро', 'Вікторович'), ('Тетяна', 'Юріївна'),
    ('Юрій', 'Олександрович'),
]

COMPANIES = [
    'Смак-Дистрибуція', 'Агро-Сервіс', 'ТехноПостач', 'Київ-Папір', 'Буд-Майстер',
    'Мед-Техніка', 'Логістик-Про', 'Енерго-Збут', 'Комп-Сервіс', 'Вода-Град',
    'Фарм-Опт', 'Офіс-Комплект',
]

ITEM_NAMES = [
    'Папір А4, 80 г/м²', 'Послуги з прибирання', 'Друк візитівок',
    'Технічне обслуговування',
    'Картридж лазерний', 'Оренда офісу', 'Консультаційні послуги', 'Доставка товару',
    'Канцелярське приладдя', 'Ремонт принтера', 'Монтаж мережі', 'Навчання персоналу',
]

CITIES = ['Київ', 'Львів', 'Одеса', 'Дніпро', 'Харків', 'Вінниця', 'Житомир']

MONTHS_UK = ['січня', 'лютого', 'березня', 'квітня', 'травня', 'червня', 'липня',
             'серпня', 'вересня', 'жовтня', 'листопада', 'грудня']

CSS_VARIANTS = [
    """@page { size: A4; margin: 20mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 11px; color: #1f1f1f; }
h1 { font-size: 16px; margin: 0 0 4px; }
.sub { color: #777; font-size: 10px; margin-bottom: 12px; }
table.meta td { padding: 2px 4px; vertical-align: top; }
table.meta td.k { color: #666; width: 110px; }
table.items { width: 100%; border-collapse: collapse; margin-top: 12px; border: 1px solid #bbb; }
table.items th { background: #eee; border: 1px solid #bbb; padding: 5px; font-size: 9.5px; text-align: left; }
table.items td { border: 1px solid #ccc; padding: 4px 6px; }
.n { text-align: right; }
.total { margin-top: 12px; font-size: 13px; font-weight: 700; text-align: right; }
.vat { margin-top: 2px; text-align: right; color: #444; }
""",
    """@page { size: A4; margin: 25mm 18mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Serif', serif; font-size: 11px; color: #111; }
h1 { font-size: 15px; margin: 0 0 2px; text-align: center; }
.sub { color: #555; font-size: 9.5px; margin-bottom: 14px; text-align: center; }
table.meta td { padding: 2px 4px; vertical-align: top; }
table.meta td.k { color: #555; width: 120px; }
table.items { width: 100%; border-collapse: collapse; margin-top: 14px; }
table.items th { border-bottom: 1px solid #333; padding: 4px; font-size: 9.5px; text-align: left; }
table.items td { border-bottom: 1px solid #ddd; padding: 5px 4px; }
.n { text-align: right; }
.total { margin-top: 14px; font-size: 12.5px; font-weight: 700; text-align: right; }
.vat { margin-top: 2px; text-align: right; color: #333; }
""",
    """@page { size: A4; margin: 15mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Sans', sans-serif; font-size: 9.5px; color: #202020; }
h1 { font-size: 14px; margin: 0 0 3px; }
.sub { color: #666; font-size: 9px; margin-bottom: 8px; }
table.meta td { padding: 1px 3px; vertical-align: top; }
table.meta td.k { color: #666; width: 95px; }
table.items { width: 100%; border-collapse: collapse; margin-top: 8px; }
table.items th { background: #f2f2f2; padding: 3px 5px; font-size: 8.5px; text-align: left; }
table.items td { padding: 3px 5px; border-bottom: 1px solid #e4e4e4; }
.n { text-align: right; }
.total { margin-top: 8px; font-size: 11px; font-weight: 700; text-align: right; }
.vat { margin-top: 1px; text-align: right; color: #444; }
""",
]

TOTAL_WORDING = ['Всього до сплати', 'Усього до сплати', 'До сплати', 'Разом', 'Всього']
VAT_WORDING = ['в т.ч. ПДВ 20%', 'ПДВ 20%', 'в тому числі ПДВ 20%']


@dataclass
class Doc:
    kind: str
    number: str
    issued: date
    number_text: str
    date_text: str
    title_text: str
    supplier: str
    tax_label: str
    tax_id: str
    address: str
    phone: str
    email: str
    bank: str
    iban: str
    items: list[tuple[str, int, float]] = field(default_factory=list)
    vat_rate: int = 20
    template: int = 0
    language: str = 'uk'
    scan: bool = False
    filename: str = ''

    @property
    def total(self) -> float:
        return round(sum(quantity * price for _, quantity, price in self.items), 2)

    @property
    def vat(self) -> float:
        if self.vat_rate == 0:
            return 0.0
        return round(self.total * self.vat_rate / (100 + self.vat_rate), 2)

    def expected(self) -> dict:
        return {
            'kind': self.kind,
            'number': self.number,
            'date': self.issued.isoformat(),
            'supplier': self.supplier,
            'tax_id': self.tax_id,
            'total': self.total,
            'vat': self.vat if self.vat_rate else None,
            'currency': 'UAH',
        }


def fmt(value: float) -> str:
    return f'{value:,.2f}'.replace(',', ' ')


def _number(rng: random.Random) -> tuple[str, str]:
    """The value the parser should return, and the way it is printed."""
    shape = rng.choice(('year-seq', 'plain', 'prefixed', 'slash'))
    if shape == 'year-seq':
        value = f'{rng.choice((2025, 2026))}-{rng.randint(100, 999)}'
    elif shape == 'plain':
        value = str(rng.randint(10, 999))
    elif shape == 'prefixed':
        value = f'{rng.choice(("КП", "АК", "ВН"))}-{rng.randint(10, 99)}'
    else:
        value = f'{rng.randint(10, 99)}/{rng.randint(1, 9)}'
    return value, value


def _date(rng: random.Random) -> tuple[date, str]:
    issued = date(2026, rng.randint(1, 11), rng.randint(1, 28))
    shape = rng.choice(('dots-full', 'dots-full', 'dots-short', 'words'))
    if shape == 'dots-full':
        return issued, issued.strftime('%d.%m.%Y')
    if shape == 'dots-short':
        return issued, issued.strftime('%d.%m.%y')
    return issued, f'{issued.day} {MONTHS_UK[issued.month - 1]} {issued.year}'


def _supplier(rng: random.Random) -> tuple[str, str, str]:
    """Name, the tax-id label used, and the id itself."""
    legal = rng.choice(('ТОВ', 'ТОВ', 'ФОП', 'ПП'))
    if legal == 'ФОП':
        first, patronymic = rng.choice(PEOPLE)
        name = f'ФОП {rng.choice(SURNAMES)} {first} {patronymic}'
        tax_label = rng.choice(('ІПН', 'РНОКПП'))
        tax_id = str(rng.randint(2000000000, 3999999999))
    else:
        company = rng.choice(COMPANIES)
        quotes = rng.choice(('«»', '""'))
        name = f'{legal} {quotes[0]}{company}{quotes[1]}'
        tax_label = 'ЄДРПОУ'
        tax_id = str(rng.randint(30000000, 49999999))
    return name, tax_label, tax_id


def _items(rng: random.Random) -> list[tuple[str, int, float]]:
    chosen = rng.sample(ITEM_NAMES, rng.randint(1, 4))
    return [(name, rng.randint(1, 9), round(rng.uniform(80, 4500), 2)) for name in chosen]


def _title(kind: str, language: str, number: str, date_text: str, rng: random.Random) -> str:
    if kind == 'invoice':
        if language == 'ru':
            wording = rng.choice(('Счет на оплату', 'Счет'))
            return f'{wording} № {number} от {date_text}'
        wording = rng.choice(('Рахунок на оплату', 'Рахунок', 'Рахунок-фактура'))
        return f'{wording} № {number} від {date_text}'
    if kind == 'act':
        wording = rng.choice(('Акт виконаних робіт', 'Акт наданих послуг',
                              'Акт здачі-приймання робіт'))
        return f'{wording} № {number} від {date_text}'
    return f'Видаткова накладна № {number} від {date_text}'


def _meta_table(doc: Doc, inline_tax: bool) -> str:
    supplier_line = doc.supplier
    if inline_tax:
        supplier_line = f'{doc.supplier}, {doc.tax_label} {doc.tax_id}'
    tax_row = '' if inline_tax else f'<tr><td class="k">{doc.tax_label}</td><td>{doc.tax_id}</td></tr>'
    return f"""
<table class="meta">
<tr><td class="k">Постачальник</td><td>{supplier_line}</td></tr>
{tax_row}
<tr><td class="k">Адреса</td><td>{doc.address}</td></tr>
<tr><td class="k">Банк</td><td>{doc.bank}</td></tr>
<tr><td class="k">IBAN</td><td>{doc.iban}</td></tr>
<tr><td class="k">Покупець</td><td>{RECEIVER}</td></tr>
</table>
"""


def _items_table(doc: Doc) -> str:
    rows = ''.join(
        f'<tr><td>{index}</td><td>{name}</td><td class="n">{quantity}</td>'
        f'<td class="n">{fmt(price)}</td><td class="n">{fmt(quantity * price)}</td></tr>'
        for index, (name, quantity, price) in enumerate(doc.items, start=1)
    )
    return f"""
<table class="items">
<tr><th>№</th><th>Найменування</th><th>Кількість</th><th>Ціна, грн</th><th>Сума, грн</th></tr>
{rows}
</table>
"""


def _totals(doc: Doc, rng: random.Random) -> str:
    wording = rng.choice(TOTAL_WORDING)
    total = f'{wording}: {fmt(doc.total)} грн'
    if doc.vat_rate == 0:
        return f'<div class="total">{total}</div><div class="vat">Без ПДВ</div>'
    vat = rng.choice(VAT_WORDING)
    return f'<div class="total">{total}</div><div class="vat">{vat} — {fmt(doc.vat)} грн</div>'


def render(doc: Doc, rng: random.Random) -> str:
    inline_tax = rng.random() < 0.35
    css = CSS_VARIANTS[doc.template]
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<h1>{doc.title_text}</h1>
<div class="sub">{doc.supplier} · {doc.address.split(',')[-1].strip()}</div>
{_meta_table(doc, inline_tax)}
{_items_table(doc)}
{_totals(doc, rng)}
</body></html>"""


def to_scan(pdf: Path, rng: random.Random) -> Path:
    """Turn a text PDF into what a scan looks like: an image-only PDF."""
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / 'page')
        subprocess.run(
            ['pdftoppm', '-r', '150', '-png', str(pdf), prefix],
            check=True, capture_output=True, timeout=120,
        )
        pages = sorted(Path(tmp).glob('page*.png'))
        images = []
        for page in pages:
            image = Image.open(page).convert('RGB')
            # A scan is never perfectly straight and never perfectly clean.
            image = image.rotate(rng.uniform(-1.4, 1.4), fillcolor=(250, 250, 245),
                                 resample=Image.BICUBIC)
            image = image.filter(ImageFilter.GaussianBlur(0.4))
            images.append(image)
        scanned = pdf.with_name(pdf.stem + '-scan.pdf')
        images[0].save(scanned, save_all=True, append_images=images[1:], resolution=150)
        return scanned


def build(count: int, out: Path, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    kinds = ['invoice'] * max(1, count // 2) + ['act'] * max(1, count // 4)
    kinds += ['delivery'] * max(1, count - len(kinds))
    rng.shuffle(kinds)
    manifest: list[dict] = []
    for index, kind in enumerate(kinds[:count], start=1):
        supplier, tax_label, tax_id = _supplier(rng)
        number, number_text = _number(rng)
        issued, date_text = _date(rng)
        language = 'ru' if kind == 'invoice' and rng.random() < 0.12 else 'uk'
        doc = Doc(
            kind=kind,
            number=number,
            issued=issued,
            number_text=number_text,
            date_text=date_text,
            title_text=_title(kind, language, number_text, date_text, rng),
            supplier=supplier,
            tax_label=tax_label,
            tax_id=tax_id,
            address=f'{rng.choice(CITIES)}, вул. {rng.choice(("Шевченка", "Хрещатик", "Соборна", "Гагаріна"))}, '
                    f'буд. {rng.randint(1, 120)}',
            phone=f'+380 {rng.randint(40, 99)} {rng.randint(100, 999)} {rng.randint(10, 99)} {rng.randint(10, 99)}',
            email=f'office{rng.randint(1, 99)}@{rng.choice(("example", "postach", "firma"))}.ua',
            bank=f'АТ «{rng.choice(("ПриватБанк", "Ощадбанк", "Укргазбанк", "Райффайзен"))}»',
            iban='UA' + ''.join(str(rng.randint(0, 9)) for _ in range(27)),
            items=_items(rng),
            vat_rate=rng.choice((0, 20, 20, 20)),
            template=rng.randrange(len(CSS_VARIANTS)),
            language=language,
            scan=(index % 4 == 0),
        )
        html = render(doc, rng)
        name = f'{kind}-{index:02d}'
        text_pdf = out / f'{name}.pdf'
        HTML(string=html).write_pdf(str(text_pdf))
        pdf, twin = text_pdf, None
        if doc.scan:
            # The text twin stays on disk on purpose: a scan is the same document
            # through a worse channel, and keeping the clean copy lets a check ask
            # whether the two parses agree instead of only whether one is non-empty.
            pdf = to_scan(text_pdf, rng)
            twin = text_pdf.name
        doc.filename = pdf.name
        entry = {
            'file': pdf.name, 'seed': seed, 'template': doc.template,
            'language': doc.language, 'scan': doc.scan, 'twin': twin,
            'printed': {'title': doc.title_text, 'date': doc.date_text,
                        'number': doc.number_text},
            'expected': doc.expected(),
        }
        manifest.append(entry)
        print(f'{pdf.name}: {doc.title_text}')
    (out / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=45)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--seed', type=int, default=SEED)
    args = parser.parse_args()
    manifest = build(args.count, args.out, args.seed)
    print(f'{len(manifest)} documents in {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
