"""Checks for the log format and the request id.

The log is a file an operator reads with `tail` and a machine filters with `jq`:
both need one JSON object per line. These checks keep the shape stable, because a
log format that drifts silently is worse than no log at all.

Run: python3 tests/test_observability.py
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import observability  # noqa: E402


def _record(**extra):
    record = logging.LogRecord(
        name='docflow.access',
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='request',
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_the_formatter_writes_one_json_object():
    line = observability.JsonFormatter().format(_record(request_id='abc123', status=200))
    payload = json.loads(line)
    assert payload['message'] == 'request'
    assert payload['request_id'] == 'abc123'
    assert payload['status'] == 200
    assert payload['ts'].endswith('Z')
    assert '\n' not in line


def test_the_formatter_keeps_a_traceback_as_text():
    try:
        raise RuntimeError('boom')
    except RuntimeError:
        import sys as _sys

        record = _record()
        record.exc_info = _sys.exc_info()
    payload = json.loads(observability.JsonFormatter().format(record))
    assert 'RuntimeError: boom' in payload['exception']


def test_a_request_carries_its_id_back():
    client = TestClient(app.app)
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.headers.get('X-Request-Id'), 'the response lost the request id'


def test_the_demo_error_is_a_real_server_error():
    client = TestClient(app.app, raise_server_exceptions=False)
    response = client.get('/api/demo/error')
    assert response.status_code == 500


def test_the_metrics_endpoint_answers_with_and_without_a_database():
    client = TestClient(app.app)
    response = client.get('/api/metrics')
    assert response.status_code == 200
    data = response.json()
    for key in ('process', 'pool', 'queue', 'tenants', 'files'):
        assert key in data
    text = client.get('/api/metrics', params={'format': 'prometheus'})
    assert text.status_code == 200
    assert text.headers['content-type'].startswith('text/plain')
    assert 'docflow_queue_oldest_seconds' in text.text
    assert 'docflow_files_confirmed' in text.text


def test_the_metrics_answer_when_the_database_is_gone():
    """A metrics endpoint that fails because a dependency is missing is a blind
    spot exactly when someone needs it. Without a database the answer is zeros."""
    import db
    import metrics

    original = (db.DSN, db.POOL_MIN, db.POOL_MAX)
    db.close_pool()
    try:
        db.DSN = 'dbname=docflow_absent'
        db.POOL_MIN = db.POOL_MAX = 1
        snapshot = metrics.snapshot({'waiting': 0, 'confirmed': 0})
    finally:
        db.DSN, db.POOL_MIN, db.POOL_MAX = original
        db.close_pool()
    assert snapshot['queue']['queued'] == 0
    assert snapshot['queue']['oldest_seconds'] == 0
    assert snapshot['tenants'] == []
    assert snapshot['files'] == {'waiting': 0, 'confirmed': 0}


def test_status_reports_where_the_log_lives():
    status = observability.status()
    assert status['log_file'].endswith('app.jsonl')
    assert isinstance(status['sentry'], bool)


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('observability checks passed')


if __name__ == '__main__':
    main()
