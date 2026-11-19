"""FastAPI server: the assistant chat API, the widget script and a demo page."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .agent import Agent, ModelClient
from .config import BASE_DIR, get_settings
from .docflow import DocFlowClient, DocFlowError
from .limits import SlidingWindow
from .tools import Toolbox

TEMPLATES = BASE_DIR / 'templates'
STATIC = BASE_DIR / 'static'
app = FastAPI(title='DocFlow Operator', version='0.2.0')
settings = get_settings()

# The widget is served by this service and calls its own origin, so no cross-origin
# access is needed. OPERATOR_CORS_ORIGINS is the explicit opt-in for embedding the
# widget in a page on another domain; without it the browser cannot call the API.
_cors_origins = [origin.strip() for origin in settings.cors_origins.split(',') if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=['GET', 'POST', 'OPTIONS'],
        allow_headers=['Content-Type'],
    )

# llama-server runs one slot; serialise requests instead of thrashing its KV cache.
_chat_lock = threading.Lock()

# A public demo on two CPU cores: one visitor must not keep the slot for long.
_chat_limiter = SlidingWindow(settings.chat_rate_limit, settings.chat_rate_window)


def _client_key(request: Request) -> str:
    """The address to count against: the proxy's client when it says so, else the peer."""
    forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else 'unknown'


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {'detail': 'too many questions from this address; try again in a moment'},
        status_code=429,
        headers={'Retry-After': str(max(1, retry_after))},
    )


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
    queue_timeout = float(getattr(settings, 'chat_queue_timeout', 20))
    if not _chat_lock.acquire(timeout=max(0.0, queue_timeout)):
        model.close()
        docflow.close()
        raise HTTPException(429, 'the assistant is busy; try again in a moment')
    try:
        result = Agent(model, toolbox, settings.max_steps, settings.time_budget).run(task, history)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - the widget should show a message, not a stack
        raise HTTPException(500, f'assistant failed: {error}') from error
    finally:
        _chat_lock.release()
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
    """What the demo page may show to a visitor; internal addresses stay inside."""
    return {
        'model': settings.model_name,
        'max_steps': settings.max_steps,
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
def chat(message: ChatRequest, request: Request):
    messages = [item.model_dump() for item in message.messages]
    if not messages or messages[-1]['role'] != 'user':
        raise HTTPException(400, 'the last message must be from the user')
    task = messages[-1]['content'].strip()
    if not task:
        raise HTTPException(400, 'empty question')
    key = _client_key(request)
    if not _chat_limiter.allow(key):
        return _too_many(_chat_limiter.retry_after(key))
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
    """Every question asked of the public demo. Off by default: it is not a public feed."""
    if not getattr(settings, 'traces_public', False):
        raise HTTPException(404, 'traces are off')
    limit = max(1, min(limit, 20))
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
