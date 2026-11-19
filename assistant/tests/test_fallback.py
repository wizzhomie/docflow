"""Rules planner and answer composition: the deterministic baseline behind the assistant."""
from __future__ import annotations

import pytest

from docflow_operator.fallback import answer_from, language_of, plan, run
from docflow_operator.tools import ToolError


def test_language_detection_uses_enough_cyrillic_letters():
    assert language_of('Покажи очередь документов') == 'ru'
    assert language_of('Show the queue') == 'en'
    assert language_of('да') == 'en'  # too short to guess


PLAN_CASES = [
    ('show the queue', [('list_queue', {'limit': 5})]),
    ('show document 3291971f49', [('get_document', {'doc_id': '3291971f49'})]),
    ('What is in 3291971f49?', [('get_document', {'doc_id': '3291971f49'})]),
    ('is 3291971f49 a duplicate?', [('check_duplicate', {'doc_id': '3291971f49'})]),
    ('check the newest for duplicates', [('check_duplicate', {'doc_id': 'latest'})]),
    ('draft a payment letter for 3291971f49',
     [('draft_letter', {'doc_id': '3291971f49', 'kind': 'payment_request'})]),
    ('draft a reply letter for 3291971f49',
     [('draft_letter', {'doc_id': '3291971f49', 'kind': 'supplier_reply'})]),
    ('prepare an approval letter for 3291971f49',
     [('draft_letter', {'doc_id': '3291971f49', 'kind': 'approval_request'}),
      ('propose_confirm', {'doc_id': '3291971f49'})]),
    ('propose confirming 3291971f49', [('propose_confirm', {'doc_id': '3291971f49'})]),
    ('give me a summary for today', [('summarise', {'period': 'today'})]),
    ('weekly report', [('summarise', {'period': 'week'})]),
    ('итоги за сегодня', [('summarise', {'period': 'today'})]),
    ('check duplicates and draft payment letters for 3291971f49',
     [('check_duplicate', {'doc_id': '3291971f49'}),
      ('draft_letter', {'doc_id': '3291971f49', 'kind': 'payment_request'})]),
]


@pytest.mark.parametrize('task,expected', PLAN_CASES)
def test_rules_plan(task, expected):
    assert plan(task) == expected


def test_an_id_without_a_known_intent_reads_the_document():
    assert plan('3291971f49') == [('get_document', {'doc_id': '3291971f49'})]


def test_summary_without_a_period_defaults_to_the_week():
    assert plan('summary please') == [('summarise', {'period': 'week'})]


def test_unknown_task_falls_back_to_the_queue():
    assert plan('hello there') == [('list_queue', {'limit': 5})]


def _step(tool, result, ok=True, **extra):
    step = {'n': 1, 'tool': tool, 'arguments': {}, 'result': result, 'ok': ok}
    step.update(extra)
    return step


QUEUE_RESULT = {
    'count': 2,
    'documents': [
        {'id': 'aaaaaaaaaa', 'type': 'invoice', 'number': '2026-501',
         'supplier': 'ТОВ «Терра-Буд»', 'total': 128000.0, 'currency': 'UAH', 'route': 'Payments'},
        {'id': 'bbbbbbbbbb', 'type': 'act', 'number': '2026-503',
         'supplier': 'ТОВ «Агро-Південь»', 'total': None, 'currency': None, 'route': 'Accounting'},
    ],
    'routes': {'Payments': 1, 'Accounting': 1},
    'needs_approval': ['dddddddddd'],
    'to_pay': ['aaaaaaaaaa'],
    'to_book': ['bbbbbbbbbb'],
}


def test_queue_answer_in_russian_lists_amounts_and_destinations():
    answer = answer_from([_step('list_queue', QUEUE_RESULT)], 'покажи очередь')
    assert 'В очереди 2 документ(ов)' in answer
    assert '128,000.00 UAH' in answer
    assert 'На согласование: dddddddddd' in answer
    assert 'На оплату: aaaaaaaaaa' in answer
    assert 'В учёт: bbbbbbbbbb' in answer
    assert 'None' not in answer


def test_queue_answer_in_english():
    answer = answer_from([_step('list_queue', QUEUE_RESULT)], 'show the queue')
    assert '2 document(s) waiting for review' in answer
    assert 'Approval: dddddddddd' in answer
    assert 'Payments: aaaaaaaaaa' in answer
    assert 'Accounting: bbbbbbbbbb' in answer


def test_document_without_a_total_uses_a_dash():
    result = {'documents': [{'id': 'x', 'number': '1', 'supplier': 's', 'route': 'r'}]}
    answer = answer_from([_step('list_queue', result)], 'show the queue')
    assert '—' in answer


def test_summary_answer_in_both_languages():
    result = {'waiting': {'count': 3, 'amount': 1234.5, 'to_pay': 1, 'needs_approval': 1, 'to_book': 1}}
    russian = answer_from([_step('summarise', result)], 'сводка')
    english = answer_from([_step('summarise', result)], 'summary')
    assert 'Ждут проверки: 3 на сумму 1,234.50' in russian
    assert '3 waiting for review, total 1,234.50' in english


def test_document_answer_includes_issues():
    result = {'document': {'type': 'invoice', 'number': '2026-501', 'supplier': 'ТОВ',
                           'total': 10.0, 'currency': 'UAH', 'route': 'Payments',
                           'issues': ['amounts do not add up', 'missing tax id']}}
    russian = answer_from([_step('get_document', result)], 'покажи документ')
    english = answer_from([_step('get_document', result)], 'show the document')
    assert 'Замечания: amounts do not add up; missing tax id' in russian
    assert 'Issues: amounts do not add up; missing tax id' in english


def test_duplicate_answer_in_both_languages():
    found = {'duplicate': True, 'matches': [{'id': 'cccccccccc'}]}
    none = {'duplicate': False, 'matches': []}
    assert 'Найден дубль: cccccccccc' in answer_from([_step('check_duplicate', found)], 'проверь дубли')
    assert 'Duplicate found: cccccccccc' in answer_from([_step('check_duplicate', found)], 'check duplicates')
    assert 'Дублей не найдено' in answer_from([_step('check_duplicate', none)], 'проверь дубли')
    assert 'No duplicate found' in answer_from([_step('check_duplicate', none)], 'check duplicates')


def test_letter_and_proposal_answers_never_promise_an_action():
    letter = {'path': '/tmp/drafts/a.md', 'kind': 'payment_request'}
    answer = answer_from([_step('draft_letter', letter)], 'draft a payment letter')
    assert 'Draft letter saved: /tmp/drafts/a.md' in answer
    assert 'human' in answer
    proposal = {'proposal': 'Confirm invoice 1 -> Payments.', 'confirmed': False}
    answer = answer_from([_step('propose_confirm', proposal)], 'propose confirming the document')
    assert 'Only a human can confirm it in DocFlow' in answer


def test_failed_and_unknown_steps_do_not_leak_into_the_answer():
    steps = [
        _step('list_queue', {'error': 'unreachable'}, ok=False),
        _step('make_coffee', {'x': 1}),
    ]
    assert answer_from(steps, 'show the queue') == ''


def test_no_steps_means_the_task_was_not_understood():
    assert answer_from([], 'покажи очередь') == 'Не удалось разобрать задачу.'
    assert answer_from([], 'show the queue') == 'Could not parse the task.'


def test_steps_without_an_explicit_ok_flag_are_treated_as_successful():
    answer = answer_from([{'tool': 'check_duplicate', 'result': {'duplicate': False}}], 'check duplicates')
    assert 'No duplicate found' in answer


class _Toolbox:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error

    def run(self, tool, arguments):
        if self.error:
            raise self.error
        return self.results.get(tool, {})


def test_run_executes_the_plan_and_marks_failures():
    toolbox = _Toolbox(results={'list_queue': QUEUE_RESULT})
    steps, answer = run('show the queue', toolbox)
    assert steps[0]['tool'] == 'list_queue'
    assert steps[0]['ok'] is True
    assert 'waiting for review' in answer


def test_run_never_raises_when_a_tool_fails():
    toolbox = _Toolbox(error=ToolError('DocFlow is unreachable'))
    steps, answer = run('show the queue', toolbox)
    assert steps[0]['ok'] is False
    assert 'DocFlow is unreachable' in steps[0]['result']['error']
    assert answer == ''  # failed steps are shown in the trace, not in the answer
