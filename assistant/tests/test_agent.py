"""Agent loop tests with a scripted fake model (no llama.cpp needed)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from test_tools import FakeDocFlow, confirmed, pending

from docflow_operator.agent import (
    Agent,
    ModelClient,
    ModelError,
    conversation_messages,
    looks_like_data_question,
    system_prompt,
)
from docflow_operator.fallback import plan
from docflow_operator.tools import Toolbox, step_schema


class FakeModel:
    def __init__(self, steps: list[dict | Exception]):
        self._steps = list(steps)
        self.calls: list[list[dict]] = []
        self.model = 'fake-model'

    def plan(self, messages):
        self.calls.append(list(messages))
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def close(self):
        pass


@pytest.fixture()
def toolbox(tmp_path: Path) -> Toolbox:
    inbox = [pending('aaaaaaaaaa', '2026-501', 'ТОВ «Терра-Буд»', 128000.0)]
    history = [confirmed('cccccccccc', '2026-501', 'ТОВ «Терра-Буд»', 128000.0)]
    return Toolbox(FakeDocFlow(inbox, history), tmp_path / 'drafts')


def test_agent_runs_two_tools_then_finishes(toolbox):
    model = FakeModel([
        {'tool': 'list_queue', 'limit': 5},
        {'tool': 'check_duplicate', 'doc_id': 'aaaaaaaaaa'},
        {'tool': 'finish', 'answer': 'One document, it is a duplicate.'},
    ])
    result = Agent(model, toolbox, max_steps=6).run('check the queue for duplicates and draft letters for them')
    assert [step['tool'] for step in result['steps']] == ['list_queue', 'check_duplicate', 'finish']
    assert result['model_answer'] == 'One document, it is a duplicate.'
    assert 'waiting for review' in result['answer']
    assert 'Duplicate found' in result['answer']
    assert result['fallback'] is False
    assert result['failure'] is None
    # the tool result is fed back to the model, not just returned to the UI
    assert any('TOOL RESULT list_queue' in message['content'] for message in model.calls[-1])


def test_agent_retries_after_bad_arguments(toolbox):
    model = FakeModel([
        {'tool': 'list_queue', 'limit': 'many'},
        {'tool': 'list_queue', 'limit': 1},
        {'tool': 'finish', 'answer': 'ok'},
    ])
    result = Agent(model, toolbox, max_steps=4).run('show the queue')
    steps = result['steps']
    assert steps[0]['ok'] is False
    assert steps[1]['ok'] is True
    # a single read answers the question; the model is not asked for another turn
    assert len(steps) == 2
    assert 'waiting for review' in result['answer']


def test_agent_falls_back_when_model_is_down(toolbox):
    model = FakeModel([ModelError('model timed out')])
    result = Agent(model, toolbox, max_steps=4).run('what is waiting?')
    assert result['fallback'] is True
    assert result['steps'][0]['tool'] == 'list_queue'
    assert '1 document' in result['answer']
    assert result['model_answer'] is not None


def test_agent_falls_back_when_model_loops_on_bad_tools(toolbox):
    model = FakeModel([
        {'tool': 'unknown_tool', 'x': 1},
        {'tool': 'unknown_tool', 'x': 1},
        {'tool': 'finish', 'answer': 'never reached'},
    ])
    result = Agent(model, toolbox, max_steps=4).run('what is waiting?')
    assert result['fallback'] is True
    assert 'repeated' in result['failure'] or result['failure'].startswith('tool failed twice')


def test_agent_stops_after_max_steps(toolbox):
    model = FakeModel([{'tool': 'list_queue', 'limit': 1}] * 10)
    result = Agent(model, toolbox, max_steps=2).run('check duplicates and draft letters for them')
    # The model never finishes, but the tools it did call still produce a usable answer.
    assert result['fallback'] is False
    assert len([s for s in result['steps'] if s['tool'] == 'list_queue']) >= 2
    assert 'waiting for review' in result['answer']


def test_prompt_lists_every_tool():
    prompt = system_prompt()
    for name in ('list_queue', 'get_document', 'check_duplicate', 'draft_letter', 'summarise',
                 'propose_confirm', 'finish'):
        assert name in prompt


def test_step_schema_is_json_serialisable():
    schema = step_schema()
    assert len(schema['oneOf']) == 7
    json.dumps(schema)
    assert all('const' in variant['properties']['tool'] for variant in schema['oneOf'])


def test_rule_plan_covers_common_tasks():
    assert plan('show the queue')[0][0] == 'list_queue'
    assert plan('what is the summary for today?')[0][0] == 'summarise'
    assert plan('is 3f9a2c1b7d a duplicate?')[0] == ('check_duplicate', {'doc_id': '3f9a2c1b7d'})
    assert plan('draft a payment letter for 3f9a2c1b7d')[0] == (
        'draft_letter', {'doc_id': '3f9a2c1b7d', 'kind': 'payment_request'})
    assert plan('propose confirming 3f9a2c1b7d')[0] == ('propose_confirm', {'doc_id': '3f9a2c1b7d'})


def test_composed_answer_skips_refused_steps():
    from docflow_operator.fallback import answer_from
    steps = [
        {'ok': True, 'tool': 'draft_letter', 'result': {'path': '/tmp/a.md', 'kind': 'payment_request'}},
        {'ok': False, 'refused': True, 'tool': 'draft_letter', 'result': {'error': 'repeated call, refused'}},
    ]
    answer = answer_from(steps, 'draft a payment letter')
    assert 'None' not in answer
    assert answer.count('/tmp/a.md') == 1


def test_faq_question_is_answered_from_the_knowledge_base_without_the_model(toolbox):
    model = FakeModel([])  # the model must not be called at all
    result = Agent(model, toolbox).run('how do I confirm a document?')
    assert 'Confirm' in result['answer']
    assert model.calls == []
    assert result['fallback'] is False
    assert [step['tool'] for step in result['steps']] == ['faq']


def test_faq_answers_in_russian(toolbox):
    model = FakeModel([])
    result = Agent(model, toolbox).run('Как подтвердить документ?')
    assert 'Подтвердить' in result['answer']
    assert model.calls == []


def test_data_question_answered_without_tools_is_replaced_with_real_data(toolbox):
    model = FakeModel([{'tool': 'finish', 'answer': 'There are 99 documents worth 1 000 000.'}])
    result = Agent(model, toolbox).run('what is waiting in the queue?')
    assert '99' not in result['answer']
    assert 'waiting for review' in result['answer']
    assert result['failure'] == 'data question answered without tools'


def test_a_draft_is_the_last_step(toolbox):
    model = FakeModel([
        {'tool': 'draft_letter', 'doc_id': 'aaaaaaaaaa', 'kind': 'payment_request'},
        {'tool': 'draft_letter', 'doc_id': 'aaaaaaaaaa', 'kind': 'payment_request'},
    ])
    result = Agent(model, toolbox).run('draft a payment letter for the newest document')
    assert [step['tool'] for step in result['steps']] == ['draft_letter']
    assert 'Draft letter saved' in result['answer']


def test_intent_shortcut_chooses_the_tool_without_the_model(toolbox):
    model = FakeModel([])  # must not be called
    result = Agent(model, toolbox).run('Propose confirming the newest document in the queue.')
    assert [step['tool'] for step in result['steps']] == ['propose_confirm']
    assert model.calls == []
    assert 'human' in result['answer'].lower()


def test_intent_shortcut_is_not_used_for_mixed_requests(toolbox):
    from docflow_operator.intent import intent_plan
    assert intent_plan('check duplicates and draft letters for them') is None
    assert intent_plan('which documents need approval?') == ('list_queue', {'limit': 10})
    assert intent_plan('draft a reply letter to the supplier') == (
        'draft_letter', {'doc_id': 'latest', 'kind': 'supplier_reply'})


def test_how_questions_never_become_actions():
    from docflow_operator.intent import intent_plan
    assert intent_plan('how do I confirm a document?') is None
    assert intent_plan('Как подтвердить документ?') is None
    assert intent_plan('propose confirming the newest document') == ('propose_confirm', {'doc_id': 'latest'})


def test_kakie_is_not_mistaken_for_how():
    from docflow_operator.intent import intent_plan
    # "как" as a prefix of "какие" must not turn a data question into an explanation
    assert intent_plan('Какие документы нужно на согласование?') == ('list_queue', {'limit': 10})
    assert intent_plan('как подтвердить документ?') is None


# --- the deterministic layers in front of the model ------------------------

def test_intent_shortcut_reports_how_it_answered(toolbox):
    model = FakeModel([])
    result = Agent(model, toolbox).run('Check whether document aaaaaaaaaa is a duplicate.')
    assert result['model'] == 'intent-rules'
    assert result['model_answer'] is None
    assert result['fallback'] is False
    assert result['failure'] is None
    assert result['language'] == 'en'
    assert isinstance(result['elapsed_ms'], int)
    assert model.calls == []


def test_intent_shortcut_with_bad_arguments_falls_through_to_the_model(toolbox, monkeypatch):
    monkeypatch.setattr('docflow_operator.agent.intent_plan',
                        lambda task: ('list_queue', {'limit': 0}))
    model = FakeModel([{'tool': 'list_queue', 'limit': 1}])
    result = Agent(model, toolbox).run('show me the queue')
    assert model.calls, 'the model must get a chance when the rule plan is invalid'
    assert result['fallback'] is False


def test_faq_path_reports_its_topic(toolbox):
    model = FakeModel([])
    result = Agent(model, toolbox).run('How do I confirm a document?')
    assert result['model'] == 'knowledge-base'
    assert result['steps'][0]['tool'] == 'faq'
    assert result['steps'][0]['arguments'] == {'topic': 'confirm'}
    assert result['model_answer'] == result['answer']


def test_a_data_question_never_uses_the_faq_even_for_a_faq_keyword(toolbox, monkeypatch):
    monkeypatch.setattr('docflow_operator.agent.faq_match',
                        lambda task: ('confirm', 'curated answer that does not belong here'))
    model = FakeModel([{'tool': 'list_queue', 'limit': 5}])
    result = Agent(model, toolbox).run('why is document aaaaaaaaaa waiting in the queue?')
    assert 'curated answer' not in result['answer']
    assert result['steps'][0]['tool'] == 'list_queue'


def test_a_platform_finish_answer_is_accepted_without_tools(toolbox):
    model = FakeModel([{'tool': 'finish', 'answer': 'Approval means a person must decide.'}])
    result = Agent(model, toolbox).run('Say something nice about today.')
    assert result['answer'] == 'Approval means a person must decide.'
    assert result['fallback'] is False
    assert result['failure'] is None


def test_refused_repeat_does_not_kill_an_answer_that_already_has_data(toolbox):
    model = FakeModel([
        {'tool': 'list_queue', 'limit': 5},
        {'tool': 'list_queue', 'limit': 5},
        {'tool': 'check_duplicate', 'doc_id': 'aaaaaaaaaa'},
    ])
    result = Agent(model, toolbox).run('check duplicates and draft letters for them')
    tools = [step['tool'] for step in result['steps']]
    assert tools == ['list_queue', 'list_queue']
    assert result['steps'][1]['refused'] is True
    assert result['failure'] is None
    assert 'waiting for review' in result['answer']


def test_two_failed_tool_calls_stop_the_loop(toolbox):
    model = FakeModel([
        {'tool': 'get_document', 'doc_id': 'ffffffffff'},
        {'tool': 'get_document', 'doc_id': 'eeeeeeeeee'},
    ])
    result = Agent(model, toolbox).run('show me the details of ffffffffff')
    assert result['failure'].startswith('tool failed twice')
    assert result['fallback'] is True


def test_time_budget_stops_the_loop(toolbox):
    model = FakeModel([{'tool': 'list_queue', 'limit': 5}])
    result = Agent(model, toolbox, time_budget=-1).run('check duplicates and draft letters')
    assert 'time budget' in result['failure']
    assert result['fallback'] is True
    assert 'waiting for review' in result['answer']


def test_model_name_is_reported(toolbox):
    result = Agent(FakeModel([{'tool': 'list_queue', 'limit': 1}]), toolbox).run('show the queue')
    assert result['model'] == 'fake-model'


def test_history_is_trimmed_and_passed_before_the_question(toolbox):
    history = [
        {'role': 'user', 'content': 'first'},
        {'role': 'assistant', 'content': 'answer one'},
        {'role': 'user', 'content': 'second'},
        {'role': 'assistant', 'content': 'answer two'},
        {'role': 'user', 'content': 'third'},
        {'role': 'assistant', 'content': 'x' * 2000},
        {'role': 'tool', 'content': 'ignored role'},
        {'role': 'user', 'content': '   '},
    ]
    model = FakeModel([{'tool': 'list_queue', 'limit': 1}])
    Agent(model, toolbox).run('show me the queue', history)

    messages = model.calls[0]
    assert messages[0]['role'] == 'system'
    assert messages[-1] == {'role': 'user', 'content': 'show me the queue'}
    middle = messages[1:-1]
    assert len(middle) <= 4
    assert all(message['role'] in ('user', 'assistant') for message in middle)
    assert all(len(message['content']) <= 1500 for message in middle)


def test_conversation_messages_always_end_with_the_new_question():
    messages = conversation_messages('hello', [{'role': 'assistant', 'content': 'hi'}])
    assert messages[0]['role'] == 'system'
    assert messages[-1] == {'role': 'user', 'content': 'hello'}


def test_data_question_detection():
    for question in ('what is in the queue?', 'check 3291971f49', 'how many documents wait?',
                     'is this a duplicate?', 'what is the total amount?'):
        assert looks_like_data_question(question), question
    for question in ('how do I confirm a document?', 'what does approval mean?', 'hello'):
        assert not looks_like_data_question(question), question


# --- model client contract --------------------------------------------------

def model_client(handler) -> ModelClient:
    http_client = httpx.Client(base_url='http://model.test', transport=httpx.MockTransport(handler))
    return ModelClient('http://model.test', 'fake-model', timeout=1.0, client=http_client)


def reply(step: dict | list | str) -> httpx.Response:
    content = step if isinstance(step, str) else json.dumps(step)
    return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})


def test_model_client_sends_a_constrained_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['payload'] = json.loads(request.content)
        return reply({'tool': 'list_queue', 'limit': 5})

    client = model_client(handler)
    step = client.plan([{'role': 'user', 'content': 'queue'}])
    client.close()

    assert step == {'tool': 'list_queue', 'limit': 5}
    payload = seen['payload']
    assert payload['model'] == 'fake-model'
    assert payload['temperature'] == 0.0
    assert payload['response_format']['type'] == 'json_schema'
    assert payload['response_format']['json_schema']['strict'] is True
    assert 'oneOf' in payload['response_format']['json_schema']['schema']


def test_model_client_translates_transport_failures():
    def timeout(request):
        raise httpx.ReadTimeout('slow', request=request)

    def unreachable(request):
        raise httpx.ConnectError('refused', request=request)

    with pytest.raises(ModelError, match='timed out'):
        model_client(timeout).plan([])
    with pytest.raises(ModelError, match='unreachable'):
        model_client(unreachable).plan([])


def test_model_client_rejects_http_errors():
    client = model_client(lambda request: httpx.Response(500))
    with pytest.raises(ModelError, match='returned 500'):
        client.plan([])


def test_model_client_rejects_broken_bodies():
    with pytest.raises(ModelError, match='unexpected response'):
        model_client(lambda request: httpx.Response(200, json={'choices': []})).plan([])


def test_model_client_rejects_non_json_content():
    with pytest.raises(ModelError, match='invalid JSON'):
        model_client(lambda request: reply('not json')).plan([])


def test_model_client_rejects_content_that_is_not_a_tool_call():
    with pytest.raises(ModelError, match='not a tool call'):
        model_client(lambda request: reply([1, 2, 3])).plan([])


def test_model_client_health_reports_both_outcomes():
    ok = model_client(lambda request: httpx.Response(200, text='{"status":"ok"}')).health()
    assert ok['ok'] is True
    assert 'ok' in ok['status']

    def down(request):
        raise httpx.ConnectError('refused', request=request)
    bad = model_client(down).health()
    assert bad['ok'] is False
    assert bad['status']


def test_a_loop_that_runs_out_of_steps_still_answers_from_its_tools(toolbox):
    model = FakeModel([
        {'tool': 'list_queue', 'limit': 5},
        {'tool': 'check_duplicate', 'doc_id': 'aaaaaaaaaa'},
        {'tool': 'get_document', 'doc_id': 'aaaaaaaaaa'},
    ])
    result = Agent(model, toolbox, max_steps=2).run('check duplicates and draft letters for them')
    assert [step['tool'] for step in result['steps']] == ['list_queue', 'check_duplicate']
    assert result['fallback'] is False
    assert 'Duplicate found' in result['answer']
