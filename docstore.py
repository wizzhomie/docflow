"""The standalone file store: pending snapshots and the confirmed history.

This is the demo that runs without PostgreSQL. A signed-in person never reads
this module — their documents live in rows — but every report has a file-store
path so the demo keeps working on a machine with no database at all.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

import routing
import settings

_ISSUES_CACHE: dict[str, tuple[float, list[str]]] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_confirmed() -> list[dict]:
    if not settings.CONFIRMED.exists():
        return []
    rows = []
    for line in settings.CONFIRMED.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _meta_path(doc_id: str) -> Path:
    if not settings.DEMO_ID_RE.fullmatch(doc_id):
        raise HTTPException(404, 'Document not found')
    return settings.PENDING / f'{doc_id}.json'


def _review_seconds(row: dict) -> float | None:
    """Seconds one person actually spent on the document.

    The stored value wins, including when it is explicitly empty: a document that
    was never opened has no measured review time, and deriving one from the upload
    timestamp would count the waiting as work. Older file records predate the
    stored field, so those are still derived from their two timestamps.
    """
    if 'review_seconds' in row:
        stored = row['review_seconds']
        if isinstance(stored, (int, float)):
            return max(0.0, float(stored))
        return None
    started = _parse_ts(row.get('uploaded_at'))
    finished = _parse_ts(row.get('confirmed_at'))
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


def _issues_for(doc_id: str | None) -> list[str]:
    """Validation issues recorded before confirms started storing them.

    Legacy confirmations have no `issues` field, so the pending snapshot of the
    same document is consulted. The cache is keyed by file mtime so an updated
    snapshot is never reported from stale bytes.
    """
    if not doc_id or not settings.DEMO_ID_RE.fullmatch(doc_id):
        return []
    path = _meta_path(doc_id)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _ISSUES_CACHE.get(doc_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        meta = json.loads(path.read_text(encoding='utf-8'))
        issues = list((meta.get('parsed') or {}).get('issues') or [])
    except (OSError, ValueError):
        issues = []
    _ISSUES_CACHE[doc_id] = (mtime, issues)
    return issues


def _row_issues(row: dict) -> list[str]:
    issues = row.get('issues')
    if issues is None:
        return _issues_for(row.get('id'))
    return list(issues)


def _pending_documents(limit: int = 20) -> list[dict]:
    """Uploaded documents that are still waiting for a decision.

    Confirming does not delete the pending snapshot, so the history is what
    separates "waiting for review" from "already saved".
    """
    confirmed_ids = {row.get('id') for row in _read_confirmed()}
    items = []
    for path in settings.PENDING.glob('*.json'):
        doc_id = path.stem
        if doc_id in confirmed_ids:
            continue
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        parsed = meta.get('parsed') or {}
        items.append(
            {
                'id': doc_id,
                'filename': meta.get('filename'),
                'uploaded_at': meta.get('uploaded_at'),
                'suffix': meta.get('suffix'),
                'source': meta.get('source', 'upload'),
                # The demo session that uploaded it: what makes the queue "yours"
                # without a sign-in. Not a credential — the visitor already has it.
                'session': meta.get('session'),
                'issues': list(parsed.get('issues') or []),
                'confidence': parsed.get('confidence'),
                'doc_type': parsed.get('type_label'),
                'route': routing.route(
                    parsed.get('type'),
                    {k: v.get('value') for k, v in (parsed.get('fields') or {}).items()},
                ),
                'fields': {k: v.get('value') for k, v in (parsed.get('fields') or {}).items()},
            }
        )
    items.sort(key=lambda item: item.get('uploaded_at') or '', reverse=True)
    return items[:limit]


def _file_counts() -> dict[str, int]:
    """What the file store holds: how many are waiting and how many were confirmed."""
    confirmed = _read_confirmed()
    ids = {row.get('id') for row in confirmed}
    waiting = sum(1 for path in settings.PENDING.glob('*.json') if path.stem not in ids)
    return {'waiting': waiting, 'confirmed': len(ids)}
