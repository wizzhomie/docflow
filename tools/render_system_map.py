"""Render the one-page system map for the plain-language guide.

Run: python3 tools/render_system_map.py
Output: docs/system-map.svg

Plain SVG, no external fonts or scripts. The map answers one question: what
happens to a document between arriving and becoming a CRM deal, and who owns
each step (system, human, external CRM).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_process_maps import CRM, DOCS, HUMAN, INK, MUTED, RISK, SYSTEM, arrow, box, defs, header, svg

WIDTH = 1240


def render():
    parts = [defs()]

    # Row 1: intake -> recognition -> formal control -> queue
    parts.append(box(40, 120, 260, 92, 'Прийом', ['папка data/inbox', 'завантаження файлу', 'готовий сэмпл'], SYSTEM))
    parts.append(box(340, 120, 290, 92, 'Розпізнавання', ['текст або OCR для сканів', 'визначення типу документа', 'поля + оцінка впевненості'], SYSTEM))
    parts.append(box(670, 120, 240, 92, 'Формальна перевірка', ['суми, дати, ЄДРПОУ', 'email, телефон, IBAN', 'містить сигнали, не виправляє'], SYSTEM))
    parts.append(box(950, 120, 250, 92, 'Черга на перевірку', ['документ не зникає', 'видно кількість сигналів', 'відкривається одним кліком'], SYSTEM))

    parts.append(arrow([(300, 166), (338, 166)]))
    parts.append(arrow([(630, 166), (668, 166)]))
    parts.append(arrow([(910, 166), (948, 166)]))

    # Profiles feeding recognition
    parts.append(box(340, 30, 290, 56, 'Профіль типу документа', ['рахунок / акт: свої підказки та поля'], MUTED, dashed=True))
    parts.append(arrow([(485, 86), (485, 118)], dashed=True))

    # Row 2: human review -> CRM -> history
    parts.append(box(100, 300, 280, 100, 'Людина у ревʼю', ['перевіряє та править поля', 'натискає Confirm', 'бачить карточку результату'], HUMAN))
    parts.append(box(470, 300, 300, 100, 'Конектор CRM', ['Uspacy або Bitrix24', 'компанія + реквізити', 'контакт і сделка'], CRM))
    parts.append(box(860, 300, 250, 100, 'Історія та метрики', ['що підтверджено', 'скільки зекономлено', 'що правлять найчастіше'], SYSTEM))

    parts.append(arrow([(1075, 212), (1075, 260), (240, 260), (240, 298)]))
    parts.append(arrow([(380, 350), (468, 350)]))
    parts.append(arrow([(770, 350), (858, 350)]))

    # Duplicate protection across both stores
    parts.append(box(470, 440, 300, 76, 'Захист від дублів', ['локальна історія', 'перевірка у самій CRM'], RISK, dashed=True))
    parts.append(arrow([(620, 438), (620, 402)], dashed=True))

    parts.append(box(40, 440, 380, 76, 'Що робить система, а що людина', ['система читає й перевіряє формально', 'людина вирішує і несе відповідальність'], MUTED, dashed=True))

    parts.append(
        f'<text x="40" y="560" font-size="13" fill="{MUTED}">'
        'Схема відповідає на питання: що трапляється з документом від прийому до сделки в CRM.'
        '</text>'
        f'<text x="40" y="582" font-size="13" fill="{MUTED}">'
        'Сині блоки — крок системи, помаранчевий — крок людини, зелений — зовнішня CRM, червоний — запобіжник.'
        '</text>'
    )
    return svg(WIDTH, 620, ''.join(parts), 'DocFlow: шлях документа')


def main() -> int:
    target = DOCS / 'system-map.svg'
    target.write_text(render(), encoding='utf-8')
    print('written:', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
