"""FastAPI server: the assistant chat API, the widget script and a demo page."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent, ModelClient
from .config import BASE_DIR, get_settings
from .docflow import DocFlowClient, DocFlowError
from .tools import Toolbox

TEMPLATES = BASE_DIR / 'templates'
STATIC = BASE_DIR / 'static'
app = FastAPI(title='DocFlow Operator', version='0.2.0')
settings = get_settings()

# The widget is loaded by another origin (the DocFlow pages), so the API must allow it.
# The API is read-only and has no credentials; keep this list tight where possible.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(',') if origin.strip()],
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Content-Type'],
)

# llama-server runs one slot; serialise requests instead of thrashing its KV cache.
_chat_lock = threading.Lock()


def _warm_model(client_factory=None) -> None:
    """Pre-compute the system prompt in the model's KV cache.

    The prompt is ~650 tokens; on two CPU cores that is ~40 s of prompt processing. Doing
    it once at startup means the first real question is as fast as the rest.
    """
    from .agent import system_prompt

    payload = {
        'model': settings.model_name,
        'messages': [{'role': 'system', 'content': system_prompt()},
                     {'role': 'user', 'content': 'hi'}],
        'max_tokens': 1,
        'temperature': 0,
    }
    factory = client_factory or (lambda: httpx.Client(base_url=settings.model_url, timeout=300))
    for _ in range(20):
        try:
            with factory() as client:
                client.post('/v1/chat/completions', json=payload)
            return
        except httpx.HTTPError:
            time.sleep(3)  # the model may still be loading; try for about a minute


def _start_warm_thread() -> None:  # pragma: no cover - daemon process detail
    threading.Thread(target=_warm_model, daemon=True).start()


# Tests and tooling import the app in-process and must not depend on a model being up.
if os.environ.get('OPERATOR_WARM_MODEL', '1') != '0':  # pragma: no branch
    _start_warm_thread()  # pragma: no cover


class ChatMessage(BaseModel):
    role: str
    content: str = ''


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


def _run_agent(task: str, history: list[dict]) -> dict:
    settings.ensure_dirs()
    docflow = DocFlowClient(settings.docflow_url, settings.docflow_timeout)
    model = ModelClient(settings.model_url, settings.model_name, settings.model_timeout,
                        settings.temperature)
    toolbox = Toolbox(docflow, settings.drafts_dir)
    started = time.time()
    try:
        with _chat_lock:
            result = Agent(model, toolbox, settings.max_steps, settings.time_budget).run(task, history)
    except Exception as error:  # noqa: BLE001 - the widget should show a message, not a stack
        raise HTTPException(500, f'assistant failed: {error}') from error
    finally:
        model.close()
        docflow.close()
    result['total_ms'] = round((time.time() - started) * 1000)
    _save_trace(result)
    return result


@app.get('/')
def index() -> FileResponse:
    return FileResponse(TEMPLATES / 'assistant.html')


@app.get('/widget.js')
def widget() -> FileResponse:
    return FileResponse(STATIC / 'widget.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-store'})


@app.get('/api/config')
def config() -> dict:
    return {
        'model': settings.model_name,
        'model_url': settings.model_url,
        'docflow_url': settings.docflow_url,
        'max_steps': settings.max_steps,
        'drafts_dir': str(settings.drafts_dir),
    }


@app.get('/api/health')
def health() -> dict:
    docflow_ok, docflow_detail = True, 'ok'
    try:
        DocFlowClient(settings.docflow_url, timeout=5).health()
    except DocFlowError as error:
        docflow_ok, docflow_detail = False, str(error)
    model = ModelClient(settings.model_url, settings.model_name, timeout=5).health()
    return {
        'ok': docflow_ok and model['ok'],
        'docflow': {'ok': docflow_ok, 'detail': docflow_detail},
        'model': model,
    }


@app.post('/api/chat')
def chat(request: ChatRequest) -> dict:
    messages = [message.model_dump() for message in request.messages]
    if not messages or messages[-1]['role'] != 'user':
        raise HTTPException(400, 'the last message must be from the user')
    task = messages[-1]['content'].strip()
    if not task:
        raise HTTPException(400, 'empty question')
    history = messages[:-1]
    result = _run_agent(task, history)
    return result


@app.post('/api/ask')
def ask(request: ChatRequest) -> dict:
    """One-shot request without history; kept for the evaluation runner."""
    task = request.messages[-1].content.strip()
    if not task:
        raise HTTPException(400, 'empty task')
    return _run_agent(task, [])


def _save_trace(result: dict) -> None:
    """Append one line per run; traces are local runtime data, not source."""
    try:
        settings.traces_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.traces_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(result, ensure_ascii=False, default=str) + '\n')
    except OSError:
        pass


@app.get('/api/traces')
def traces(limit: int = 20) -> list[dict]:
    path: Path = settings.traces_path
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()[-limit:]
    rows = []
    for line in reversed(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
