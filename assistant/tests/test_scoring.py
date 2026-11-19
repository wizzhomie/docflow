"""Scoring of the scripted scenarios: the function that decides whether the numbers hold."""
from __future__ import annotations

import pytest

from eval.scoring import score

SCENARIO = {
    'id': 'demo',
    'task': 'check this',
    'expect': {'tools': ['list_queue', 'check_duplicate'], 'answer_any': ['duplicate', '—']},
}


def steps(*items):
    return [{'tool': tool, 'ok': ok, 'refused': refused} for tool, ok, refused in items]


def test_all_three_conditions_must_hold():
    result = score(SCENARIO, steps(('list_queue', True, False), ('check_duplicate', True, False)),
                   'Duplicate found: cccccccccc')
    assert result == {'tool_ok': True, 'answer_ok': True, 'valid_calls': True, 'success': True}


def test_tools_must_be_called_in_order():
    result = score(SCENARIO, steps(('check_duplicate', True, False), ('list_queue', True, False)),
                   'Duplicate found')
    assert result['tool_ok'] is False
    assert result['success'] is False


def test_a_missing_tool_fails_the_run():
    result = score(SCENARIO, steps(('list_queue', True, False)), 'Duplicate found')
    assert result['tool_ok'] is False


def test_tools_any_accepts_one_of_the_listed_tools():
    scenario = {'expect': {'tools': ['list_queue', 'summarise'], 'tools_any': True, 'answer_any': []}}
    assert score(scenario, steps(('summarise', True, False)), 'ok')['tool_ok'] is True
    assert score(scenario, steps(('check_duplicate', True, False)), 'ok')['tool_ok'] is False


def test_finish_is_not_counted_as_a_tool():
    result = score(SCENARIO, steps(('list_queue', True, False), ('check_duplicate', True, False),
                                   ('finish', True, False)), 'Duplicate found')
    assert result['tool_ok'] is True


def test_answer_is_matched_case_insensitively_and_must_not_be_empty():
    assert score(SCENARIO, steps(('list_queue', True, False), ('check_duplicate', True, False)),
                 '  DUPLICATE FOUND  ')['answer_ok'] is True
    assert score(SCENARIO, steps(('list_queue', True, False), ('check_duplicate', True, False)),
                 '   ')['answer_ok'] is False


def test_no_keywords_means_any_non_empty_answer():
    scenario = {'expect': {'tools': ['list_queue'], 'answer_any': []}}
    assert score(scenario, steps(('list_queue', True, False)), 'anything')['answer_ok'] is True
    assert score(scenario, steps(('list_queue', True, False)), '')['answer_ok'] is False


def test_a_refused_repeat_is_not_an_invalid_call():
    result = score(SCENARIO, steps(('list_queue', True, False), ('list_queue', False, True),
                                   ('check_duplicate', True, False)), 'Duplicate found')
    assert result['valid_calls'] is True
    assert result['success'] is True


def test_a_failed_step_that_was_not_refused_fails_validation():
    result = score(SCENARIO, steps(('list_queue', False, False), ('check_duplicate', True, False)),
                   'Duplicate found')
    assert result['valid_calls'] is False
    assert result['success'] is False


@pytest.mark.parametrize('answer_any,answer,expected', [
    (['2026-501'], 'Invoice №2026-501 from Terra', True),
    (['Терра-Буд'], 'Supplier: ТОВ «Терра-Буд»', True),
    (['128'], 'total is 99', False),
])
def test_keywords_are_substrings(answer_any, answer, expected):
    scenario = {'expect': {'tools': ['list_queue'], 'answer_any': answer_any}}
    assert score(scenario, steps(('list_queue', True, False)), answer)['answer_ok'] is expected
