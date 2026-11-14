"""Mailbox intake: IMAP transport, MIME attachment parsing and poll state.

The fragile, network-facing part of the e-mail source lives here; the
application layer decides what to do with each attachment (ingest it through the
same pipeline as an upload), so every rule about documents stays in one place.

Two properties matter more than convenience:

- a message is marked ``\\Seen`` only after the callback accepted all of its
  attachments, so a failed ingest is retried on the next poll instead of being
  lost silently;
- the polling state (seen Message-IDs, counters, last error) is persisted, so a
  restart does not fetch the same mail twice.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import time
from email.header import decode_header, make_header
from pathlib import Path

BASE = Path(__file__).parent
STATE = BASE / 'data' / 'mailbox_state.json'
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
SEEN_LIMIT = 1000

_EMPTY_STATE = {
    'seen': {},
    'messages': 0,
    'attachments': 0,
    'last_check': None,
    'last_error': None,
    'last_message': None,
}


def settings() -> dict:
    """IMAP settings from the environment; empty values mean "not configured"."""
    env = os.environ
    ssl_raw = (env.get('DOCFLOW_IMAP_SSL', '1') or '1').strip().lower()
    try:
        port = int(env.get('DOCFLOW_IMAP_PORT', '993') or 993)
    except ValueError:
        port = 993
    try:
        interval = int(env.get('DOCFLOW_IMAP_INTERVAL', '30') or 30)
    except ValueError:
        interval = 30
    return {
        'host': (env.get('DOCFLOW_IMAP_HOST') or '').strip(),
        'port': port,
        'user': (env.get('DOCFLOW_IMAP_USER') or '').strip(),
        'password': env.get('DOCFLOW_IMAP_PASSWORD') or '',
        'folder': (env.get('DOCFLOW_IMAP_FOLDER') or 'INBOX').strip() or 'INBOX',
        'interval': max(10, interval),
        'ssl': ssl_raw not in ('0', 'false', 'no'),
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg['host'] and cfg['user'] and cfg['password'])


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {**_EMPTY_STATE, 'seen': {}}
    if not isinstance(data, dict):
        return dict(_EMPTY_STATE)
    state = dict(_EMPTY_STATE)
    state['seen'] = {}  # never share the default dict between calls
    state.update(data)
    if not isinstance(state.get('seen'), dict):
        state['seen'] = {}
    return state


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE)


def _trim(state: dict) -> None:
    seen = state.get('seen') or {}
    if len(seen) > SEEN_LIMIT:
        keep = sorted(seen.items(), key=lambda item: item[1])[-SEEN_LIMIT:]
        state['seen'] = dict(keep)


def _header(value: str | None) -> str:
    """Decode RFC 2047 encoded headers and attachment names."""
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def message_attachments(msg: email.message.Message) -> list[dict]:
    """Every named, non-empty attachment of a message, decoded to bytes."""
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        raw_name = part.get_filename()
        if not raw_name:
            continue
        name = _header(raw_name)
        try:
            payload = part.get_payload(decode=True) or b''
        except Exception:
            continue
        if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
            continue
        attachments.append({
            'filename': name,
            'content_type': part.get_content_type(),
            'size': len(payload),
            'data': payload,
        })
    return attachments


def _raw_from_fetch(data: list) -> bytes | None:
    """The literal payload of ``UID FETCH ... (RFC822)``.

    ``imaplib`` returns a response that carries a literal as a tuple
    ``(b'1 (RFC822 {852}', <message bytes>)`` plus a closing ``b')'``, so the
    message is the longest byte string found at either level.
    """
    candidates: list[bytes] = []
    for item in data or []:
        if isinstance(item, (bytes, bytearray)):
            candidates.append(bytes(item))
        elif isinstance(item, tuple):
            candidates.extend(bytes(part) for part in item if isinstance(part, (bytes, bytearray)))
    if not candidates:
        return None
    return max(candidates, key=len)


def poll(handler, limit: int = 20) -> dict:
    """Check the mailbox once and hand every attachment to ``handler``.

    ``handler(attachment, message_meta)`` returns ``True`` when the attachment
    was ingested, ``None`` when it must be ignored on purpose (unsupported file
    type) and ``False`` when ingesting failed and the message should be retried
    on the next poll.
    """
    cfg = settings()
    state = _load_state()
    result = {'messages': 0, 'attachments': 0, 'ignored': 0, 'errors': []}
    if not (cfg['host'] and cfg['user'] and cfg['password']):
        state['last_error'] = 'mailbox is not configured'
        state['last_check'] = int(time.time())
        _save_state(state)
        result['errors'].append(state['last_error'])
        return result

    conn = None
    had_failure = False
    try:
        if cfg['ssl']:
            conn = imaplib.IMAP4_SSL(cfg['host'], cfg['port'])
        else:
            conn = imaplib.IMAP4(cfg['host'], cfg['port'])
        conn.login(cfg['user'], cfg['password'])
        conn.select(cfg['folder'])
        typ, data = conn.uid('search', None, 'UNSEEN')
        uids = (data[0] or b'').decode().split() if data and data[0] else []
        for uid in uids[:limit]:
            try:
                typ, msg_data = conn.uid('fetch', uid, '(RFC822)')
            except Exception as exc:
                result['errors'].append(f'fetch {uid}: {exc}')
                continue
            raw = _raw_from_fetch(msg_data or [])
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            meta = {
                'message_id': (msg.get('Message-ID') or f'uid:{uid}').strip(),
                'from': _header(msg.get('From')),
                'subject': _header(msg.get('Subject')),
                'date': _header(msg.get('Date')),
            }
            if meta['message_id'] in state['seen']:
                conn.uid('store', uid, '+FLAGS', '(\\Seen)')
                continue
            attachments = message_attachments(msg)
            if not attachments:
                conn.uid('store', uid, '+FLAGS', '(\\Seen)')
                state['seen'][meta['message_id']] = int(time.time())
                continue
            ingested = 0
            ignored = 0
            failed = 0
            for attachment in attachments:
                try:
                    outcome = handler(attachment, meta)
                except Exception as exc:
                    outcome = False
                    result['errors'].append(f'{attachment.get("filename")}: {exc}')
                if outcome is True:
                    ingested += 1
                elif outcome is None:
                    ignored += 1
                else:
                    failed += 1
            if failed:
                # Leave the message unread so the next poll retries it.
                had_failure = True
                state['last_error'] = f'{failed} attachment(s) could not be ingested; will retry'
                continue
            conn.uid('store', uid, '+FLAGS', '(\\Seen)')
            state['seen'][meta['message_id']] = int(time.time())
            state['messages'] = int(state.get('messages') or 0) + 1
            state['attachments'] = int(state.get('attachments') or 0) + ingested
            state['last_message'] = f"{meta['from']} · {meta['subject']}"[:160]
            result['messages'] += 1
            result['attachments'] += ingested
            result['ignored'] += ignored
        if not result['errors'] and not had_failure:
            state['last_error'] = None
    except Exception as exc:  # network, auth and protocol errors are one class here
        state['last_error'] = f'{type(exc).__name__}: {exc}'[:300]
        result['errors'].append(state['last_error'])
    finally:
        try:
            if conn is not None:
                conn.logout()
        except Exception:
            pass
    state['last_check'] = int(time.time())
    _trim(state)
    _save_state(state)
    return result


def status() -> dict:
    """What the UI and the API may know: never the password."""
    cfg = settings()
    state = _load_state()
    return {
        'configured': bool(cfg['host'] and cfg['user'] and cfg['password']),
        'host': cfg['host'],
        'user': cfg['user'],
        'folder': cfg['folder'],
        'interval': cfg['interval'],
        'ssl': cfg['ssl'],
        'last_check': state.get('last_check'),
        'last_error': state.get('last_error'),
        'last_message': state.get('last_message'),
        'messages': int(state.get('messages') or 0),
        'attachments': int(state.get('attachments') or 0),
    }


def reset_state() -> None:
    """Drop the polling state; used by tests and by an explicit reset."""
    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
