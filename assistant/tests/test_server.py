"""HTTP surface: widget, demo page, config, health, chat validation, traces, warm-up."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import docflow_operator.server as server
from docflow_operator.docflow import DocFlowError


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.app)


# --- static surface ---------------------------------------------------------

def test_widget_script_is_served(client):
    response = client.get('/widget.js')
    assert response.status_code == 200
    assert 'javascript' in response.headers['content-type']
    assert response.headers['cache-control'] == 'no-store'
    assert 'docflowAssistantLoaded' in response.text
    assert 'dfo-bubble' in response.text


def test_demo_page_hosts_the_widget(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'DocFlow assistant' in response.text
    assert '/widget.js' in response.text


def test_config_endpoint_stays_public_but_shows_no_internal_addresses(client):
    body = client.get('/api/config').json()
    assert body == {'model': server.settings.model_name, 'max_steps': server.settings.max_steps}


# --- health -----------------------------------------------------------------

def test_health_is_ok_when_both_dependencies_answer(client, monkeypatch):
    class FakeDocFlow:
        def __init__(self, *args, **kwargs):
            pass

        def health(self):
            return {'status': 'ok'}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def health(self):
            return {'ok': True, 'status': 'ok'}

    monkeypatch.setattr(server, 'DocFlowClient', FakeDocFlow)
    monkeypatch.setattr(server, 'ModelClient', FakeModel)
    body = client.get('/api/health').json()
    assert body == {'ok': True, 'docflow': {'ok': True, 'detail': 'ok'},
                    'model': {'ok': True, 'status': 'ok'}}


def test_health_reports_a_broken_docflow(client, monkeypatch):
    class FakeDocFlow:
        def __init__(self, *args, **kwargs):
            pass

        def health(self):
            raise DocFlowError('DocFlow is unreachable at http://127.0.0.1:8765')

    monkeypatch.setattr(server, 'DocFlowClient', FakeDocFlow)
    monkeypatch.setattr(server, 'ModelClient', lambda *a, **k: SimpleNamespace(
        health=lambda: {'ok': True, 'status': 'ok'}))
    body = client.get('/api/health').json()
    assert body['ok'] is False
    assert body['docflow']['ok'] is False
    assert 'unreachable' in body['docflow']['detail']


def test_health_reports_a_broken_model(client, monkeypatch):
    monkeypatch.setattr(server, 'DocFlowClient', lambda *a, **k: SimpleNamespace(
        health=lambda: {'status': 'ok'}))
    monkeypatch.setattr(server, 'ModelClient', lambda *a, **k: SimpleNamespace(
        health=lambda: {'ok': False, 'status': 'connection refused'}))
    body = client.get('/api/health').json()
    assert body['ok'] is False
    assert body['docflow']['ok'] is True
    assert body['model']['ok'] is False


# --- chat requests ----------------------------------------------------------

def test_chat_requires_a_user_message_last(client):
    response = client.post('/api/chat', json={'messages': [{'role': 'assistant', 'content': 'hi'}]})
    assert response.status_code == 400


def test_chat_passes_history_to_the_agent(client, monkeypatch):
    seen = {}

    def fake_run(task, history):
        seen['task'] = task
        seen['history'] = history
        return {'task': task, 'answer': 'ok', 'steps': [], 'fallback': False}

    monkeypatch.setattr(server, '_run_agent', fake_run)
    response = client.post('/api/chat', json={'messages': [
        {'role': 'user', 'content': 'hello'},
        {'role': 'assistant', 'content': 'hi, how can I help?'},
        {'role': 'user', 'content': 'what is in the queue?'},
    ]})
    assert response.status_code == 200
    assert response.json()['answer'] == 'ok'
    assert seen['task'] == 'what is in the queue?'
    assert seen['history'][0] == {'role': 'user', 'content': 'hello'}


def test_empty_chat_is_rejected(client):
    response = client.post('/api/chat', json={'messages': [{'role': 'user', 'content': '   '}]})
    assert response.status_code == 400


def test_chat_rejects_more_than_twenty_messages(client):
    messages = [{'role': 'user', 'content': f'message {index}'} for index in range(21)]
    response = client.post('/api/chat', json={'messages': messages})
    assert response.status_code == 422


def test_chat_accepts_exactly_twenty_messages(client, monkeypatch):
    monkeypatch.setattr(server, '_run_agent', lambda task, history: {'answer': 'ok'})
    messages = [{'role': 'assistant', 'content': 'a'} for _ in range(19)]
    messages.append({'role': 'user', 'content': 'last question'})
    assert client.post('/api/chat', json={'messages': messages}).status_code == 200


def test_chat_surfaces_agent_failures_as_500(client, monkeypatch):
    from fastapi import HTTPException

    def boom(task, history):
        raise HTTPException(500, 'assistant failed: boom')

    monkeypatch.setattr(server, '_run_agent', boom)
    response = client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'hi'}]})
    assert response.status_code == 500
    assert 'assistant failed' in response.json()['detail']


def test_chat_is_post_only(client):
    assert client.get('/api/chat').status_code == 405


def test_chat_is_rate_limited_per_client(client, monkeypatch):
    monkeypatch.setattr(server, '_run_agent', lambda task, history: {'answer': 'ok'})
    server._chat_limiter.reset()
    try:
        for _ in range(server.settings.chat_rate_limit):
            ok = client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'hi'}]})
            assert ok.status_code == 200
        blocked = client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'hi'}]})
        assert blocked.status_code == 429
        assert int(blocked.headers['retry-after']) >= 1
        # the proxy's client address is what counts, not the proxy itself
        other = client.post('/api/chat', headers={'X-Forwarded-For': '203.0.113.9'},
                            json={'messages': [{'role': 'user', 'content': 'hi'}]})
        assert other.status_code == 200
    finally:
        server._chat_limiter.reset()


def test_a_question_does_not_wait_forever_for_a_busy_model(monkeypatch, tmp_path):
    closed = []
    settings = SimpleNamespace(
        ensure_dirs=lambda: None, docflow_url='x', docflow_timeout=1.0,
        model_url='x', model_name='m', model_timeout=1.0, temperature=0.0,
        drafts_dir=tmp_path, max_steps=4, time_budget=10.0, traces_path=tmp_path / 't.jsonl',
        chat_queue_timeout=0.01,
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(server, 'settings', settings)
    monkeypatch.setattr(server, 'DocFlowClient', FakeClient)
    monkeypatch.setattr(server, 'ModelClient', FakeClient)
    monkeypatch.setattr(server, 'Toolbox', lambda *args: None)

    assert server._chat_lock.acquire(timeout=1)
    try:
        with pytest.raises(Exception) as error:
            server._run_agent('hi', [])
    finally:
        server._chat_lock.release()
    assert getattr(error.value, 'status_code', None) == 429
    assert closed == [True, True]


def test_no_cors_by_default_because_the_widget_is_same_origin(client, monkeypatch):
    monkeypatch.setattr(server, '_run_agent', lambda task, history: {'answer': 'ok'})
    origin = 'https://docflow.example'
    response = client.post('/api/chat', headers={'Origin': origin},
                           json={'messages': [{'role': 'user', 'content': 'hi'}]})
    assert 'access-control-allow-origin' not in response.headers
    # no middleware means the browser's preflight is not answered either
    preflight = client.options('/api/chat', headers={
        'Origin': origin,
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type',
    })
    assert preflight.status_code == 405


# --- one-shot evaluation endpoint -------------------------------------------

def test_ask_runs_without_history(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, '_run_agent', lambda task, history: seen.update(
        task=task, history=history) or {'answer': 'ok'})
    response = client.post('/api/ask', json={'messages': [{'role': 'user', 'content': 'queue?'}]})
    assert response.status_code == 200
    assert seen == {'task': 'queue?', 'history': []}


def test_ask_rejects_an_empty_task(client):
    response = client.post('/api/ask', json={'messages': [{'role': 'user', 'content': '  '}]})
    assert response.status_code == 400


def test_ask_rejects_an_empty_message_list(client):
    assert client.post('/api/ask', json={'messages': []}).status_code == 422


# --- traces -----------------------------------------------------------------

def test_traces_are_returned_newest_first_and_invalid_lines_are_skipped(client, monkeypatch, tmp_path):
    path = tmp_path / 'traces.jsonl'
    path.write_text('\n'.join([
        json.dumps({'n': 1}),
        'not json at all',
        json.dumps({'n': 2}),
        json.dumps({'n': 3}),
    ]) + '\n', encoding='utf-8')
    monkeypatch.setattr(server, 'settings', SimpleNamespace(traces_path=path, traces_public=True))
    body = client.get('/api/traces', params={'limit': 2}).json()
    assert [row['n'] for row in body] == [3, 2]
    # reading past the invalid line proves it is skipped, not fatal
    body = client.get('/api/traces', params={'limit': 10}).json()
    assert [row['n'] for row in body] == [3, 2, 1]


def test_traces_are_empty_when_nothing_ran(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'settings', SimpleNamespace(
        traces_path=tmp_path / 'missing.jsonl', traces_public=True))
    assert client.get('/api/traces').json() == []


def test_traces_are_off_unless_explicitly_turned_on(client):
    # the trace file holds every visitor's question; it is not a public feed
    assert client.get('/api/traces').status_code == 404


def test_traces_limit_is_clamped(client, monkeypatch, tmp_path):
    path = tmp_path / 'traces.jsonl'
    path.write_text('\n'.join(json.dumps({'n': index}) for index in range(40)) + '\n',
                    encoding='utf-8')
    monkeypatch.setattr(server, 'settings', SimpleNamespace(traces_path=path, traces_public=True))
    body = client.get('/api/traces', params={'limit': 1000}).json()
    assert len(body) == 20


def test_save_trace_appends_one_json_line(monkeypatch, tmp_path):
    path = tmp_path / 'data' / 'traces.jsonl'
    monkeypatch.setattr(server, 'settings', SimpleNamespace(traces_path=path))
    server._save_trace({'task': 'x', 'answer': 'y'})
    server._save_trace({'task': 'z', 'answer': 'w'})
    lines = path.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1]) == {'task': 'z', 'answer': 'w'}


def test_a_broken_trace_file_never_breaks_a_request(monkeypatch, tmp_path):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('x', encoding='utf-8')
    monkeypatch.setattr(server, 'settings', SimpleNamespace(traces_path=blocker / 'traces.jsonl'))
    server._save_trace({'task': 'x'})  # OSError is swallowed on purpose


# --- assembling a run -------------------------------------------------------

def test_run_agent_turns_an_unexpected_failure_into_a_500_and_closes_clients(monkeypatch, tmp_path):
    closed = []
    settings = SimpleNamespace(
        ensure_dirs=lambda: None, docflow_url='x', docflow_timeout=1.0,
        model_url='x', model_name='m', model_timeout=1.0, temperature=0.0,
        drafts_dir=tmp_path, max_steps=4, time_budget=10.0, traces_path=tmp_path / 't.jsonl',
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            closed.append(True)

    class BoomAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, task, history):
            raise RuntimeError('boom')

    monkeypatch.setattr(server, 'settings', settings)
    monkeypatch.setattr(server, 'DocFlowClient', FakeClient)
    monkeypatch.setattr(server, 'ModelClient', FakeClient)
    monkeypatch.setattr(server, 'Toolbox', lambda *args: None)
    monkeypatch.setattr(server, 'Agent', BoomAgent)

    with pytest.raises(Exception) as error:
        server._run_agent('hi', [])
    assert getattr(error.value, 'status_code', None) == 500
    assert 'assistant failed: boom' in error.value.detail
    assert closed == [True, True]


def test_run_agent_wires_clients_together_and_records_a_trace(monkeypatch, tmp_path):
    drafts = tmp_path / 'drafts'
    traces = tmp_path / 'traces.jsonl'
    settings = SimpleNamespace(
        ensure_dirs=lambda: drafts.mkdir(parents=True, exist_ok=True),
        docflow_url='http://docflow.test', docflow_timeout=5.0,
        model_url='http://model.test', model_name='fake-model', model_timeout=5.0,
        temperature=0.0, drafts_dir=drafts, max_steps=4, time_budget=10.0, traces_path=traces,
    )
    captured = {}

    class FakeDocFlow:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            captured['docflow_closed'] = True

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            captured['model_closed'] = True

    class FakeAgent:
        def __init__(self, model, toolbox, max_steps, time_budget):
            captured['wiring'] = (model, toolbox, max_steps, time_budget)

        def run(self, task, history):
            captured['task'] = task
            captured['history'] = history
            return {'task': task, 'answer': 'ok', 'steps': []}

    monkeypatch.setattr(server, 'settings', settings)
    monkeypatch.setattr(server, 'DocFlowClient', FakeDocFlow)
    monkeypatch.setattr(server, 'ModelClient', FakeModel)
    monkeypatch.setattr(server, 'Toolbox', lambda docflow, drafts_dir: ('toolbox', drafts_dir))
    monkeypatch.setattr(server, 'Agent', FakeAgent)

    result = server._run_agent('hello', [{'role': 'user', 'content': 'earlier'}])

    assert result['answer'] == 'ok'
    assert isinstance(result['total_ms'], int)
    assert captured['wiring'][2:] == (4, 10.0)
    assert captured['task'] == 'hello'
    assert captured['history'] == [{'role': 'user', 'content': 'earlier'}]
    assert captured['docflow_closed'] is True and captured['model_closed'] is True
    assert json.loads(traces.read_text(encoding='utf-8').splitlines()[0])['answer'] == 'ok'


# --- prompt warm-up ---------------------------------------------------------

class _WarmClient:
    def __init__(self, payloads, fail_times=0):
        self.payloads = payloads
        self.fail_times = fail_times

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, path, json=None):
        if self.fail_times:
            self.fail_times -= 1
            raise httpx.ConnectError('model still loading')
        self.payloads.append(json)


def test_warm_up_sends_the_system_prompt_once(monkeypatch):
    payloads = []
    server._warm_model(lambda: _WarmClient(payloads))
    assert len(payloads) == 1
    assert payloads[0]['model'] == server.settings.model_name
    assert payloads[0]['messages'][0]['role'] == 'system'
    assert 'FACTS about DocFlow' in payloads[0]['messages'][0]['content']
    assert payloads[0]['max_tokens'] == 1


def test_warm_up_retries_while_the_model_loads(monkeypatch):
    payloads = []
    slept = []
    monkeypatch.setattr(server.time, 'sleep', slept.append)
    flaky = _WarmClient(payloads, fail_times=2)
    server._warm_model(lambda: flaky)
    assert len(payloads) == 1
    assert slept == [3, 3]


def test_warm_up_gives_up_after_twenty_attempts(monkeypatch):
    attempts = []

    def factory():
        attempts.append(1)
        return _WarmClient([], fail_times=1)

    monkeypatch.setattr(server.time, 'sleep', lambda seconds: None)
    server._warm_model(factory)
    assert len(attempts) == 20
