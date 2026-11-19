"""One browser, one demo session: what a visitor may see of the file store.

The file-based demo has no sign-in and one shared directory. Without a boundary, a
visitor reads the operator's uploads, the test documents and the counters of both,
which is not a demo of the product — it is our working desk. So each browser gets
an opaque id in a cookie; uploads made in that browser carry the id, and the demo
lists the labelled synthetic showcase plus the visitor's own documents, never
someone else's.
"""

from __future__ import annotations

import secrets

COOKIE = 'docflow_demo'
MAX_AGE = 30 * 24 * 3600  # a month: long enough for a visitor to come back
SOURCE = 'synthetic'


def session_id(request) -> str:
    """The visitor's demo session, empty when this browser has none yet."""
    return (request.cookies.get(COOKIE) or '').strip()


def new_session() -> str:
    return secrets.token_hex(8)


def visible(record: dict, session: str) -> bool:
    """Whether a visitor may see this record: the showcase, or their own upload."""
    if record.get('source') == SOURCE:
        return True
    return bool(session) and record.get('session') == session
