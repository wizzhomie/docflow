"""Intent rules: unambiguous phrasings get the tool from rules, everything else the model."""
from __future__ import annotations

import pytest

from docflow_operator.intent import DOC_ID_RE, intent_plan

CASES = [
    # propose_confirm
    ('Propose confirming the newest document.', 'propose_confirm', {'doc_id': 'latest'}),
    ('Propose to confirm document 3291971f49.', 'propose_confirm', {'doc_id': '3291971f49'}),
    ('Предложи подтвердить последний документ.', 'propose_confirm', {'doc_id': 'latest'}),
    ('Предлагаю подтвердить 3291971f49.', 'propose_confirm', {'doc_id': '3291971f49'}),
    # draft_letter and its kind
    ('Draft a payment request letter for document 3291971f49.', 'draft_letter',
     {'doc_id': '3291971f49', 'kind': 'payment_request'}),
    ('Draft an approval letter for 3291971f49.', 'draft_letter',
     {'doc_id': '3291971f49', 'kind': 'approval_request'}),
    ('Draft a reply letter to the supplier.', 'draft_letter',
     {'doc_id': 'latest', 'kind': 'supplier_reply'}),
    ('Подготовь черновик письма об оплате.', 'draft_letter',
     {'doc_id': 'latest', 'kind': 'payment_request'}),
    # check_duplicate
    ('Check whether document 3291971f49 is a duplicate.', 'check_duplicate', {'doc_id': '3291971f49'}),
    ('Is the newest document a duplicate?', 'check_duplicate', {'doc_id': 'latest'}),
    ('Найди дубли для 3291971f49.', 'check_duplicate', {'doc_id': '3291971f49'}),
    # summarise and its period
    ('Give me a summary for today.', 'summarise', {'period': 'today'}),
    ('Weekly summary, please.', 'summarise', {'period': 'week'}),
    ('Сводка за неделю.', 'summarise', {'period': 'week'}),
    ('Итоги за сегодня.', 'summarise', {'period': 'today'}),
    ('Сколько документов ждёт проверки?', 'summarise', {'period': 'all'}),
    ('Summary of everything.', 'summarise', {'period': 'all'}),
    # list_queue
    ('Which documents need approval?', 'list_queue', {'limit': 10}),
    ('What needs review right now?', 'list_queue', {'limit': 10}),
    ('Какие документы нужно на согласование?', 'list_queue', {'limit': 10}),
    ('Что нужно проверить?', 'list_queue', {'limit': 10}),
]


@pytest.mark.parametrize('task,tool,arguments', CASES)
def test_unambiguous_phrases_get_a_rule_plan(task, tool, arguments):
    assert intent_plan(task) == (tool, arguments)


NOT_FOR_RULES = [
    '', '   ', 'hello', 'открой документ',
    # two intentions: the model has to plan them together
    'check duplicates and draft letters for them',
    'Найди дубли и подготовь письма.',
    # a letter without a kind is ambiguous
    'Draft a letter for the newest document.',
    'Подготовь письмо по документу.',
    'Write a reminder to the supplier about 3291971f49.',  # letter without a kind
]


@pytest.mark.parametrize('task', NOT_FOR_RULES)
def test_ambiguous_tasks_go_to_the_model(task):
    assert intent_plan(task) is None


EXPLANATIONS = [
    'how do I confirm a document?',
    'How does routing work?',
    'why did this invoice go to approval?',
    'what is a duplicate?',
    'what does approval mean?',
    'explain the threshold',
    'Как подтвердить документ?',
    'Почему документ ушёл на согласование?',
    'Что такое DocFlow?',
    'Объясни маршрутизацию.',
]


@pytest.mark.parametrize('task', EXPLANATIONS)
def test_explanation_questions_are_never_turned_into_actions(task):
    assert intent_plan(task) is None


def test_kakie_is_not_mistaken_for_how():
    # "как" inside "какие" used to turn a data question into an explanation
    assert intent_plan('Какие документы нужно на согласование?') == ('list_queue', {'limit': 10})
    assert intent_plan('как подтвердить документ?') is None


def test_a_draft_word_wins_even_when_the_queue_is_mentioned():
    assert intent_plan('Check the queue and draft a payment letter.') == (
        'draft_letter', {'doc_id': 'latest', 'kind': 'payment_request'})


def test_document_id_is_taken_from_the_text_and_lowercased():
    assert intent_plan('CHECK DUPLICATE 3291971F49') == ('check_duplicate', {'doc_id': '3291971f49'})
    assert intent_plan('check duplicate 0123456789ab') == ('check_duplicate', {'doc_id': '0123456789ab'})


def test_ids_outside_the_expected_length_are_not_guessed():
    # 13 hex characters: no id in the text, so the tool resolves "latest" itself
    assert intent_plan('check duplicate 0123456789abc') == ('check_duplicate', {'doc_id': 'latest'})


def test_doc_id_regex_contract():
    assert DOC_ID_RE.findall('id 3291971f49 and 0123456789ab') == ['3291971f49', '0123456789ab']
    assert DOC_ID_RE.search('no identifier here') is None


def test_explanation_has_priority_over_tool_words():
    # "how" wins even when the sentence mentions a draft: it is a question, not an action
    assert intent_plan('how do I draft a payment letter?') is None
