"""Form tokens: the second line of defence after SameSite.

A session cookie marked `SameSite=Lax` already stops most cross-site POSTs, but
"most" is not a guarantee — a browser that ignores the attribute, or a page on a
sibling subdomain, would still be able to confirm documents on someone's behalf.
This is the standard double submit: a random value in a cookie and in a hidden
field of the form, compared when the form comes back. It needs no secret and no
server-side state, which is why it fits a demo that must run with nothing
configured.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response

COOKIE = 'docflow_csrf'


def new_token() -> str:
    return secrets.token_urlsafe(24)


def expected(request: Request) -> str:
    """The token this browser should send back: its cookie, or a fresh one."""
    return request.cookies.get(COOKIE) or new_token()


def ensure(response: Response, request: Request, value: str) -> None:
    """Give the browser the token the page embeds, when it does not have one yet.

    The value is passed in rather than generated here: the page and the cookie must
    carry the same token, and two calls to `new_token()` are two different tokens.
    """
    if request.cookies.get(COOKIE):
        return
    response.set_cookie(
        COOKIE,
        value,
        httponly=True,
        samesite='lax',
        path='/',
        secure=request.url.scheme == 'https',
    )


def valid(request: Request, submitted: str | None) -> bool:
    """Whether the submitted token is the one this browser was given.

    Compared with `compare_digest`: a form token is short enough that a timing
    difference is worth removing rather than arguing about.
    """
    cookie = request.cookies.get(COOKIE) or ''
    if not cookie or not submitted:
        return False
    return secrets.compare_digest(cookie, str(submitted))
