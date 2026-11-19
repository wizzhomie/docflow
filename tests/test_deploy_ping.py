"""The dead-man switch: what is pinged, retried and left alone.

The switch alerts when pings stop, so the URL and the failed-run marker matter
more than the happy path. These checks use a fake opener; no network involved.

Run: python3 -m pytest tests/test_ping.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'deploy' / 'healthcheck'))

import ping  # noqa: E402


class Answer:
    def __init__(self):
        self.read_called = False

    def read(self):
        self.read_called = True
        return b'ok'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_healthy_run_pings_the_base_url():
    assert ping.endpoint('https://hc-ping.com/uuid/', True) == 'https://hc-ping.com/uuid'


def test_a_failed_run_pings_the_fail_path():
    assert ping.endpoint('https://hc-ping.com/uuid', False) == 'https://hc-ping.com/uuid/fail'


def test_the_switches_answer_is_read():
    answer = Answer()
    assert ping.ping('https://hc-ping.com/uuid', opener=lambda url, timeout: answer) is True
    assert answer.read_called


def test_one_dropped_connection_is_retried(monkeypatch):
    monkeypatch.setattr(ping.time, 'sleep', lambda seconds: None)
    attempts = []

    def opener(url, timeout):
        attempts.append(url)
        if len(attempts) == 1:
            raise OSError('connection reset')
        return Answer()

    assert ping.ping('https://hc-ping.com/uuid', opener=opener) is True
    assert len(attempts) == 2


def test_two_failures_are_reported_not_hidden(monkeypatch):
    monkeypatch.setattr(ping.time, 'sleep', lambda seconds: None)

    def opener(url, timeout):
        raise OSError('network down')

    assert ping.ping('https://hc-ping.com/uuid', opener=opener) is False


def test_without_a_url_nothing_is_called(monkeypatch):
    monkeypatch.delenv('DOCFLOW_PING_URL', raising=False)
    calls = []
    monkeypatch.setattr(ping, 'ping', lambda url, **kwargs: calls.append(url) or True)
    assert ping.main(['success']) == 0
    assert calls == []


def test_a_failed_run_uses_the_fail_url(monkeypatch):
    monkeypatch.setenv('DOCFLOW_PING_URL', 'https://hc-ping.com/uuid/')
    seen = {}

    def fake_ping(url, **kwargs):
        seen['url'] = url
        return True

    monkeypatch.setattr(ping, 'ping', fake_ping)
    assert ping.main(['fail']) == 0
    assert seen['url'] == 'https://hc-ping.com/uuid/fail'


def test_a_switch_that_does_not_answer_is_visible(monkeypatch):
    monkeypatch.setenv('DOCFLOW_PING_URL', 'https://hc-ping.com/uuid')
    monkeypatch.setattr(ping, 'ping', lambda *a, **k: False)
    assert ping.main(['success']) == 1
