"""What the service does when it is asked for more than it can carry.

Measured on the live demo before these checks were written: a 12-page scan held
the only event loop for 37 seconds, during which `/api/health` timed out; five
parallel uploads of one page each took 16 seconds apiece and the health endpoint
missed two samples; a 201-page PDF was refused in 25 milliseconds because the
page limit is checked before parsing. The checks below keep the boundary honest:
a slow extraction runs off the event loop, a burst is refused with a Retry-After
instead of stacking, and a scan is only rendered up to the OCR page limit.

Run: python3 tests/test_limits.py
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import db  # noqa: E402
import extractor  # noqa: E402
import intake  # noqa: E402
import routes.reports as reports  # noqa: E402
import settings  # noqa: E402


def _temp_storage(monkeypatch, tmp_path):
    """A document written by a check must not land in the demo's folders."""
    for name in ('UPLOADS', 'PENDING'):
        directory = tmp_path / name.lower()
        directory.mkdir()
        monkeypatch.setattr(settings, name, directory)
    monkeypatch.setattr(settings, 'CONFIRMED', tmp_path / 'confirmed.jsonl')


def test_an_upload_is_refused_when_every_extraction_slot_is_busy(monkeypatch, tmp_path):
    _temp_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'EXTRACTION_WAIT_SECONDS', 0.2)
    held = 0
    # Fill whatever is free: another check in the same process may have left a
    # slot taken, and the point is that a full gate refuses rather than queues.
    while intake.GATE.acquire(0.01):
        held += 1
    try:
        assert held >= 1
        client = TestClient(app.app)
        response = client.post(
            '/api/upload', files={'file': ('note.txt', b'Invoice 1 for 100', 'text/plain')}
        )
    finally:
        for _ in range(held):
            intake.GATE.release()
    assert response.status_code == 503
    assert response.headers['retry-after'] == '5'
    # The bytes were spooled before the gate was asked: a refusal must not
    # leave an upload nobody can reach.
    assert not list((tmp_path / 'uploads').iterdir())


def test_a_slow_extraction_does_not_stop_the_rest_of_the_service(monkeypatch, tmp_path):
    _temp_storage(monkeypatch, tmp_path)
    original = intake._process

    def slow(*args, **kwargs):
        time.sleep(1.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(intake, '_process', slow)

    async def scenario():
        transport = httpx.ASGITransport(app=app.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            upload = asyncio.create_task(
                client.post(
                    '/api/upload',
                    files={'file': ('note.txt', b'Invoice 1 for 100', 'text/plain')},
                )
            )
            await asyncio.sleep(0.2)
            started = time.monotonic()
            health = await client.get('/api/health')
            latency = time.monotonic() - started
            response = await upload
        return health.status_code, latency, response.status_code

    health_status, latency, upload_status = asyncio.run(scenario())
    assert upload_status == 200
    assert health_status == 200
    # A second, not a few hundred milliseconds: the parse sleeps 1.0 s, and the
    # loop has to answer while it does.
    assert latency < 0.4


def test_a_scan_is_rendered_only_up_to_the_ocr_page_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'MAX_OCR_PAGES', 3)
    monkeypatch.setattr(extractor.shutil, 'which', lambda name: f'/usr/bin/{name}')
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(extractor.subprocess, 'run', fake_run)
    monkeypatch.setattr(extractor, '_ocr_image', lambda page: '')

    assert extractor._ocr_pdf(tmp_path / 'scan.pdf') == ''
    command = commands[0]
    assert command[0] == 'pdftoppm'
    assert command[command.index('-f') + 1] == '1'
    assert command[command.index('-l') + 1] == '3'


def test_a_scan_stops_when_the_ocr_budget_is_spent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'MAX_OCR_PAGES', 5)
    monkeypatch.setattr(settings, 'MAX_OCR_SECONDS', 0)
    monkeypatch.setattr(extractor.shutil, 'which', lambda name: f'/usr/bin/{name}')
    rendered: list[Path] = []

    def fake_run(command, **kwargs):
        # pdftoppm names its pages `<prefix>-1.png`, `<prefix>-2.png`, ...
        prefix = Path(command[-1])
        for number in range(1, 6):
            page = prefix.with_name(f'{prefix.name}-{number}.png')
            page.write_bytes(b'png')
            rendered.append(page)
        return subprocess.CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(extractor.subprocess, 'run', fake_run)
    calls: list[Path] = []
    monkeypatch.setattr(extractor, '_ocr_image', lambda page: calls.append(page) or '')

    assert extractor._ocr_pdf(tmp_path / 'scan.pdf') == ''
    assert rendered, 'the fake renderer has to have produced pages'
    # A zero budget is spent before the first page: Tesseract is never called.
    assert calls == []


def test_a_connection_lost_mid_request_is_503_not_a_traceback(monkeypatch):
    def lost(*args, **kwargs):
        raise db.psycopg2.OperationalError(
            'server closed the connection unexpectedly'
        )

    monkeypatch.setattr(reports, '_confirmed_rows', lost)
    client = TestClient(app.app, raise_server_exceptions=False)
    response = client.get('/api/documents')
    assert response.status_code == 503
    assert response.headers['retry-after'] == '5'
    assert response.json()['detail'] == 'PostgreSQL is unavailable'
