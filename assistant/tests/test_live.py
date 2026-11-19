"""Live smoke tests: the real DocFlow, the real model and the real operator server.

Skipped unless the services are up, so the default test run stays offline:

    scripts/deploy.sh start
    .venv/bin/python -m pytest -m live
"""
from __future__ import annotations

import httpx
import pytest

from docflow_operator.agent import ModelClient, system_prompt
from docflow_operator.config import get_settings
from docflow_operator.tools import TOOLS

pytestmark = pytest.mark.live

ALLOWED_TOOL_CALLS = {tool['name'] for tool in TOOLS} | {'faq'}


def _up(url: str, timeout: float = 3.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code < 500
    except httpx.HTTPError:
        return False


@pytest.fixture(scope='module')
def services():
    settings = get_settings()
    operator = f'http://127.0.0.1:{settings.port}'
    checks = {
        'DocFlow': f'{settings.docflow_url}/api/health',
        'model': f'{settings.model_url}/health',
        'operator': f'{operator}/api/health',
    }
    down = [name for name, url in checks.items() if not _up(url)]
    if down:
        pytest.skip('services are not running: ' + ', '.join(down) + ' (start with scripts/deploy.sh)')
    return settings, operator


def test_docflow_is_healthy(services):
    settings, _ = services
    body = httpx.get(f'{settings.docflow_url}/api/health', timeout=5).json()
    assert body.get('status') in ('ok', 'healthy') or body.get('ok') is True


def test_model_answers_for_the_operator(services):
    settings, _ = services
    # A cold model spends ~40 s on the warm prompt before it answers: give it room.
    model = ModelClient(settings.model_url, settings.model_name, timeout=120)
    try:
        assert model.health()['ok'] is True
        step = model.plan([{'role': 'system', 'content': system_prompt()},
                           {'role': 'user', 'content': 'What is waiting for review?'}])
    finally:
        model.close()
    assert isinstance(step, dict)
    assert step.get('tool') in {tool['name'] for tool in TOOLS} | {'finish'}


def test_operator_reports_the_whole_stack(services):
    _, operator = services
    body = httpx.get(f'{operator}/api/health', timeout=10).json()
    assert body['ok'] is True
    assert body['docflow']['ok'] is True
    assert body['model']['ok'] is True


def test_chat_answers_from_real_data_and_stays_read_only(services):
    _, operator = services
    response = httpx.post(f'{operator}/api/chat', timeout=90,
                          json={'messages': [{'role': 'user',
                                              'content': 'Show me the documents waiting for review.'}]})
    assert response.status_code == 200
    body = response.json()
    assert body['answer'].strip()
    assert body['steps'], 'an answer about the queue must come from tools'
    called = {step['tool'] for step in body['steps'] if step.get('ok')}
    assert called <= ALLOWED_TOOL_CALLS
    assert not called & {'confirm', 'send', 'pay'}
    assert isinstance(body['elapsed_ms'], int)
