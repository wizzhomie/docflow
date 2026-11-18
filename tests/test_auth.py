"""Checks for sign-in: hashing, session tokens, redirects and the throttle.

These are the parts where a quiet mistake becomes a security hole, so they are
tested rather than described: a hash that verifies the wrong password, a session
that stores its own token, or a redirect that points at another host.

Run: python3 tests/test_auth.py
The database checks skip themselves when PostgreSQL is not reachable.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

import auth  # noqa: E402

DSN = os.environ.get('DOCFLOW_PG_DSN', 'dbname=docflow_dev')

try:
    with psycopg2.connect(DSN):
        POSTGRES_AVAILABLE = True
except psycopg2.Error:
    POSTGRES_AVAILABLE = False


def test_a_password_verifies_against_its_own_hash():
    stored = auth.hash_password('correct horse battery staple')
    assert auth.verify_password('correct horse battery staple', stored)


def test_a_wrong_password_does_not_verify():
    stored = auth.hash_password('correct horse battery staple')
    assert not auth.verify_password('correct horse battery stapl', stored)
    assert not auth.verify_password('', stored)


def test_two_hashes_of_one_password_differ():
    """A salt per password: the same secret must not produce the same hash."""
    assert auth.hash_password('same') != auth.hash_password('same')


def test_a_missing_or_broken_hash_never_matches():
    assert not auth.verify_password('x', None)
    assert not auth.verify_password('x', '')
    assert not auth.verify_password('x', 'bcrypt$whatever')
    assert not auth.verify_password('x', 'scrypt$not-a-number$8$1$aa$bb')


def test_the_hash_names_its_parameters():
    stored = auth.hash_password('anything')
    scheme, n, r, p, salt, digest = stored.split('$')
    assert scheme == 'scrypt'
    assert int(n) >= 2**14, 'scrypt cost is too low to be worth the name'
    assert int(r) >= 8 and int(p) >= 1
    assert len(salt) > 8 and len(digest) > 8


def test_a_session_token_is_not_stored_as_is():
    if not POSTGRES_AVAILABLE:
        return
    token = 'a-token-that-must-not-appear-in-the-database'
    assert auth.token_hash(token) != token

    slug = f'auth-{uuid.uuid4().hex[:8]}'
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO companies (name, slug) VALUES ('Auth test', %s) RETURNING id", (slug,))
        company_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO users (company_id, email, full_name, role) VALUES (%s, %s, 'Auth Test', 'owner') RETURNING id",
            (company_id, f'{slug}@test.local'),
        )
        user_id = cursor.fetchone()[0]
    try:
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sessions (user_id, token_hash, expires_at) "
                "VALUES (%s, %s, now() + interval '1 hour')",
                (user_id, auth.token_hash(token)),
            )
            cursor.execute(
                "SELECT count(*) FROM sessions WHERE token_hash = %s", (token,)
            )
            assert cursor.fetchone()[0] == 0, 'the raw token was stored'
            cursor.execute(
                "SELECT count(*) FROM sessions WHERE token_hash = %s", (auth.token_hash(token),)
            )
            assert cursor.fetchone()[0] == 1
    finally:
        with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM companies WHERE id = %s', (company_id,))


def test_a_redirect_stays_inside_the_site():
    assert auth._next_path('/app') == '/app'
    assert auth._next_path('/pg/queue') == '/pg/queue'
    assert auth._next_path('https://evil.test/collect') == auth.DEFAULT_LANDING
    assert auth._next_path('//evil.test/collect') == auth.DEFAULT_LANDING
    assert auth._next_path(None) == auth.DEFAULT_LANDING


def test_signing_in_lands_in_the_workspace():
    """The queue is a part of the platform, not a separate destination."""
    assert auth.DEFAULT_LANDING == '/app'


def test_a_login_post_needs_the_token_from_the_page():
    """SameSite is a browser setting; the form carries its own proof as well."""
    if not POSTGRES_AVAILABLE:
        return
    import re

    from fastapi.testclient import TestClient

    import app as docflow_app

    client = TestClient(docflow_app.app, raise_server_exceptions=False)
    page = client.get('/auth/login')
    assert page.status_code == 200

    # A fresh address each run: the throttle counts failures per e-mail, and a
    # fixed one would make this test flip after five runs in a quarter of an hour.
    email = f'csrf-{uuid.uuid4().hex[:8]}@test.local'
    without = client.post(
        '/auth/login',
        data={'email': email, 'password': 'whatever'},
        follow_redirects=False,
    )
    assert without.status_code == 303
    assert 'Page+expired' in without.headers['location']

    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    with_token = client.post(
        '/auth/login',
        data={'email': email, 'password': 'definitely-wrong', 'csrf': token},
        follow_redirects=False,
    )
    assert with_token.status_code == 303
    # Past the token check, the answer is about the password, not the form.
    assert 'Wrong+e-mail+or+password' in with_token.headers['location']


def test_a_stale_form_token_does_not_count_towards_the_throttle():
    """An open tab is not a password guess: only credential failures lock an account."""
    if not POSTGRES_AVAILABLE:
        return
    email = f'csrf-throttle-{uuid.uuid4().hex[:8]}@test.local'
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        for _ in range(6):
            cursor.execute(
                "INSERT INTO auth_events (email, method, outcome, detail) "
                "VALUES (%s, 'password', 'denied', 'csrf')",
                (email,),
            )
    assert auth.recent_failures(email) == 0


def test_a_failed_attempt_is_counted_for_the_throttle():
    if not POSTGRES_AVAILABLE:
        return
    email = f'throttle-{uuid.uuid4().hex[:8]}@test.local'
    with psycopg2.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO auth_events (email, method, outcome, detail) VALUES "
            "(%s, 'password', 'denied', 'test'), (%s, 'password', 'denied', 'test')",
            (email, email),
        )
    assert auth.recent_failures(email) == 2
    assert auth.recent_failures('nobody@test.local') == 0


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('auth checks passed')


if __name__ == '__main__':
    main()
