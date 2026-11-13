"""Render the as-is / to-be process maps as standalone SVG files.

Run: python3 tools/render_process_maps.py
Output: docs/process-as-is.svg, docs/process-to-be.svg

The files are plain SVG (no external fonts or scripts) so they can be embedded
in a case study, a deck or a README without extra tooling.
"""

from pathlib import Path
from xml.sax.saxutils import escape

DOCS = Path(__file__).resolve().parents[1] / 'docs'

INK = '#111827'
MUTED = '#6b7280'
LINE = '#e5e7eb'
BG = '#f7f8fb'
SYSTEM = '#2563eb'
HUMAN = '#f59e0b'
CRM = '#10b981'
RISK = '#dc2626'

FONT = "'Carlito','DejaVu Sans',system-ui,sans-serif"


def header(width, title, subtitle, legend):
    chips = []
    x = width - 40
    for label, color in reversed(legend):
        w = 14 + len(label) * 7.2
        x -= w
        chips.append(
            f'<rect x="{x:.1f}" y="40" width="10" height="10" rx="3" fill="{color}"/>'
            f'<text x="{x + 16:.1f}" y="49.5" font-size="12.5" fill="{MUTED}">{escape(label)}</text>'
        )
        x -= 22
    return (
        f'<text x="40" y="52" font-size="25" font-weight="700" fill="{INK}">{escape(title)}</text>'
        f'<text x="40" y="78" font-size="13.5" fill="{MUTED}">{escape(subtitle)}</text>'
        + ''.join(chips)
    )


def box(x, y, w, h, title, lines, color, dashed=False):
    stroke = color if dashed else LINE
    body = []
    body.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#ffffff" '
        f'stroke="{stroke}" stroke-width="{1.5 if dashed else 1}"/>'
    )
    body.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" fill="{color}"/>')
    body.append(
        f'<text x="{x + 20}" y="{y + 27}" font-size="15" font-weight="600" fill="{INK}">{escape(title)}</text>'
    )
    for index, line in enumerate(lines[:3]):
        body.append(
            f'<text x="{x + 20}" y="{y + 49 + index * 17}" font-size="12.5" fill="{MUTED}">{escape(line)}</text>'
        )
    return ''.join(body)


def arrow(points, color='#9ca3af', dashed=False, marker='arrow'):
    path = 'M ' + ' L '.join(f'{x} {y}' for x, y in points)
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    return (
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"{dash} '
        f'marker-end="url(#{marker})"/>'
    )


def defs():
    markers = []
    for name, color in (('arrow', '#9ca3af'), ('arrow-risk', RISK), ('arrow-crm', CRM), ('arrow-human', HUMAN)):
        markers.append(
            f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>'
        )
    return '<defs>' + ''.join(markers) + '</defs>'


def svg(width, height, content, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
        f'font-family="{FONT}">'
        f'<rect width="{width}" height="{height}" fill="{BG}"/>'
        + defs()
        + content
        + '</svg>'
    )


def render_as_is():
    width, height = 1560, 470
    parts = [header(width, 'Процесс до автоматизации', 'Ручной контур: счёт приходит в почту, попадает в CRM руками',
                    [('человек', HUMAN), ('риск', RISK)])]

    boxes = [
        (40, 'Получение счёта', ['email, мессенджер,', 'бумага, повторные'], HUMAN),
        (330, 'Ручной ввод в CRM', ['номер, дата, поставщик,', 'сумма, ЄДРПОУ'], HUMAN),
        (620, 'Проверка бухгалтером', ['сумма, НДС,', 'реквизиты'], HUMAN),
        (910, 'Согласование оплаты', ['руководитель,', 'переписка'], HUMAN),
        (1200, 'Оплата и проводка', ['1С / банк', 'отдельным шагом'], CRM),
    ]
    for x, title, lines, color in boxes:
        parts.append(box(x, 130, 260, 96, title, lines, color))
    for x in (300, 590, 880, 1170):
        parts.append(arrow([(x, 178), (x + 30, 178)]))

    pains = [
        (40, 'Ошибки в сумме и ЄДРПОУ', 'вводятся руками, проверяются глазами'),
        (330, 'Потерянные счета', 'нет единого реестра, дубли не видны'),
        (620, 'Дубли сделок', 'повторная отправка создаёт вторую'),
        (910, 'Нет истории', 'кто что менял и когда — неизвестно'),
    ]
    for x, title, line in pains:
        parts.append(f'<rect x="{x}" y="270" width="260" height="78" rx="12" fill="#fef2f2" stroke="#fecaca"/>')
        parts.append(f'<text x="{x + 18}" y="298" font-size="13" font-weight="600" fill="{RISK}">{escape(title)}</text>')
        parts.append(f'<text x="{x + 18}" y="319" font-size="12" fill="#b91c1c">{escape(line)}</text>')
        parts.append(arrow([(x + 130, 226), (x + 130, 268)], color=RISK, dashed=True, marker='arrow-risk'))

    parts.append(f'<text x="40" y="400" font-size="13" fill="{MUTED}">'
                 'Один документ = 3–10 минут ручной работы, качество зависит от внимания человека. '
                 'Данные для CRM вводятся вручную и не сверяются машиной.</text>')
    parts.append('<text x="40" y="424" font-size="12" fill="#9ca3af">'
                 'Схема показывает текущий процесс, а не дефекты конкретного исполнителя.</text>')
    (DOCS / 'process-as-is.svg').write_text(svg(width, height, ''.join(parts), 'Процесс до автоматизации'), encoding='utf-8')


def render_to_be():
    width, height = 1620, 560
    parts = [header(width, 'Процесс после автоматизации', 'DocFlow снимает интерпретацию, валидацию и передачу в CRM; человек решает только спорное',
                    [('система', SYSTEM), ('человек', HUMAN), ('CRM', CRM), ('исключение', RISK)])]

    steps = [
        (30, 'Приём документа', ['upload, API,', 'почта'], SYSTEM),
        (292, 'Извлечение текста', ['PDF, скан,', 'OCR ukr+eng'], SYSTEM),
        (554, 'Интерпретация', ['поля счёта и', 'позиции'], SYSTEM),
        (816, 'Валидация', ['инварианты,', 'риск-флаг'], SYSTEM),
        (1078, 'Ревью человеком', ['только спорное,', 'правки и confirm'], HUMAN),
        (1340, 'Сделка в CRM', ['сумма, компания,', 'комментарий'], CRM),
    ]
    for x, title, lines, color in steps:
        parts.append(box(x, 120, 240, 96, title, lines, color))
    for x in (270, 532, 794, 1056, 1318):
        parts.append(arrow([(x, 168), (x + 22, 168)]))

    exceptions = [
        (30, 'Текст не извлечён', ['скан низкого качества →', 'ручной ввод, не тишина'], 412),
        (560, 'Инвариант провален', ['сумма, НДС, ЄДРПОУ, items →', 'приоритетное ревью'], 936),
        (1090, 'Дубль по номеру + ЄДРПОУ', ['сделка не создаётся,', 'показывается найденная'], 1460),
    ]
    for x, title, lines, source_x in exceptions:
        parts.append(box(x, 300, 260, 92, title, lines, RISK, dashed=True))
        parts.append(arrow([(source_x, 216), (source_x, 254), (x + 130, 254), (x + 130, 298)],
                           color=RISK, dashed=True, marker='arrow-risk'))

    parts.append('<text x="40" y="446" font-size="12" font-weight="600" fill="'
                 + MUTED + '" letter-spacing="1.2">ИЗМЕРЕНИЯ</text>')
    metrics = ['auto-pass rate', 'median review time', 'corrections by field', 'duplicates blocked', 'CRM failures']
    x = 40
    for metric in metrics:
        w = 22 + len(metric) * 7.4
        parts.append(f'<rect x="{x:.1f}" y="462" width="{w:.1f}" height="30" rx="15" fill="#eef2ff" stroke="#c7d2fe"/>')
        parts.append(f'<text x="{x + 11:.1f}" y="481.5" font-size="12" fill="#3730a3">{escape(metric)}</text>')
        x += w + 10

    parts.append(f'<text x="40" y="528" font-size="13" fill="{MUTED}">'
                 'Правило: машина никогда не отправляет в CRM значение, в котором не уверена — '
                 'спорное уходит человеку, а не в тишину.</text>')
    (DOCS / 'process-to-be.svg').write_text(svg(width, height, ''.join(parts), 'Процесс после автоматизации'), encoding='utf-8')


if __name__ == '__main__':
    render_as_is()
    render_to_be()
    print('written:', DOCS / 'process-as-is.svg', DOCS / 'process-to-be.svg')
