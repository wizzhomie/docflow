"""Support knowledge: curated FAQ answers and product facts.

Two different jobs:

* ``faq_answer`` handles the common support questions instantly and deterministically —
  the answer is written by a human, so it cannot be hallucinated and costs no tokens;
* ``SUPPORT_FACTS`` is the context for the rare question that neither the FAQ nor the tools
  cover, when the model has to phrase an explanation itself.
"""

from __future__ import annotations

import re

LANGUAGE_RE = re.compile(r'[а-яіїєґ]', re.IGNORECASE)


def _ru(text: str) -> bool:
    return len(LANGUAGE_RE.findall(text)) >= 3


SUPPORT_FACTS = """\
FACTS about DocFlow:
- DocFlow turns documents (PDF, scan, photo) into structured records. Sources: upload, watched folder, IMAP mailbox.
- Extraction is deterministic rules, not a language model: invoice, act or delivery note; fields with confidence and issues; OCR for scans.
- Routing: invoice goes to Payments, act and delivery note to Accounting, total at or above the approval threshold (100 000 by default) to Approval.
- Every document is confirmed by a person; confirmed rows go to history and the CRM. A duplicate is the same supplier with the same number, or the same date and amount.
- Typical issues: amounts do not add up, a tax id or IBAN is missing, number or date unreadable, low OCR quality. Fix: open the document, correct the field, confirm.
- Document types and the approval threshold are configuration (profiles), not code.
- This assistant reads DocFlow through the API. It cannot confirm, pay, send mail or edit documents: it answers, checks history, writes draft files and proposes actions for a human.
- If the facts do not cover the question, say honestly that you do not know and suggest asking the DocFlow team.
"""

# keyword -> (ru answer, en answer). First match wins, so keep the lists specific.
FAQ: list[dict] = [
    {
        'id': 'confirm',
        'keywords': ['подтверд', 'confirm', 'как провести', 'approve document'],
        'ru': ('Чтобы подтвердить документ: откройте его в DocFlow, проверьте поля, которые '
               'распознала система (номер, дата, поставщик, суммы), при необходимости '
               'поправьте и нажмите «Подтвердить». Проводит документ всегда человек — '
               'автоматического подтверждения нет.'),
        'en': ('To confirm a document, open it in DocFlow, check the extracted fields '
               '(number, date, supplier, amounts), correct anything that is wrong and press '
               'Confirm. A person always confirms; nothing is confirmed automatically.'),
    },
    {
        'id': 'approval',
        'keywords': ['согласован', 'approval', 'порог', 'threshold', 'почему на соглас'],
        'ru': ('На согласование документ уходит, когда его сумма не ниже порога — по '
               'умолчанию 100 000. Порог — настройка сервера, его можно изменить. Внутри '
               'согласования документ ждёт решения человека: оплата и подтверждение '
               'остаются за сотрудником.'),
        'en': ('A document goes to Approval when its total is at or above the approval '
               'threshold, 100 000 by default. The threshold is a server setting. Approval '
               'is still a human decision: payment and confirmation stay with a person.'),
    },
    {
        'id': 'duplicate',
        'keywords': ['дубл', 'duplicate', 'повтор'],
        'ru': ('Дубль — это тот же поставщик с тем же номером документа, либо та же дата и '
               'та же сумма. DocFlow хранит подтверждённую историю и сравнивает с ней новый '
               'документ. Если дубль найден, документ лучше не проводить повторно: проверьте '
               'его и отклоните или объедините с уже проведённым.'),
        'en': ('A duplicate is the same supplier with the same document number, or the same '
               'date and the same amount. DocFlow keeps confirmed history and compares a new '
               'document against it. If one is found, do not book it twice.'),
    },
    {
        'id': 'issues',
        'keywords': ['ошибк', 'не сход', 'не совпада', 'проблем', 'issue', 'wrong amount',
                     'missing', 'не хватает', 'не читается', 'add up'],
        'ru': ('Типичные замечания: суммы не сходятся (строки против итога), не хватает '
               'ИНН или IBAN, не распознались номер или дата, низкое качество скана. '
               'Лечится одинаково: откройте документ, поправьте проблемное поле вручную и '
               'подтвердите. Если скан совсем плохой — загрузите файл получше.'),
        'en': ('Typical issues: the amounts do not add up (items versus total), a tax id or '
               'IBAN is missing, the number or date could not be read, or the scan is poor. '
               'Fix: open the document, correct the field by hand and confirm. For a bad '
               'scan, upload a better file.'),
    },
    {
        'id': 'routing',
        'keywords': ['куда уход', 'куда попада', 'маршрут', 'route', 'routing', 'where does'],
        'ru': ('Маршрут определяют правила, а не модель: счёт уходит в оплату, акт и '
               'видаткова накладна — в учёт, а документ с суммой не ниже порога — на '
               'согласование. Типы документов и правила маршрутизации — это настройки '
               '(профили), поэтому новый тип добавляется без переписывания кода.'),
        'en': ('Routing is done by rules, not by a model: an invoice goes to Payments, an act '
               'or delivery note to Accounting, and anything at or above the approval '
               'threshold to Approval. Document types and routing are configuration '
               '(profiles), so a new type does not need new code.'),
    },
    {
        'id': 'capabilities',
        'keywords': ['что ты умеешь', 'что умеешь', 'who are you', 'что ты можешь',
                     'what can you do', 'help me', 'помоги'],
        'ru': ('Я читаю DocFlow и отвечаю по очереди и истории: показываю, что ждёт '
               'проверки, объясняю маршрут и замечания, проверяю дубли, готовлю черновики '
               'писем и предложения подтвердить документ. Подтверждать, оплачивать и '
               'отправлять письма я не могу — это делает человек.'),
        'en': ('I read DocFlow and answer about the queue and history: what is waiting for '
               'review, why a document was routed somewhere, whether it is a duplicate, and '
               'I prepare draft letters and confirmation proposals. I cannot confirm, pay or '
               'send mail — a person does that.'),
    },
    {
        'id': 'what-is-docflow',
        'keywords': ['что такое docflow', 'what is docflow', 'как работает docflow',
                     'how does docflow work', 'о платформе', 'about the platform'],
        'ru': ('DocFlow превращает документы — PDF, сканы, фото — в структурированные '
               'записи: определяет тип (счёт, акт, накладная), вытаскивает номер, дату, '
               'поставщика и суммы, находит замечания и предлагает маршрут. Разбор '
               'детерминированный: правила и профили, без языковой модели. Дальше документ '
               'проверяет человек и подтверждает, а данные уходят в CRM.'),
        'en': ('DocFlow turns documents — PDFs, scans, photos — into structured records: it '
               'detects the type (invoice, act, delivery note), extracts number, date, '
               'supplier and amounts, flags issues and proposes a route. Extraction is '
               'deterministic: rules and profiles, no language model. A person then reviews '
               'and confirms, and the data goes to the CRM.'),
    },
    {
        'id': 'odm',
        'keywords': ['ии', 'нейросет', 'ai ', 'machine learning', 'llm', 'модель использ'],
        'ru': ('Разбор документов идёт без языковой модели — это правила, профили и '
               'проверки. Языковая модель используется только мной, ассистентом: чтобы '
               'понять вопрос и выбрать инструмент. Модель работает локально, документы '
               'никуда не отправляются.'),
        'en': ('Document extraction uses no language model: it is rules, profiles and '
               'checks. The language model is only in this assistant, to understand the '
               'question and pick a tool. It runs locally; documents are not sent anywhere.'),
    },
]


def faq_match(task: str) -> tuple[str, str] | None:
    """Return (topic, curated answer) for a common platform question, else None."""
    text = task.lower()
    for entry in FAQ:
        if any(keyword in text for keyword in entry['keywords']):
            return entry['id'], (entry['ru'] if _ru(task) else entry['en'])
    return None


def faq_answer(task: str) -> str | None:
    match = faq_match(task)
    return match[1] if match else None
