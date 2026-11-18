"""Values that go into the server-rendered pages.

The queue and the report are built by string concatenation, and the values they
print come from the uploaded document: a supplier name is whatever the invoice
says, and an invoice can say `<script>`. Escaping where the value is used is
what keeps a document from running code in the browser of the person who
reviews it — the file is hostile input exactly like a request body is.

A template engine with autoescape would make the same guarantee by construction,
and is the right next step when these pages grow more forms; until then this
module is the single place that knows the rule, so a reviewer has one file to
check instead of every f-string.
"""

from __future__ import annotations

from html import escape as _escape
from typing import Any

DASH = "\u2014"


def esc(value: Any) -> str:
    """Text safe to place in HTML content and inside a quoted attribute."""
    if value is None:
        return ""
    return _escape(str(value), quote=True)


def shown(value: Any) -> str:
    """A value for display: escaped, with an em dash where there is nothing."""
    if value is None or value == "":
        return DASH
    return esc(value)
