"""Configuration tests: every setting comes from the environment, with safe defaults."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow_operator import config
from docflow_operator.config import BASE_DIR, Settings, get_settings

OPERATOR_ENV = [
    'DOCFLOW_URL', 'DOCFLOW_TIMEOUT', 'OPERATOR_MODEL_URL', 'OPERATOR_MODEL_NAME',
    'OPERATOR_MODEL_TIMEOUT', 'OPERATOR_MAX_STEPS', 'OPERATOR_TIME_BUDGET',
    'OPERATOR_TEMPERATURE', 'OPERATOR_PORT', 'OPERATOR_CORS_ORIGINS', 'OPERATOR_DATA_DIR',
    'OPERATOR_CHAT_RATE_LIMIT', 'OPERATOR_CHAT_RATE_WINDOW', 'OPERATOR_CHAT_QUEUE_TIMEOUT',
    'OPERATOR_TRACES_PUBLIC', 'OPERATOR_DEMO_URL',
]


@pytest.fixture()
def clean_env(monkeypatch):
    for name in OPERATOR_ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_a_local_demo(clean_env):
    settings = Settings()
    assert settings.docflow_url == 'http://127.0.0.1:8765'
    assert settings.docflow_timeout == 15.0
    assert settings.model_url == 'http://127.0.0.1:8080'
    assert settings.model_name == 'qwen2.5-1.5b-instruct-q4_k_m'
    assert settings.model_timeout == 90.0
    assert settings.max_steps == 4
    assert settings.time_budget == 120.0
    assert settings.temperature == 0.0
    assert settings.port == 8769
    assert settings.cors_origins == ''
    assert settings.demo_url == ''
    assert settings.chat_rate_limit == 6
    assert settings.chat_rate_window == 60.0
    assert settings.chat_queue_timeout == 20.0
    assert settings.traces_public is False
    assert settings.data_dir == BASE_DIR / 'data'


def test_environment_overrides_every_knob(monkeypatch):
    monkeypatch.setenv('DOCFLOW_URL', 'http://docflow.internal:9000')
    monkeypatch.setenv('DOCFLOW_TIMEOUT', '3.5')
    monkeypatch.setenv('OPERATOR_MODEL_URL', 'http://model.internal:8000')
    monkeypatch.setenv('OPERATOR_MODEL_NAME', 'qwen3-0.6b')
    monkeypatch.setenv('OPERATOR_MODEL_TIMEOUT', '30')
    monkeypatch.setenv('OPERATOR_MAX_STEPS', '1')
    monkeypatch.setenv('OPERATOR_TIME_BUDGET', '12.5')
    monkeypatch.setenv('OPERATOR_TEMPERATURE', '0.2')
    monkeypatch.setenv('OPERATOR_PORT', '9999')
    monkeypatch.setenv('OPERATOR_CORS_ORIGINS', 'https://docflow.test, http://localhost:5000')
    monkeypatch.setenv('OPERATOR_CHAT_RATE_LIMIT', '3')
    monkeypatch.setenv('OPERATOR_CHAT_RATE_WINDOW', '30')
    monkeypatch.setenv('OPERATOR_CHAT_QUEUE_TIMEOUT', '5')
    monkeypatch.setenv('OPERATOR_TRACES_PUBLIC', '1')
    monkeypatch.setenv('OPERATOR_DEMO_URL', 'https://demo.test:8443/')
    monkeypatch.setenv('OPERATOR_DATA_DIR', '/tmp/operator-data')
    settings = Settings()
    assert settings.docflow_url == 'http://docflow.internal:9000'
    assert settings.docflow_timeout == 3.5
    assert settings.model_url == 'http://model.internal:8000'
    assert settings.model_name == 'qwen3-0.6b'
    assert settings.model_timeout == 30.0
    assert settings.max_steps == 1
    assert settings.time_budget == 12.5
    assert settings.temperature == 0.2
    assert settings.port == 9999
    assert settings.cors_origins == 'https://docflow.test, http://localhost:5000'
    assert settings.chat_rate_limit == 3
    assert settings.chat_rate_window == 30.0
    assert settings.chat_queue_timeout == 5.0
    assert settings.traces_public is True
    assert settings.demo_url == 'https://demo.test:8443/'
    assert settings.data_dir == Path('/tmp/operator-data')


def test_urls_never_keep_a_trailing_slash(monkeypatch):
    monkeypatch.setenv('DOCFLOW_URL', 'http://docflow.test/')
    monkeypatch.setenv('OPERATOR_MODEL_URL', 'http://model.test///')
    settings = Settings()
    assert settings.docflow_url == 'http://docflow.test'
    assert settings.model_url == 'http://model.test'


def test_blank_environment_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv('DOCFLOW_URL', '   ')
    monkeypatch.setenv('OPERATOR_PORT', '')
    settings = Settings()
    assert settings.docflow_url == 'http://127.0.0.1:8765'
    assert settings.port == 8769


def test_invalid_numbers_fail_loudly(monkeypatch):
    monkeypatch.setenv('OPERATOR_MAX_STEPS', 'many')
    with pytest.raises(ValueError):
        Settings()


def test_derived_paths_below_the_data_directory(monkeypatch):
    monkeypatch.setenv('OPERATOR_DATA_DIR', '/srv/operator')
    settings = Settings()
    assert settings.drafts_dir == Path('/srv/operator/drafts')
    assert settings.traces_path == Path('/srv/operator/traces.jsonl')


def test_ensure_dirs_creates_only_the_drafts_directory(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data')
    settings.ensure_dirs()
    assert (tmp_path / 'data' / 'drafts').is_dir()
    # Traces are appended lazily by the server; building settings must not touch the disk.
    assert not (tmp_path / 'data' / 'traces.jsonl').exists()


def test_get_settings_returns_a_fresh_value(clean_env):
    assert get_settings() == Settings()
    assert isinstance(get_settings(), Settings)


def test_base_dir_is_the_repository_root():
    assert (BASE_DIR / 'requirements.txt').is_file()
    assert (BASE_DIR / 'docflow_operator' / 'server.py').is_file()


def test_config_module_exposes_base_dir():
    assert config.BASE_DIR == BASE_DIR
