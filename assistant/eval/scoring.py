"""Scoring for the scripted scenarios: tools called, answer keywords, call validity.

Kept separate from the runner so it can be tested without DocFlow, a model or the
network: the score is the part that decides whether the numbers in the README are true.
"""
from __future__ import annotations


def _tools_match(expected: dict, tools: list[str]) -> bool:
    if expected.get('tools_any'):
        return any(tool in tools for tool in expected['tools'])
    position = 0
    for wanted in expected['tools']:
        while position < len(tools) and tools[position] != wanted:
            position += 1
        if position == len(tools):
            return False
        position += 1
    return True


def score(scenario: dict, steps: list[dict], answer: str) -> dict:
    """Compare one run with the scenario's `expect` block.

    * tool_ok: the expected tools were called, in order (or any of them for `tools_any`);
    * answer_ok: the answer is not empty and contains one of the expected keywords;
    * valid_calls: every step succeeded; a refused repeat is the guard working, not a bug;
    * success: all three.
    """
    expect = scenario['expect']
    tools = [step['tool'] for step in steps if step.get('tool') != 'finish']
    tool_ok = _tools_match(expect, tools)

    keywords = [word.casefold() for word in expect.get('answer_any', [])]
    stripped = answer.strip()
    answer_ok = bool(stripped) and (not keywords or any(word in stripped.casefold() for word in keywords))

    valid = all(step.get('ok') for step in steps if not step.get('refused'))
    return {
        'tool_ok': tool_ok,
        'answer_ok': answer_ok,
        'valid_calls': valid,
        'success': tool_ok and answer_ok and valid,
    }
