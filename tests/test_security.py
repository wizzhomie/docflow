"""Checks for the two ways a demo turns into an incident.

The first is a request that becomes a path: an id is a file name here, so an
unchecked id is a way to read something the service never meant to serve. The
second is a document that becomes markup: the supplier name on an invoice is
whatever the invoice says, and an invoice can say `<script>`.

Both are checked at the seam where the value is used, because that is the only
place every future caller has to pass through.

Run: python3 tests/test_security.py
"""

import json
import sys
import tempfile
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

import app  # noqa: E402
import observability  # noqa: E402
import pg_queue  # noqa: E402
import webutil  # noqa: E402

# The checks write documents; they must not touch the running demo's data.
ROOT = Path(tempfile.mkdtemp(prefix='docflow-security-'))
app.UPLOADS = ROOT / 'uploads'
app.PENDING = ROOT / 'pending'
app.INBOX = ROOT / 'inbox'
app.INBOX_DONE = ROOT / 'inbox' / 'processed'
app.CONFIRMED = ROOT / 'confirmed.jsonl'
for directory in (app.UPLOADS, app.PENDING, app.INBOX, app.INBOX_DONE):
    directory.mkdir(parents=True, exist_ok=True)
app.CONFIRMED.write_text('', encoding='utf-8')


def _request(cookies: dict[str, str] | None = None) -> Request:
    """A request with nothing but a cookie header: no server, no database."""
    pairs = '; '.join(f'{name}={value}' for name, value in (cookies or {}).items())
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': 'GET',
        'scheme': 'http',
        'path': '/api/confirm',
        'raw_path': b'/api/confirm',
        'query_string': b'',
        'server': ('test', 80),
        'client': ('127.0.0.1', 1234),
        'headers': [(b'cookie', pairs.encode())] if pairs else [],
    }
    return Request(scope)


# --- a request must not become a path ------------------------------------------


def test_meta_path_refuses_anything_but_a_document_id():
    for bad in ('../confirmed', '..%2F..%2Fapp', 'abcdefghij', 'abcdef012', ''):
        try:
            app._meta_path(bad)
        except Exception as error:  # noqa: BLE001 - the type is the point
            assert getattr(error, 'status_code', None) == 404, bad
        else:
            raise AssertionError(f'{bad!r} reached the filesystem')


def test_files_route_does_not_walk_out_of_the_uploads_folder():
    client = TestClient(app.app, raise_server_exceptions=False)
    for attempt in (
        '/files/..%2F..%2Fapp.py',
        '/files/%2e%2e%2f%2e%2e%2fapp.py',
        '/files/..%2Fconfirmed.jsonl',
        '/files/notanid',
    ):
        response = client.get(attempt)
        assert response.status_code == 404, attempt


def test_files_route_still_serves_a_known_upload():
    doc_id = uuid.uuid4().hex[:10]
    (app.UPLOADS / f'{doc_id}.pdf').write_bytes(b'%PDF-1.4 demo')
    response = TestClient(app.app).get(f'/files/{doc_id}')
    assert response.status_code == 200
    assert response.content.startswith(b'%PDF')


def test_confirm_refuses_an_id_that_is_not_a_document_id():
    client = TestClient(app.app)
    for bad in ('../../etc/passwd', 'abcdefghij', 'abc', '', 'abcdef0123/../x'):
        response = client.post('/api/confirm', json={'id': bad, 'fields': {}})
        assert response.status_code == 422, bad


# --- a document must not become markup ----------------------------------------


def test_escaping_covers_quotes_not_only_brackets():
    assert webutil.esc('<b>&"x"</b>') == '&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;'
    assert webutil.esc(None) == ''
    assert webutil.shown('') == webutil.DASH


def test_a_supplier_name_from_a_pdf_cannot_run_script():
    documents = [
        {
            'id': 1,
            'doc_type': 'invoice_ua',
            'supplier': '<script>alert(1)</script>',
            'number': '"><img src=x onerror=alert(1)>',
            'doc_date': '2026-01-01',
            'total': 1234,
            'currency': 'UAH',
            'events': 0,
        }
    ]
    html = pg_queue._queue_rows(documents)
    assert '<script>' not in html
    assert '<img' not in html
    assert '&lt;script&gt;' in html
    assert '1 234 UAH' in html


def test_the_review_form_cannot_be_broken_out_of_an_attribute():
    item = {
        'id': 7,
        'status': 'new',
        'number': '" onfocus="alert(1)',
        'doc_date': '',
        'total': 12.5,
        'currency': 'UAH',
    }
    person = {'role': 'owner', 'company_name': 'Demo', 'full_name': 'Owner'}
    html = pg_queue._document_form(item, person)
    assert 'onfocus="' not in html
    assert '&quot; onfocus=&quot;' in html


def test_a_viewer_gets_no_form_to_submit():
    item = {'id': 7, 'status': 'new', 'number': '1', 'doc_date': '', 'total': 1, 'currency': 'UAH'}
    html = pg_queue._document_form(item, {'role': 'viewer', 'company_name': 'Demo'})
    assert '<form' not in html
    assert 'denied' in html


# --- one source of truth for the numbers --------------------------------------


def _row(edited: list[str], doc_type: str | None = 'invoice_ua') -> dict:
    record = {
        'id': uuid.uuid4().hex[:10],
        'uploaded_at': '2026-11-13T10:00:00+00:00',
        'confirmed_at': '2026-11-13T10:02:00+00:00',
        'fields': {'total': 1000.0},
        'edited_fields': edited,
        'confidence': 0.9,
    }
    if doc_type:
        record['doc_type'] = doc_type
    return record


def test_accuracy_divides_by_the_fields_the_profile_asks_for():
    impact = app.build_impact([_row(['supplier'])], 3.0)
    assert impact['fields_reviewed'] == 7
    assert impact['field_accuracy'] == round((1 - 1 / 7) * 100, 1)


def test_the_headline_and_the_report_report_agree():
    rows = [_row([]), _row(['supplier', 'total']), _row(['vat'])]
    original = app._read_confirmed
    app._read_confirmed = lambda: rows
    try:
        headline = app.stats()
        report = app.impact()
    finally:
        app._read_confirmed = original
    assert headline['documents'] == report['documents']
    assert headline['edited_fields'] == report['edited_fields']
    assert headline['accuracy'] == report['field_accuracy']
    assert headline['saved_minutes'] == report['time_saved_minutes']


# --- two clicks at once --------------------------------------------------------


def test_a_second_confirmation_at_the_same_moment_is_not_a_second_deal():
    doc_id = uuid.uuid4().hex[:10]
    fields = {
        'number': '2026-777',
        'date': '2026-12-01',
        'supplier': 'ТОВ «Тест»',
        'tax_id': '12345678',
        'total': 500.0,
        'vat': 0.0,
        'currency': 'UAH',
    }
    meta = {
        'id': doc_id,
        'filename': 'invoice-777.pdf',
        'uploaded_at': '2026-12-01T09:00:00+00:00',
        'source': 'upload',
        'parsed': {
            'type': 'invoice_ua',
            'type_label': 'Invoice',
            'doc_title': 'Рахунок',
            'fields': {key: {'value': value, 'confidence': 0.9} for key, value in fields.items()},
            'issues': [],
            'confidence': 0.9,
            'text_preview': 'Рахунок № 2026-777',
        },
    }
    app._meta_path(doc_id).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')

    payload = app.ConfirmPayload(id=doc_id, fields=fields)
    request = _request()
    answers: list[dict] = []
    barrier = threading.Barrier(4)

    def run() -> None:
        barrier.wait()
        answers.append(app.confirm(payload, request))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(answers) == 4
    duplicates = [answer for answer in answers if answer['crm'].get('duplicate')]
    assert len(duplicates) == 3, 'a simultaneous second click must be answered as a duplicate'
    written = [json.loads(line) for line in app.CONFIRMED.read_text(encoding='utf-8').splitlines() if line]
    assert len(written) == 4, 'every click is recorded, the duplicate flag is what marks it'


def test_the_confirm_holds_the_lock_across_the_check_and_the_write():
    """The race itself needs a scheduler to happen, so the mechanism is pinned:
    the duplicate check and the append run inside the lock, not next to it."""
    seen: dict[str, bool] = {}
    original = app._confirm_in_files

    def probe(payload):
        seen['locked'] = app.CONFIRM_LOCK.locked()
        return {'ok': True}

    app._confirm_in_files = probe
    try:
        app.confirm(app.ConfirmPayload(id='abcdef0123', fields={}), _request())
    finally:
        app._confirm_in_files = original
    assert seen.get('locked') is True


# --- the log must say who, without keeping the token --------------------------


def test_the_log_names_the_session_by_digest_not_by_token():
    token = 'a-bearer-token-nobody-should-find-in-a-log'
    user, _ = observability._person(_request({'docflow_session': token}))
    assert token not in (user or '')
    assert user.startswith('session:')
    assert len(user) == len('session:') + 8


def test_the_log_prefers_the_resolved_person():
    request = _request()
    request.state.user = 'owner@demo.local'
    request.state.company = 'Demo Company'
    assert observability._person(request) == ('owner@demo.local', 'Demo Company')


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('security checks passed')


if __name__ == '__main__':
    main()
