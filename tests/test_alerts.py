"""The mail channel of the healthcheck: optional, debounced, never a gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'deploy' / 'healthcheck' / 'notify_mail.py'
SPEC = importlib.util.spec_from_file_location('notify_mail', PATH)
notify_mail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify_mail)


def no_env(monkeypatch):
    for key in notify_mail.KEYS:
        monkeypatch.delenv(f'DOCFLOW_SMTP_{key}', raising=False)


def test_without_settings_the_mailer_is_a_no_op(monkeypatch):
    no_env(monkeypatch)

    def explode(*args, **kwargs):
        raise AssertionError('SMTP must not be touched when it is not configured')

    monkeypatch.setattr(notify_mail.smtplib, 'SMTP', explode)
    assert notify_mail.main(['notify_mail.py', 'fail', 'database is down']) == 0


def test_the_message_names_the_problem_and_where_it_happened(monkeypatch):
    no_env(monkeypatch)
    monkeypatch.setenv('DOCFLOW_SMTP_TO', 'owner@example.com')
    values = notify_mail.settings()
    message = notify_mail.build('fail', 'docflow-public: HTTP 502', values)
    assert 'not answering' in message['Subject']
    body = message.get_content()
    assert 'docflow-public: HTTP 502' in body
    assert 'Host:' in body


def test_a_recovery_has_its_own_subject(monkeypatch):
    no_env(monkeypatch)
    monkeypatch.setenv('DOCFLOW_SMTP_TO', 'owner@example.com')
    message = notify_mail.build('ok', 'the demo answers again', notify_mail.settings())
    assert 'answering again' in message['Subject']


def test_a_refused_relay_is_reported_not_raised(monkeypatch):
    no_env(monkeypatch)
    monkeypatch.setenv('DOCFLOW_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('DOCFLOW_SMTP_TO', 'owner@example.com')
    values = notify_mail.settings()
    monkeypatch.setattr(notify_mail.smtplib, 'SMTP',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('refused')))
    message = notify_mail.build('fail', 'something broke', values)
    assert notify_mail.send(message, values) is False
