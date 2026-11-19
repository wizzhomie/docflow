"""Read-only DocFlow client: paths, error translation, and the absence of write methods."""
from __future__ import annotations

import json

import httpx
import pytest

from docflow_operator.docflow import DocFlowClient, DocFlowError

BASE = 'http://docflow.test'


def make_client(handler, timeout: float = 1.0) -> DocFlowClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url=BASE, transport=transport)
    return DocFlowClient(BASE + '/', timeout=timeout, client=http_client)


def json_ok(payload, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)
    return handler


def test_base_url_is_normalised():
    client = make_client(json_ok({}))
    assert client.base_url == BASE
    client.close()


def test_every_read_method_hits_the_expected_get_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = {'ok': True} if request.url.path == '/api/health' else [{'id': 'x'}]
        if request.url.path == '/api/stats':
            payload = {'total': 1}
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    try:
        assert client.health() == {'ok': True}
        assert client.inbox(limit=7) == [{'id': 'x'}]
        assert client.documents(limit=9) == [{'id': 'x'}]
        assert client.document('abc123') == [{'id': 'x'}]
        assert client.stats() == {'total': 1}
    finally:
        client.close()

    paths = [(request.method, request.url.path) for request in seen]
    assert paths == [
        ('GET', '/api/health'),
        ('GET', '/api/inbox'),
        ('GET', '/api/documents'),
        ('GET', '/api/documents/abc123'),
        ('GET', '/api/stats'),
    ]
    assert seen[1].url.params['limit'] == '7'
    assert seen[2].url.params['limit'] == '9'


def test_no_write_method_exists():
    # The client must physically be unable to confirm, pay or send anything.
    for forbidden in ('confirm', 'post', 'send', 'pay', 'update', 'delete', 'create'):
        assert not hasattr(DocFlowClient, forbidden), forbidden


def test_not_found_becomes_a_tool_friendly_error():
    client = make_client(lambda request: httpx.Response(404, json={'detail': 'nope'}))
    with pytest.raises(DocFlowError, match='not found'):
        client.document('missing')
    client.close()


def test_server_error_includes_status_and_path():
    client = make_client(lambda request: httpx.Response(500))
    with pytest.raises(DocFlowError, match=r'returned 500 for /api/health'):
        client.health()
    client.close()


def test_invalid_json_is_reported():
    client = make_client(lambda request: httpx.Response(200, text='<html>oops</html>'))
    with pytest.raises(DocFlowError, match='invalid JSON'):
        client.inbox()
    client.close()


def test_connection_failure_says_where_docflow_should_be():
    def handler(request):
        raise httpx.ConnectError('connection refused', request=request)

    client = make_client(handler)
    with pytest.raises(DocFlowError) as error:
        client.health()
    assert 'unreachable' in str(error.value)
    assert BASE in str(error.value)
    client.close()


def test_timeout_is_translated_too():
    def handler(request):
        raise httpx.ReadTimeout('slow', request=request)

    client = make_client(handler)
    with pytest.raises(DocFlowError, match='unreachable'):
        client.stats()
    client.close()


def test_client_is_a_context_manager_and_closes_http():
    transport = httpx.MockTransport(json_ok({'ok': True}))
    http_client = httpx.Client(base_url=BASE, transport=transport)
    with DocFlowClient(BASE, client=http_client) as client:
        assert client.health() == {'ok': True}
    assert http_client.is_closed


def test_close_is_idempotent():
    client = make_client(json_ok({}))
    client.close()
    client.close()


def test_query_parameters_are_absent_when_not_requested():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({'ok': True}).encode(),
                              headers={'content-type': 'application/json'})

    client = make_client(handler)
    client.health()
    client.close()
    assert not seen[0].url.query
