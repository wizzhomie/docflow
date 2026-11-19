"""Knowledge base tests: curated answers, language choice and the safety facts."""
from __future__ import annotations

import re

import pytest

from docflow_operator.knowledge import FAQ, SUPPORT_FACTS, faq_answer, faq_match

CYRILLIC = re.compile(r'[а-яіїєґ]', re.IGNORECASE)

TOPIC_CASES = [
    ('Как подтвердить документ?', 'confirm'),
    ('How do I confirm a document?', 'confirm'),
    ('Почему документ ушёл на согласование?', 'approval'),
    ('What is the approval threshold?', 'approval'),
    ('Документ уже был проведён — это дубль?', 'duplicate'),
    ('Is this a duplicate?', 'duplicate'),
    ('Что делать, если суммы не сходятся?', 'issues'),
    ('The amounts do not add up, what should I do?', 'issues'),
    ('Куда уходит счёт?', 'routing'),
    ('Where does an act go?', 'routing'),
    ('Что ты умеешь?', 'capabilities'),
    ('What can you do?', 'capabilities'),
    ('Как работает DocFlow?', 'what-is-docflow'),
    ('What is DocFlow?', 'what-is-docflow'),
    ('Используется ли нейросеть для разбора?', 'odm'),
    ('Does DocFlow use an LLM?', 'odm'),
]


@pytest.mark.parametrize('question,topic', TOPIC_CASES)
def test_common_support_questions_have_a_curated_answer(question, topic):
    match = faq_match(question)
    assert match is not None, f'no FAQ answer for {question!r}'
    assert match[0] == topic
    assert len(match[1]) > 40


def test_answers_follow_the_language_of_the_question():
    russian = faq_match('Почему документ ушёл на согласование?')
    english = faq_match('Why did the document go to approval?')
    assert CYRILLIC.search(russian[1])
    assert not CYRILLIC.search(english[1])
    assert russian[1] != english[1]


def test_short_russian_snippet_does_not_switch_the_answer_language():
    # two Cyrillic letters are not enough: the question stays English
    assert not CYRILLIC.search(faq_match('Confirm да?')[1])
    # a single Russian word with 8 letters is enough to answer in Russian
    assert CYRILLIC.search(faq_match('подтвердить?')[1])


def test_unknown_questions_have_no_curated_answer():
    assert faq_match('Who won the football match yesterday?') is None
    assert faq_answer('Who won the football match yesterday?') is None


def test_faq_answer_is_the_same_as_the_match():
    match = faq_match('Как подтвердить документ?')
    assert faq_answer('Как подтвердить документ?') == match[1]


def test_the_faq_table_is_well_formed():
    ids = [entry['id'] for entry in FAQ]
    assert len(ids) == len(set(ids)), 'duplicate FAQ ids'
    for entry in FAQ:
        assert entry['keywords'], entry['id']
        assert all(isinstance(word, str) and word for word in entry['keywords'])
        assert entry['ru'].strip() and entry['en'].strip()
        assert not CYRILLIC.search(entry['en']), f"English answer of {entry['id']} has Cyrillic"


def test_safety_facts_are_present_in_the_prompt_context():
    # These sentences are the contract with the model: if they disappear from the facts,
    # the model may start promising actions the service cannot perform.
    for phrase in ('100 000', 'confirmed by a person', 'cannot confirm, pay, send mail',
                   'do not know', 'routing'):
        assert phrase.lower() in SUPPORT_FACTS.lower()


def test_the_support_facts_stay_short_enough_for_the_prompt():
    # The whole prompt is pre-warmed in the KV cache; facts twice as long cost seconds.
    assert len(SUPPORT_FACTS) < 2500
