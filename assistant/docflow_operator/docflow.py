"""Thin read-only client for the DocFlow HTTP API.

The operator is a consumer of DocFlow, not a part of it: every call here is a GET.
There is deliberately no method that confirms a document, sends mail or touches the
CRM, so the agent physically cannot move money even if the model asks for it.
"""
from __future__ import annotations

import httpx


class DocFlowError(RuntimeError):
    """DocFlow answered with an error or was unreachable."""


class DocFlowClient:
    def __init__(self, base_url: str, timeout: float = 15.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip('/')
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> 'DocFlowClient':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, path: str, **params) -> object:
        try:
            response = self._client.get(path, params=params or None)
        except httpx.HTTPError as error:  # connection refused, timeout, DNS
            raise DocFlowError(f'DocFlow is unreachable at {self.base_url}: {error}') from error
        if response.status_code == 404:
            raise DocFlowError('not found')
        if response.status_code >= 400:
            raise DocFlowError(f'DocFlow returned {response.status_code} for {path}')
        try:
            return response.json()
        except ValueError as error:
            raise DocFlowError(f'DocFlow returned invalid JSON for {path}') from error

    # --- read-only surface -------------------------------------------------
    def health(self) -> dict:
        return self._get('/api/health')  # type: ignore[return-value]

    def inbox(self, limit: int = 20) -> list[dict]:
        return self._get('/api/inbox', limit=limit)  # type: ignore[return-value]

    def documents(self, limit: int = 50) -> list[dict]:
        return self._get('/api/documents', limit=limit)  # type: ignore[return-value]

    def document(self, doc_id: str) -> dict:
        return self._get(f'/api/documents/{doc_id}')  # type: ignore[return-value]

    def stats(self) -> dict:
        return self._get('/api/stats')  # type: ignore[return-value]
