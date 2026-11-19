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
    # Origins allowed to call the chat API (the widget runs on the DocFlow pages).
    cors_origins: str = field(default_factory=lambda: _env('OPERATOR_CORS_ORIGINS', '*'))
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
