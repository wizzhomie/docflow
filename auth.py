"""Sign-in for the demo: a password, a Google account, and a session the database owns.

What is real here and what is not, because the difference matters:

- real: scrypt password hashing, a session token that exists in the cookie only,
  the SHA-256 of it in the database, an expiry, a login throttle and an audit row
  for every attempt;
- real: the Google path is a plain OAuth 2.0 code flow — no SDK, four HTTP calls;
- not real: e-mail verification, password reset and multi-factor. A demo has no
  mail delivery, and claiming otherwise in a README would be a lie.

A person without a password is a Google-only person: a null hash means "only the
provider can vouch for this account".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg2.extras import RealDictCursor

import db
from webutil import esc

router = APIRouter(tags=["auth"])

COOKIE = "docflow_session"
STATE_COOKIE = "docflow_oauth_state"

SESSION_HOURS = int(os.environ.get("DOCFLOW_SESSION_HOURS", "12"))
MAX_FAILURES = int(os.environ.get("DOCFLOW_MAX_FAILURES", "5"))
FAILURE_WINDOW_MINUTES = 15

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


# --- passwords -----------------------------------------------------------------


def hash_password(password: str) -> str:
    """scrypt with a per-password salt, in a format that names its parameters."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Compare in constant time; a malformed or empty hash never matches."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- sessions ------------------------------------------------------------------


def token_hash(token: str) -> str:
    """Only the hash of a session token is stored: a dump cannot be replayed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _client(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else ""
    return ip, request.headers.get("user-agent", "")[:200]


def record_event(email: str, method: str, outcome: str, request: Request, detail: str = "") -> None:
    ip, agent = _client(request)
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO auth_events (email, method, outcome, ip, user_agent, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (email.lower()[:200], method, outcome, ip, agent, detail[:200]),
        )


def recent_failures(email: str) -> int:
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM auth_events WHERE email = %s AND outcome = 'denied' "
            "AND at > now() - interval '%s minutes'",
            (email.lower(), FAILURE_WINDOW_MINUTES),
        )
        return cursor.fetchone()[0]


def create_session(user_id: int, request: Request, method: str = "password") -> str:
    token = secrets.token_urlsafe(32)
    ip, agent = _client(request)
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at, user_agent, ip) "
            "VALUES (%s, %s, now() + interval '%s hours', %s, %s)",
            (user_id, token_hash(token), SESSION_HOURS, agent, ip),
        )
    return token


def end_session(request: Request) -> None:
    token = request.cookies.get(COOKIE)
    if not token:
        return
    with db.connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash(token),))


def session_person(request: Request) -> dict[str, Any] | None:
    """The person behind the session cookie, with company and role."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT u.id, u.email, u.full_name, u.role, u.company_id, c.name AS company_name,
                   s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN companies c ON c.id = u.company_id
            WHERE s.token_hash = %s AND s.expires_at > now()
            """,
            (token_hash(token),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s "
            "AND last_seen_at < now() - interval '5 minutes'",
            (token_hash(token),),
        )
        # The request log reads these: a line that names the person and the
        # company answers "who did it" without opening another query.
        request.state.user = row["email"]
        request.state.company = row["company_name"]
        return dict(row)


# --- pages ---------------------------------------------------------------------

STYLE = """
:root{--bg:#07080d;--surface:#0e1018;--line:#232637;--text:#eceef5;--muted:#9aa0b4;--accent:#7c5cff;--accent-2:#22d3ee}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px;
  font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--text);
  background:radial-gradient(1200px 600px at 15% -10%,rgba(124,92,255,.22),transparent 60%),
             radial-gradient(900px 500px at 95% 110%,rgba(34,211,238,.16),transparent 55%),var(--bg)}
.shell{width:100%;max-width:420px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:22px}
.mark{width:26px;height:26px;border-radius:50%;position:relative;
  background:conic-gradient(from 180deg,var(--accent),var(--accent-2),#ff5c9d,var(--accent))}
.mark::after{content:"";position:absolute;inset:6px;border-radius:50%;background:var(--bg);border:2px solid rgba(255,255,255,.7)}
.brand b{font-size:17px;letter-spacing:-.01em}
.wrap{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:28px}
h1{font-size:20px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 22px;font-size:14px}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:14px 0 5px}
input{font:15px inherit;padding:11px 13px;border:1px solid var(--line);border-radius:11px;width:100%;
  background:#0b0d16;color:var(--text)}
input:focus{outline:none;border-color:var(--accent)}
button{font:15px inherit;margin-top:20px;padding:12px 18px;border:0;border-radius:999px;
  background:var(--accent);color:#fff;width:100%;cursor:pointer}
button:hover{filter:brightness(1.08)}
.google{display:block;text-align:center;margin-top:12px;padding:12px 18px;border:1px solid var(--line);
  border-radius:999px;color:var(--text);text-decoration:none;font-size:14px;background:rgba(255,255,255,.04)}
.google:hover{background:rgba(255,255,255,.08)}
.error{background:rgba(255,92,92,.12);border:1px solid rgba(255,92,92,.35);border-radius:12px;
  padding:10px 14px;margin-bottom:16px;font-size:14px}
.foot{margin-top:18px;font-size:13px;color:var(--muted);text-align:center}
.foot a{color:#a9a4ff}
"""


def _page(title: str, body: str) -> str:
    safe_title = esc(title)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DocFlow · {safe_title}</title><style>{STYLE}</style></head>
<body><div class="shell">
  <div class="brand"><span class="mark"></span><b>DocFlow</b></div>
  <div class="wrap"><h1>{safe_title}</h1>{body}</div>
  <div class="foot"><a href="/">← back to the overview</a></div>
</div></body></html>"""


DEFAULT_LANDING = "/app"


def _next_path(next_path: str | None) -> str:
    """Only a local path may be a redirect target, never another host.

    Signing in lands in the workspace: the queue is part of the platform, not a
    separate destination the person has to know about.
    """
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return DEFAULT_LANDING


def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, error: str | None = None) -> Any:
    if session_person(request) is not None:
        return RedirectResponse(_next_path(next), status_code=303)
    # `error` arrives in the query string, so it is hostile by construction: a
    # link like `/auth/login?error=<script>…` must print, not run.
    message = f'<div class="error">{esc(error)}</div>' if error else ""
    google = (
        '<a class="google" href="/auth/google">Continue with Google</a>'
        if google_configured()
        else '<div class="foot">Google sign-in is off: no client id configured.</div>'
    )
    body = (
        f'{message}<p class="sub">Sign in to review documents of your company.</p>'
        f'<form method="post" action="/auth/login">'
        f'<label for="email">E-mail</label>'
        f'<input id="email" name="email" type="email" autocomplete="username" required>'
        f'<label for="password">Password</label>'
        f'<input id="password" name="password" type="password" autocomplete="current-password" required>'
        f'<input type="hidden" name="next" value="{esc(_next_path(next))}">'
        f'<button type="submit">Sign in</button></form>{google}'
    )
    return _page("Sign in", body)


@router.post("/auth/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/app"),
) -> Any:
    normalised = email.strip().lower()
    if recent_failures(normalised) >= MAX_FAILURES:
        record_event(normalised, "password", "throttled", request)
        return RedirectResponse(
            "/auth/login?error=Too+many+attempts.+Try+again+in+fifteen+minutes", status_code=303
        )

    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            "SELECT id, password_hash, role FROM users WHERE lower(email) = %s", (normalised,)
        )
        row = cursor.fetchone()

    # Same answer for "no such person" and "wrong password": a login form that
    # tells an attacker which e-mails exist is an account enumeration leak.
    if row is None or not verify_password(password, row.get("password_hash")):
        record_event(normalised, "password", "denied", request)
        return RedirectResponse("/auth/login?error=Wrong+e-mail+or+password", status_code=303)

    token = create_session(row["id"], request)
    record_event(normalised, "password", "ok", request)
    response = RedirectResponse(_next_path(next), status_code=303)
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_HOURS * 3600,
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/auth/logout")
def logout(request: Request) -> Any:
    person = session_person(request)
    end_session(request)
    if person is not None:
        record_event(person["email"], "password", "ok", request, detail="logout")
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


# --- Google --------------------------------------------------------------------


def _redirect_uri(request: Request) -> str:
    configured = os.environ.get("GOOGLE_REDIRECT_URI")
    if configured:
        return configured
    return f"{request.url.scheme}://{request.url.netloc}/auth/google/callback"


@router.get("/auth/google")
def google_start(request: Request, next: str | None = None) -> Any:
    if not google_configured():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": _redirect_uri(request),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=303)
    response.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600,
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Any:
    if error:
        record_event("", "google", "denied", request, detail=error)
        return RedirectResponse("/auth/login?error=Google+sign-in+was+cancelled", status_code=303)
    if not google_configured():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    if not code or not state or state != request.cookies.get(STATE_COOKIE):
        record_event("", "google", "denied", request, detail="bad state")
        return RedirectResponse("/auth/login?error=Sign+in+expired,+try+again", status_code=303)

    try:
        with httpx.Client(timeout=10) as client:
            token_response = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uri": _redirect_uri(request),
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]
            profile = client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            profile.raise_for_status()
            info = profile.json()
    except (httpx.HTTPError, KeyError, ValueError):
        record_event("", "google", "error", request, detail="token exchange failed")
        return RedirectResponse("/auth/login?error=Google+did+not+answer,+try+again", status_code=303)

    email = (info.get("email") or "").strip().lower()
    subject = info.get("sub") or ""
    if not email or not info.get("email_verified", False):
        record_event(email, "google", "denied", request, detail="unverified email")
        return RedirectResponse("/auth/login?error=Google+account+has+no+verified+e-mail", status_code=303)

    with db.connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT id FROM users WHERE lower(email) = %s", (email,))
        row = cursor.fetchone()
        if row is None:
            # An unknown Google account joins nobody: being able to prove an
            # e-mail address is not the same as belonging to a company.
            record_event(email, "google", "denied", request, detail="no such person")
            return RedirectResponse(
                "/auth/login?error=This+e-mail+is+not+invited+to+any+company", status_code=303
            )
        cursor.execute(
            "UPDATE users SET google_sub = coalesce(google_sub, %s) WHERE id = %s",
            (subject, row["id"]),
        )
        user_id = row["id"]

    token = create_session(user_id, request, method="google")
    record_event(email, "google", "ok", request)
    response = RedirectResponse(DEFAULT_LANDING, status_code=303)
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_HOURS * 3600,
        secure=request.url.scheme == "https",
    )
    response.delete_cookie(STATE_COOKIE)
    return response


def status() -> dict[str, Any]:
    """What /api/health/detail reports about sign-in."""
    return {
        "password": True,
        "google": google_configured(),
        "session_hours": SESSION_HOURS,
        "cookie": COOKIE,
    }
