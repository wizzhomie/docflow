"""Generate a text-native synthetic corpus for the meaning-layer experiment.

Why text and not PDFs: the meaning layer receives `extract_text()` output, not a
file. Rendering PDFs on this Windows machine would require WeasyPrint/GTK and
`pdftotext`; neither is installed. A text corpus with `pdftotext -layout`-style
spacing exercises exactly the model input, is deterministic, and keeps the
pointer labels exact (line number + printed substring).

The manifest contains both the expected values and the ground-truth pointers, so
the same corpus feeds `tools/meaning_plan_experiment.py` (baseline) and the
fine-tuning set (pointer labels).

Run from the repository root:
  python finetune/corpus/make_text_corpus.py --count 300 --out data/synthetic-corpus
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import meaning  # noqa: E402

SEED = 20261121
FIELDS = ('number', 'date', 'supplier', 'tax_id', 'total', 'vat', 'currency')

COMPANIES = [
    'ТОВ «Ромашка»', 'ТОВ «Вектор»', 'ТОВ «Чистий дім»', 'ТОВ «ТехноСофт»',
    'ТОВ «Північний шлях»', 'ТОВ «Затишок-Сервіс»', 'ТОВ «Логіка»',
    'ТОВ «Агро-Сервіс»', 'ТОВ «Київ-Папір»', 'ТОВ «Буд-Майстер»',
    'ТОВ «Мед-Техніка»', 'ТОВ «Логістик-Про»', 'ТОВ «Офіс-Комплект»',
    'ТОВ «Смак-Дистрибуція»', 'ТОВ «Енерго-Збут»', 'ТОВ «Вода-Град»',
]
PEOPLE = [
    ('Коваленко', 'Олена', 'Іванівна'), ('Гриценко', 'Ірина', 'Петрівна'),
    ('Мельник', 'Олег', 'Іванович'), ('Шевченко', 'Наталія', 'Сергіївна'),
    ('Бондаренко', 'Андрій', 'Миколайович'), ('Ткачук', 'Марія', 'Петрівна'),
    ('Мороз', 'Сергій', 'Олегович'), ('Лисенко', 'Оксана', 'Андріївна'),
    ('Савченко', 'Дмитро', 'Вікторович'), ('Руденко', 'Тетяна', 'Юріївна'),
]
ITEMS = [
    ('Розробка корпоративного сайту', 1, 50000.0),
    ('Технічна підтримка, міс.', 3, 10000.0),
    ('Аудит складської логістики', 1, 120000.0),
    ('Консультації щодо WMS', 8, 7500.0),
    ('Навчання персоналу', 2, 15000.0),
    ('Прибирання офісних приміщень', 6, 25000.0),
    ('Ліцензія DocFlow Pro, рік', 25, 4800.0),
    ('Впровадження та налаштування', 1, 45000.0),
    ('Папір А4, 80 г/м²', 20, 320.0),
    ('Картридж лазерний', 4, 2100.0),
    ('Оренда офісу, міс.', 1, 30000.0),
    ('Доставка товару', 1, 1500.0),
]
CITIES = ['Київ', 'Львів', 'Одеса', 'Дніпро', 'Харків', 'Вінниця', 'Житомир']
STREETS = ['Хрещатик', 'Садова', 'Миру', 'Велика Васильківська', 'Соборна', 'Шевченка']
MONTHS_UK = [
    'січня', 'лютого', 'березня', 'квітня', 'травня', 'червня',
    'липня', 'серпня', 'вересня', 'жовтня', 'листопада', 'грудня',
]
MONTHS_RU = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]


def money(value: float) -> str:
    return f'{value:,.2f}'.replace(',', ' ').replace('.', ',')


def pick_supplier(rng: random.Random) -> tuple[str, str, str]:
    """Return (name, tax_id, address)."""
    if rng.random() < 0.35:
        surname, first, patronymic = rng.choice(PEOPLE)
        name = f'ФОП {surname} {first} {patronymic}'
        tax_id = f'{rng.randint(1000000000, 9999999999)}'
    else:
        name = rng.choice(COMPANIES)
        tax_id = f'{rng.randint(10000000, 99999999)}'
    address = f'м. {rng.choice(CITIES)}, вул. {rng.choice(STREETS)}, {rng.randint(1, 120)}'
    return name, tax_id, address


def make_items(rng: random.Random) -> list[tuple[str, float, float, float]]:
    chosen = rng.sample(ITEMS, k=rng.randint(1, 4))
    rows = []
    for name, qty, price in chosen:
        rows.append((name, float(qty), price, round(qty * price, 2)))
    return rows


def header_block(
    rng: random.Random, *, layout: int, russian: bool, word_date: bool,
) -> tuple[list[str], dict, dict]:
    """Title plus parties. Layout 0=supplier first, 1=buyer first, 2=two columns, 3=Russian."""
    number = rng.choice([
        f'2026-{rng.randint(100, 999)}', str(rng.randint(10, 999)),
        f'КП-{rng.randint(10, 99)}', f'{rng.randint(100, 999)}/{rng.randint(1, 12)}',
    ])
    day = date(2026, rng.randint(1, 11), rng.randint(1, 28))
    if word_date:
        months = MONTHS_RU if russian else MONTHS_UK
        printed_date = f'{day.day} {months[day.month - 1]} {day.year}'
    else:
        printed_date = day.strftime('%d.%m.%Y')
    supplier, tax_id, address = pick_supplier(rng)
    buyer = rng.choice([c for c in COMPANIES if c != supplier])
    buyer_tax = f'{rng.randint(10000000, 99999999)}'

    lines: list[str] = []
    pointers: dict = {}

    def add(line: str) -> int:
        lines.append(line)
        return len(lines)

    if russian:
        add(f'Счёт на оплату № {number} от {printed_date}')
        supplier_label, buyer_label, tax_label, address_label = (
            'Поставщик', 'Покупатель', 'ИНН', 'Адрес')
    else:
        add(f'Рахунок на оплату № {number} від {printed_date}')
        supplier_label, buyer_label, tax_label, address_label = (
            'Постачальник', 'Покупець', 'ЄДРПОУ', 'Адреса')
    pointers['number'] = {'line': 1, 'text': number}
    pointers['date'] = {'line': 1, 'text': printed_date}

    supplier_tax_label = tax_label if len(tax_id) == 8 else ('ІПН' if not russian else 'ИНН')
    buyer_tax_label = tax_label if len(buyer_tax) == 8 else 'ІПН'

    def add_supplier() -> None:
        pointers['supplier'] = {'line': add(f'{supplier_label}: {supplier}'), 'text': supplier}
        pointers['tax_id'] = {'line': add(f'{supplier_tax_label} {tax_id}'), 'text': tax_id}
        add(f'{address_label}: {address}')

    def add_buyer() -> None:
        add(f'{buyer_label}: {buyer}')
        add(f'{buyer_tax_label} {buyer_tax}')

    if layout == 2:
        pointers['supplier'] = {
            'line': add(f'{supplier_label}: {supplier}          {buyer_label}: {buyer}'),
            'text': supplier,
        }
        pointers['tax_id'] = {
            'line': add(f'{supplier_tax_label} {tax_id}                     {buyer_tax_label} {buyer_tax}'),
            'text': tax_id,
        }
        add(f'{address_label}: {address}')
    elif layout == 1:
        add_buyer()
        add_supplier()
    else:
        add_supplier()
        add_buyer()

    expected = {
        'number': number,
        'date': day.isoformat(),
        'supplier': supplier,
        'tax_id': tax_id,
    }
    return lines, pointers, expected


def items_block(rng: random.Random, rows, russian: bool) -> list[str]:
    header = ('№   Наименование                    Кол-во    Цена        Сумма'
              if russian else
              '№   Найменування                    Кількість  Ціна        Сума')
    lines = ['', header]
    for index, (name, qty, price, amount) in enumerate(rows, 1):
        lines.append(f'{index:<3} {name:<33} {qty:>7g}   {money(price):>11}   {money(amount):>11}')
    return lines


def totals_block(
    rng: random.Random, *, subtotal: float, vat: float, vat_rate: int, russian: bool,
    style: str = 'net_first',
) -> tuple[list[str], dict, dict]:
    """Totals block. When VAT is 20%, the subtotal line shows the net amount, which
    is deliberately different from the total: a model that points at it is wrong.

    Two blocks the market really prints. `net_first` is «Разом без ПДВ / ПДВ 20% /
    Всього до сплати: X грн». `ink` is the table 1С and M.E.Doc print: «Всього: X /
    Сума ПДВ: Y / Всього із ПДВ: Z», where the amount due is the third line and the
    currency sits after it on the next one — five of the twelve real invoices used
    to point at the net «Всього» line, so the class is in the corpus now.
    """
    currency = 'грн'
    net = round(subtotal - vat, 2)
    if style == 'ink':
        if russian:
            net_label, vat_label = 'Всего', 'Сумма НДС'
            total_label = 'Всего с НДС' if vat_rate else 'Всего без НДС'
            words_label, preposition = 'Всего наименований', 'на сумму'
        else:
            net_label, vat_label = 'Всього', 'Сума ПДВ'
            total_label = 'Всього із ПДВ' if vat_rate else 'Всього без ПДВ'
            words_label, preposition = 'Всього найменувань', 'на суму'
        lines = ['',
                 f'{net_label}: {money(net)}',
                 f'{vat_label}: {money(vat)}',
                 f'{total_label}: {money(subtotal)}',
                 f'{words_label} {rng.randint(1, 4)}, {preposition} {money(subtotal)} {currency}.']
        pointers = {
            'total': {'line': 4, 'text': money(subtotal)},
            'vat': {'line': 3, 'text': money(vat)},
            'currency': {'line': 5, 'text': f'{currency}.'},
        }
        expected = {'total': round(subtotal, 2), 'vat': round(vat, 2), 'currency': 'UAH'}
        return lines, pointers, expected
    if russian:
        total_label, vat_label = 'Всего к оплате', (f'НДС {vat_rate}%' if vat_rate else 'Без НДС')
        net_label = 'Итого без НДС'
    else:
        total_label, vat_label = 'Всього до сплати', (f'ПДВ {vat_rate}%' if vat_rate else 'Без ПДВ')
        net_label = 'Разом без ПДВ'
    if vat_rate:
        subtotal_line = f'{net_label}: {money(net)}'
    else:
        subtotal_line = f'Разом: {money(subtotal)}'
    total_line = f'{total_label}: {money(subtotal)} {currency}'
    if vat_rate and rng.random() < 0.25:
        total_line += f' (ПДВ {money(vat)})'
    lines = ['', subtotal_line, f'{vat_label}: {money(vat)}', total_line]
    pointers = {
        'total': {'line': 4, 'text': money(subtotal)},
        'vat': {'line': 3, 'text': money(vat)},
        'currency': {'line': 4, 'text': currency},
    }
    expected = {'total': round(subtotal, 2), 'vat': round(vat, 2), 'currency': 'UAH'}
    return lines, pointers, expected


def meta_block(rng: random.Random, russian: bool) -> list[str]:
    """Distractor lines: a contract number and a payment deadline date."""
    lines = []
    if rng.random() < 0.3:
        label = 'Договір' if not russian else 'Договор'
        lines.append(f'{label} № {rng.randint(10, 999)}/{rng.randint(2020, 2026)}')
    if rng.random() < 0.3:
        deadline = date(2026, rng.randint(1, 12), rng.randint(1, 28)).strftime('%d.%m.%Y')
        label = 'Термін оплати' if not russian else 'Срок оплаты'
        lines.append(f'{label}: {deadline}')
    return lines


def build_document(rng: random.Random, index: int,
                   layouts: list[int] | None = None) -> tuple[str, dict, dict, dict]:
    layout = rng.choice(layouts or [0, 1, 2, 3])
    russian = layout == 3 or rng.random() < 0.1
    word_date = rng.random() < 0.25
    vat_rate = 20 if rng.random() < 0.8 else 0
    totals_style = 'ink' if rng.random() < 0.4 else 'net_first'
    rows = make_items(rng)
    subtotal = round(sum(row[3] for row in rows), 2)
    vat = round(subtotal * vat_rate / (100 + vat_rate), 2) if vat_rate else 0.0

    head, head_pointers, head_expected = header_block(
        rng, layout=layout, russian=russian, word_date=word_date)
    body = items_block(rng, rows, russian)
    tail, tail_pointers, tail_expected = totals_block(
        rng, subtotal=subtotal, vat=vat, vat_rate=vat_rate, russian=russian,
        style=totals_style)
    meta = meta_block(rng, russian)

    blocks = [('head', head, head_pointers, head_expected),
              ('meta', meta, {}, {})]
    if rng.random() < 0.3:
        blocks += [('totals', tail, tail_pointers, tail_expected),
                   ('items', body, {}, {})]
    else:
        blocks += [('items', body, {}, {}),
                   ('totals', tail, tail_pointers, tail_expected)]

    lines: list[str] = []
    pointers: dict = {}
    expected: dict = {}
    for _, block_lines, block_pointers, block_expected in blocks:
        offset = len(lines)
        lines.extend(block_lines)
        for field, pointer in block_pointers.items():
            pointers[field] = {'line': pointer['line'] + offset, 'text': pointer['text']}
        expected.update(block_expected)

    text = '\n'.join(lines) + '\n'
    resolved = meaning.resolve({'fields': pointers}, text)
    for name in FIELDS:
        want = expected.get(name)
        got = resolved.get(name)
        if got is None:
            raise AssertionError(
                f'doc {index}: {name} did not resolve; pointer={pointers.get(name)}\n{text}')
        if name in ('total', 'vat'):
            if abs(float(got.value) - float(want)) > 0.005:
                raise AssertionError(f'doc {index}: {name} {got.value} != {want}\n{text}')
        elif str(got.value).casefold() != str(want).casefold():
            raise AssertionError(f'doc {index}: {name} {got.value!r} != {want!r}\n{text}')
    meta_info = {'layout': layout, 'russian': russian, 'word_date': word_date,
                 'vat_rate': vat_rate, 'totals': totals_style, 'index': index}
    return text, expected, pointers, meta_info


def add_noise(text: str, rng: random.Random) -> str:
    """OCR-like label damage that does not touch the printed field values."""
    replacements = [('ЄДРПОУ', 'ЕДРПОУ'), ('ІПН', 'INH'), ('№', 'Ne')]
    lines = []
    for line in text.splitlines():
        for old, new in replacements:
            if old in line and rng.random() < 0.5:
                line = line.replace(old, new, 1)
        lines.append(line)
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=45)
    parser.add_argument('--layouts', default='',
                        help='comma-separated layout ids, e.g. 1,2; empty means all')
    parser.add_argument('--out', type=Path,
                        default=REPO / 'data' / 'synthetic-corpus')
    args = parser.parse_args()
    layouts = [int(part) for part in args.layouts.split(',') if part.strip()] or None
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    manifest = []
    for index in range(1, args.count + 1):
        text, expected, pointers, meta = build_document(rng, index, layouts)
        if index % 4 == 0:
            text = add_noise(text, rng)
        name = f'doc_{index:04d}.txt'
        (args.out / name).write_text(text, encoding='utf-8')
        manifest.append({'file': name, 'expected': expected, 'pointers': pointers, 'meta': meta})
    (args.out / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'generated {len(manifest)} documents in {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
