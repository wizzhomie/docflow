"""Configuration for the operator.

Everything that differs between a laptop demo and the container lives here, so the
code never carries hardcoded hosts or paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    # DocFlow is the only external dependency and is used read-only.
    docflow_url: str = field(default_factory=lambda: _env('DOCFLOW_URL', 'http://127.0.0.1:8765').rstrip('/'))
    docflow_timeout: float = field(default_factory=lambda: float(_env('DOCFLOW_TIMEOUT', '15')))

    # llama.cpp server with an OpenAI-compatible API.
    model_url: str = field(default_factory=lambda: _env('OPERATOR_MODEL_URL', 'http://127.0.0.1:8080').rstrip('/'))
    model_name: str = field(default_factory=lambda: _env('OPERATOR_MODEL_NAME', 'qwen2.5-1.5b-instruct-q4_k_m'))
    model_timeout: float = field(default_factory=lambda: float(_env('OPERATOR_MODEL_TIMEOUT', '90')))
    max_steps: int = field(default_factory=lambda: int(_env('OPERATOR_MAX_STEPS', '4')))
    time_budget: float = field(default_factory=lambda: float(_env('OPERATOR_TIME_BUDGET', '120')))
    temperature: float = field(default_factory=lambda: float(_env('OPERATOR_TEMPERATURE', '0')))

    port: int = field(default_factory=lambda: int(_env('OPERATOR_PORT', '8769')))
    # Extra origins allowed to call the chat API. Empty by default: the widget is served
    # by this same service and talks to its own origin, so the API needs no CORS at all.
    # Set it only when the widget is embedded in a page on another origin.
    cors_origins: str = field(default_factory=lambda: _env('OPERATOR_CORS_ORIGINS', ''))

    # One visitor must not be able to occupy the single model slot for long.
    chat_rate_limit: int = field(default_factory=lambda: int(_env('OPERATOR_CHAT_RATE_LIMIT', '6')))
    chat_rate_window: float = field(default_factory=lambda: float(_env('OPERATOR_CHAT_RATE_WINDOW', '60')))
    chat_queue_timeout: float = field(default_factory=lambda: float(_env('OPERATOR_CHAT_QUEUE_TIMEOUT', '20')))

    # Traces hold every question asked of the public demo; off unless explicitly turned on.
    traces_public: bool = field(default_factory=lambda: _env('OPERATOR_TRACES_PUBLIC', '0') in {'1', 'true', 'yes'})
    data_dir: Path = field(default_factory=lambda: Path(_env('OPERATOR_DATA_DIR', str(BASE_DIR / 'data'))))

    @property
    def drafts_dir(self) -> Path:
        return self.data_dir / 'drafts'

    @property
    def traces_path(self) -> Path:
        return self.data_dir / 'traces.jsonl'

    def ensure_dirs(self) -> None:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
