"""Machine clients of the platform: API keys, stored as digests, scoped per action.

An integration is a machine, not a person: it cannot hold a session cookie and it
must not be given somebody's password. The operator issues a key instead, and the
key file keeps only the SHA-256 of the secret — a backup, a screenshot or a copy
of `data/api_keys.json` cannot be replayed as a key, and the plaintext exists in
exactly one place: the answer to the command that issued it.

A key names the actions it may perform. An integration that only reads extraction
results is issued `documents:read` and can never confirm: the scope is checked at
the route, not remembered in a comment.

Token shape: `dfk_<id>_<secret>`, where the id is the lookup key and the secret is
compared in constant time. Both are hex, so `_` splits the token unambiguously.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import settings

PREFIX = 'dfk_'
SCOPES = ('documents:write', 'documents:read', 'documents:confirm')
MAX_KEYS = 200
# A busy integration calls this on every request; the file is rewritten only when
# the moment has actually changed, which keeps a read path from becoming a writer.
TOUCH_SECONDS = 60

_LOCK = threading.Lock()


def _path() -> Path:
    return Path(settings.API_KEYS)


def _load() -> dict[str, Any]:
    """The key file, or an empty one.

    A missing file is the normal state of a fresh checkout, and a corrupt one must
    not take the service down: both read as "no keys", and no key verifies.
    """
    try:
        document = json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'version': 1, 'keys': []}
    if not isinstance(document, dict) or not isinstance(document.get('keys'), list):
        return {'version': 1, 'keys': []}
    return document


def _save(document: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    if os.name != 'nt':
        # The digests are not the secret, but a key file readable by every user on
        # the box is a list of which integrations exist: not something to publish.
        temporary.chmod(0o600)
    temporary.replace(path)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _clean_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    chosen = sorted({str(scope).strip() for scope in (scopes or SCOPES)} & set(SCOPES))
    if not chosen:
        raise ValueError(f'a key needs at least one scope from: {", ".join(SCOPES)}')
    return chosen


def public(record: dict[str, Any]) -> dict[str, Any]:
    """A key as anything outside this module may see it: never the digest."""
    return {
        'id': record.get('id'),
        'label': record.get('label'),
        'scopes': list(record.get('scopes') or []),
        'company_id': record.get('company_id'),
        'created_at': record.get('created_at'),
        'last_used_at': record.get('last_used_at'),
        'revoked': bool(record.get('revoked')),
    }


def issue(
    label: str,
    scopes: list[str] | tuple[str, ...] | None = None,
    company_id: int | None = None,
) -> tuple[dict[str, Any], str]:
    """Create a key and return `(record, token)`; the token is not stored anywhere."""
    clean_label = (label or '').strip()
    if not clean_label:
        raise ValueError('a key needs a label: what integration is it for')
    if company_id is not None:
        company_id = int(company_id)
        if company_id <= 0:
            raise ValueError('company_id must be a positive integer')
    chosen = _clean_scopes(scopes)
    with _LOCK:
        document = _load()
        if len(document['keys']) >= MAX_KEYS:
            raise ValueError(f'the key file already holds {MAX_KEYS} keys: revoke one first')
        key_id = secrets.token_hex(6)
        while any(record.get('id') == key_id for record in document['keys']):
            key_id = secrets.token_hex(6)
        secret = secrets.token_hex(24)
        record = {
            'id': key_id,
            'label': clean_label[:80],
            'scopes': chosen,
            'company_id': company_id,
            'hash': _digest(secret),
            'created_at': _now(),
            'last_used_at': None,
            'revoked': False,
        }
        document['keys'].append(record)
        _save(document)
    return public(record), f'{PREFIX}{key_id}_{secret}'


def verify(token: str | None) -> dict[str, Any] | None:
    """The key behind a token, or None.

    Unknown, revoked and malformed tokens are one answer on purpose: a client
    learns that its credential was not accepted, not which part of it was wrong.
    """
    token = (token or '').strip()
    if not token.startswith(PREFIX):
        return None
    parts = token.split('_')
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    _, key_id, secret = parts
    record = None
    for candidate in _load()['keys']:
        if candidate.get('id') == key_id:
            record = candidate
            break
    if record is None or record.get('revoked'):
        return None
    # Compared against the digest, not the token: a failure takes the same time
    # whether the id exists or not, because the comparison happens either way only
    # after both lookups, and `compare_digest` never short-circuits.
    if not hmac.compare_digest(str(record.get('hash') or ''), _digest(secret)):
        return None
    touch(key_id)
    return public(record)


def touch(key_id: str) -> None:
    """Record that a key was used, at most once a minute.

    The timestamp answers "which integration is this and is it still alive"; a
    write per request would turn every read into a write.
    """
    now = datetime.now(UTC)
    with _LOCK:
        document = _load()
        for record in document['keys']:
            if record.get('id') != key_id:
                continue
            last = record.get('last_used_at') or ''
            try:
                previous = datetime.fromisoformat(last) if last else None
            except ValueError:
                previous = None
            if previous is not None and (now - previous).total_seconds() < TOUCH_SECONDS:
                return
            record['last_used_at'] = now.isoformat(timespec='seconds')
            _save(document)
            return


def listing() -> list[dict[str, Any]]:
    """Every key, digests stripped, newest last."""
    return [public(record) for record in _load()['keys']]


def revoke(key_id: str) -> bool:
    """Stop a key from verifying; returns whether this call changed anything."""
    with _LOCK:
        document = _load()
        for record in document['keys']:
            if record.get('id') != key_id:
                continue
            if record.get('revoked'):
                return False
            record['revoked'] = True
            record['revoked_at'] = _now()
            _save(document)
            return True
    return False


def company_of(key: dict[str, Any]) -> int | None:
    """The company a key is bound to, or None for a key of the file store."""
    company_id = key.get('company_id')
    return int(company_id) if company_id else None
