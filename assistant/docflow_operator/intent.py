"""Deterministic intent shortcuts for unambiguous questions.

A 1.5B model picks the right tool most of the time, not every time: "propose confirming
the newest document" once became a payment letter. For a handful of phrases the intent is
unambiguous, so the tool is chosen by rules instead of by sampling. Anything mixed or
unusual still goes to the model.

Each shortcut is one regex family and one tool call; if two families match, the model
decides, so "check the duplicates and draft letters" is not forced into one read.
"""
from __future__ import annotations

import re

DOC_ID_RE = re.compile(r'\b[0-9a-f]{8,12}\b', re.IGNORECASE)

PATTERNS: list[tuple[str, re.Pattern]] = [
    ('propose_confirm', re.compile(
        r'(предлож\w*\s+подтверд|propose\s+(confirm|to confirm)|предлагаю\s+подтверд)', re.IGNORECASE)),
    ('draft_letter', re.compile(
        r'(черновик\w*|draft|письм\w*|letter|напомин\w*|remind)', re.IGNORECASE)),
    ('check_duplicate', re.compile(r'(дубл\w*|duplicate)', re.IGNORECASE)),
    ('summarise', re.compile(
        r'(сводк\w*|summary|summarise|summarize|итог\w*|сколько\s+документ)', re.IGNORECASE)),
    ('list_queue', re.compile(
        r'(какие\s+документ\w*|which\s+documents?|что\s+нужн\w*|what\s+needs?)', re.IGNORECASE)),
]

PAYMENT_RE = re.compile(r'(оплат|payment|pay)', re.IGNORECASE)
APPROVAL_RE = re.compile(r'(соглас|approv)', re.IGNORECASE)
REPLY_RE = re.compile(r'(ответ|reply)', re.IGNORECASE)


EXPLANATION_RE = re.compile(
    r'^\s*(как\s|как$|why\b|how\b|почему\b|зачем\b|что\s+такое\b|what\s+is\b|'
    r'what\s+does\b|explain\b|объясни\b)', re.IGNORECASE)


def intent_plan(task: str) -> tuple[str, dict] | None:
    """Return a deterministic (tool, arguments) plan, or None to let the model decide."""
    text = task.lower()
    if EXPLANATION_RE.search(text):
        return None  # "how do I ..." is a question about the platform, not an action
    names = [name for name, pattern in PATTERNS if pattern.search(text)]
    if len(names) != 1:
        return None
    name = names[0]
    doc_id = (DOC_ID_RE.search(text).group(0) if DOC_ID_RE.search(text) else 'latest')
    if name == 'propose_confirm':
        return 'propose_confirm', {'doc_id': doc_id}
    if name == 'draft_letter':
        if REPLY_RE.search(text):
            kind = 'supplier_reply'
        elif PAYMENT_RE.search(text):
            kind = 'payment_request'
        elif APPROVAL_RE.search(text):
            kind = 'approval_request'
        else:
            return None  # a letter without a kind is ambiguous; let the model ask itself
        return 'draft_letter', {'doc_id': doc_id, 'kind': kind}
    if name == 'check_duplicate':
        return 'check_duplicate', {'doc_id': doc_id}
    if name == 'summarise':
        if re.search(r'(today|сегодня|за день)', text):
            period = 'today'
        elif re.search(r'(week|недел)', text):
            period = 'week'
        else:
            period = 'all'
        return 'summarise', {'period': period}
    if name == 'list_queue':
        return 'list_queue', {'limit': 10}
    return None
