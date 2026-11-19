"""The assistant loop: a small local model plans, typed tools execute, code grounds the answer.

The assistant is built into DocFlow as a support chat: it explains how the platform works
and answers questions about the live review queue. Two rules keep it honest:

* product questions are answered only from the facts in ``knowledge.py``;
* questions about concrete documents, numbers or duplicates must go through tools, and the
  answer is composed by code from the tool output, so a 1.5B model cannot invent amounts.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from .fallback import answer_from, language_of
from .fallback import run as run_rules
from .intent import intent_plan
from .knowledge import SUPPORT_FACTS, faq_match
from .tools import ArgumentError, Toolbox, ToolError, result_text, step_schema, tool_prompt, validate_arguments


class ModelError(RuntimeError):
    """The model server failed, timed out or produced something that is not JSON."""


SYSTEM_TEMPLATE = """You are the support assistant built into DocFlow, a document intake platform. People ask in plain language about the platform or the current review queue. Answer in their language, briefly.

Tools:
{tools}

How to answer:
1. Platform questions (how it works, routing, confirmation, errors, thresholds) are explanations: call finish with an answer from the FACTS. Questions like "how do I confirm" or "why did it go to approval" are not requests to act.
2. Questions about concrete documents, the queue, amounts or duplicates must use tools. Never invent ids or numbers; if the user gives no id, use doc_id "latest".
3. One tool per step; never call the same tool with the same arguments twice; finish when done.
4. Never claim you confirmed, paid or sent anything.
5. finish(answer): at most three short sentences with the key numbers and ids.
6. If neither the facts nor the tools cover the question, say you do not know and suggest the DocFlow team.

Examples:
User: how do I confirm a document?
{{"tool":"finish","answer":"Open it in DocFlow, check the extracted fields and press Confirm. Every document is confirmed by a person."}}
User: what is waiting today?
{{"tool":"list_queue","limit":10}}

{facts}
"""

DOC_ID_RE = re.compile(r'\b[0-9a-f]{8,12}\b')
# Only questions that point at *state* (a document, the queue, amounts, duplicates) need
# tools. Generic "how do I confirm" or "what does approval mean" are support questions.
DATA_QUESTION_RE = re.compile(
    r'(\b[0-9a-f]{8,12}\b|очеред|queue|ждут|ждёт|waiting|сколько|how many|'
    r'дубл|duplicate|этот документ|этот счёт|этот счёт|this document|this invoice|'
    r'входящ|inbox|сумм|amount|номер|number|какие документ|which document)', re.IGNORECASE)


def system_prompt() -> str:
    return SYSTEM_TEMPLATE.format(tools=tool_prompt(), facts=SUPPORT_FACTS)


def looks_like_data_question(task: str) -> bool:
    return bool(DATA_QUESTION_RE.search(task))


# Tasks that may need a second tool after reading the queue: drafts, proposals, duplicate
# checks per document. Everything else is answered by a single read and stops there —
# the model does not get another turn it would only spend repeating itself.
MULTI_STEP_RE = re.compile(
    r'(черновик|письм|draft|letter|подтверд|confirm|propose|предлож|дубл|duplicate)', re.IGNORECASE)


FINAL_TOOLS = ('draft_letter', 'propose_confirm')


def _single_step(task: str) -> bool:
    return not MULTI_STEP_RE.search(task)


def conversation_messages(task: str, history: list[dict] | None) -> list[dict]:
    """System prompt, a short trimmed history, then the new question."""
    messages: list[dict] = [{'role': 'system', 'content': system_prompt()}]
    for message in (history or [])[-4:]:
        role = message.get('role')
        content = str(message.get('content') or '').strip()
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content[:1500]})
    messages.append({'role': 'user', 'content': task})
    return messages


class ModelClient:
    """Minimal OpenAI-compatible client for llama-server."""

    def __init__(self, base_url: str, model: str, timeout: float = 180.0,
                 temperature: float = 0.0, max_tokens: int = 256,
                 client: httpx.Client | None = None):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict:
        try:
            response = self._client.get('/health', timeout=5)
            return {'ok': response.status_code == 200, 'status': response.text.strip()}
        except httpx.HTTPError as error:
            return {'ok': False, 'status': str(error)}

    def plan(self, messages: list[dict]) -> dict:
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'response_format': {
                'type': 'json_schema',
                'json_schema': {'name': 'operator_step', 'strict': True, 'schema': step_schema()},
            },
        }
        try:
            response = self._client.post('/v1/chat/completions', json=payload, timeout=self.timeout)
        except httpx.TimeoutException as error:
            raise ModelError('model timed out') from error
        except httpx.HTTPError as error:
            raise ModelError(f'model server unreachable: {error}') from error
        if response.status_code >= 400:
            raise ModelError(f'model server returned {response.status_code}')
        try:
            body = response.json()
            content = body['choices'][0]['message']['content'] or ''
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ModelError('model server returned an unexpected response') from error
        try:
            step = json.loads(content)
        except ValueError as error:
            raise ModelError(f'model returned invalid JSON: {content[:120]!r}') from error
        if not isinstance(step, dict) or 'tool' not in step:
            raise ModelError('model response is not a tool call')
        return step


class Agent:
    def __init__(self, model: ModelClient, toolbox: Toolbox, max_steps: int = 4,
                 time_budget: float = 120.0):
        self.model = model
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.time_budget = time_budget

    def run(self, task: str, history: list[dict] | None = None) -> dict:
        started = time.time()

        # Unambiguous phrasings get the tool from rules: a sampled tool choice is the
        # one place where a small model still picks the wrong action on a clear question.
        if True:
            planned = intent_plan(task)
            if planned:
                tool, arguments = planned
                try:
                    validate_arguments(tool, arguments)
                    result = self.toolbox.run(tool, arguments)
                    step = {'n': 1, 'tool': tool, 'arguments': arguments, 'result': result,
                            'ok': True, 'ms': round((time.time() - started) * 1000)}
                    return {
                        'task': task,
                        'answer': answer_from([step], task).strip(),
                        'model_answer': None,
                        'steps': [step],
                        'language': language_of(task),
                        'fallback': False,
                        'failure': None,
                        'model': 'intent-rules',
                        'elapsed_ms': round((time.time() - started) * 1000),
                    }
                except (ArgumentError, ToolError):
                    pass  # ambiguous data after all: let the model try

        # Common support questions are answered from the curated knowledge base without
        # touching the model: instant, free of hallucinations, and no CPU latency.
        if not looks_like_data_question(task):
            curated = faq_match(task)
            if curated:
                topic, answer = curated
                return {
                    'task': task,
                    'answer': answer,
                    'model_answer': answer,
                    'steps': [{'n': 1, 'tool': 'faq', 'arguments': {'topic': topic},
                               'result': {'answer': answer}, 'ok': True, 'ms': 0}],
                    'language': language_of(task),
                    'fallback': False,
                    'failure': None,
                    'model': 'knowledge-base',
                    'elapsed_ms': round((time.time() - started) * 1000),
                }

        messages = conversation_messages(task, history)
        steps: list[dict] = []
        seen: set[str] = set()
        tool_errors = 0
        answer: str | None = None
        used_fallback = False
        failure: str | None = None

        for step_no in range(1, self.max_steps + 1):
            if time.time() - started > self.time_budget:
                failure = f'time budget of {int(self.time_budget)}s exceeded'
                break
            step_started = time.time()
            try:
                decision = self.model.plan(messages)
            except ModelError as error:
                failure = str(error)
                break

            tool = decision.get('tool')
            arguments = {key: value for key, value in decision.items() if key != 'tool'}
            signature = json.dumps({'tool': tool, 'arguments': arguments}, sort_keys=True, ensure_ascii=False)

            if tool == 'finish':
                steps.append({'n': step_no, 'tool': 'finish', 'arguments': arguments,
                              'result': {'answer': arguments.get('answer', '')},
                              'ok': True, 'ms': round((time.time() - step_started) * 1000)})
                answer = str(arguments.get('answer') or '').strip()
                break

            if signature in seen:
                steps.append({'n': step_no, 'tool': tool, 'arguments': arguments,
                              'result': {'error': 'repeated call, refused'},
                              'ok': False, 'refused': True,
                              'ms': round((time.time() - step_started) * 1000)})
                messages.append({'role': 'assistant', 'content': json.dumps(decision, ensure_ascii=False)})
                messages.append({'role': 'user', 'content':
                                 'TOOL ERROR: you already called this tool with these arguments. '
                                 'Use a different tool or call finish(answer).'})
                if not [step for step in steps if step.get('ok') and step.get('tool') != 'finish']:
                    failure = 'model repeated the same tool call'
                break

            seen.add(signature)
            try:
                validate_arguments(tool, arguments)
                result = self.toolbox.run(tool, arguments)
                steps.append({'n': step_no, 'tool': tool, 'arguments': arguments,
                              'result': result, 'ok': True, 'ms': round((time.time() - step_started) * 1000)})
                messages.append({'role': 'assistant', 'content': json.dumps(decision, ensure_ascii=False)})
                messages.append({'role': 'user', 'content':
                                 f'TOOL RESULT {tool}: {result_text(result)}\n'
                                 'If this answers the question, call finish(answer) now with a short answer.'})
                tool_errors = 0
                if tool in FINAL_TOOLS or _single_step(task):
                    break
            except (ArgumentError, ToolError) as error:
                steps.append({'n': step_no, 'tool': tool, 'arguments': arguments,
                              'result': {'error': str(error)}, 'ok': False,
                              'ms': round((time.time() - step_started) * 1000)})
                messages.append({'role': 'assistant', 'content': json.dumps(decision, ensure_ascii=False)})
                messages.append({'role': 'user', 'content': f'TOOL ERROR: {error}. Correct the arguments or choose another tool.'})
                tool_errors += 1
                if tool_errors >= 2:
                    failure = f'tool failed twice: {error}'
                    break

        model_answer = answer
        executed = [step for step in steps if step.get('ok') and step.get('tool') != 'finish']

        # A data question answered without touching the tools is exactly the hallucination
        # we care about: ignore the model's prose and build the answer from real data.
        if not executed and model_answer and looks_like_data_question(task):
            failure = failure or 'data question answered without tools'
            model_answer = None

        if not executed and not model_answer:
            used_fallback = True
            fallback_steps, fallback_answer = run_rules(task, self.toolbox)
            offset = len(steps)
            for step in fallback_steps:
                step['n'] += offset
            steps.extend(fallback_steps)
            model_answer = fallback_answer
            if not steps:
                failure = failure or 'no tools were executed'

        # Numbers and ids in the answer come from the tool results, not from the model.
        composed = answer_from(steps, task).strip()
        answer = composed or model_answer or ('No tools were executed.' if failure else '')

        elapsed_ms = round((time.time() - started) * 1000)
        return {
            'task': task,
            'answer': answer,
            'model_answer': model_answer,
            'steps': steps,
            'language': language_of(task),
            'fallback': used_fallback,
            'failure': failure,
            'model': self.model.model,
            'elapsed_ms': elapsed_ms,
        }
