"""Everything that turns an incoming file into a pending document.

Three doors lead in — an upload, the watched folder, the mailbox — and all three
end in `_process`, so a document that arrives by e-mail is recognised, reviewed
and routed exactly like one dropped into the folder.
"""
import asyncio
import json
import logging
import shutil
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path

import docstore
import mailbox
import settings
from extractor import extract_text, parse_invoice, parse_items

logger = logging.getLogger('docflow')


class ExtractionBusy(RuntimeError):
    """Every extraction slot is taken by someone else.

    Raised instead of waiting forever: the upload route turns it into a 503 with
    a `Retry-After`, so a burst sees a fast, honest refusal while the documents
    already being read still get the CPU.
    """


class _Gate:
    """A bounded number of extractions at once, readable from any thread.

    A `threading.Semaphore` would be simpler, but its size is fixed when it is
    built, and the knob here is a settings value like every other one. The
    condition also makes the waiting time countable, so a caller can decide how
    long "busy" may last before it is told to come back.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active >= max(1, settings.EXTRACTIONS):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            self._active += 1
            return True

    def release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify()

    def active(self) -> int:
        with self._condition:
            return self._active


GATE = _Gate()


def _pdf_page_count(path: Path) -> int | None:
    """Pages in a PDF, or None when it is not a PDF or cannot be read.

    pypdf walks the page tree without extracting text, so the check costs
    milliseconds even for a document that would take minutes to read.
    """
    if path.suffix.lower() != '.pdf':
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 - an unreadable PDF is a parse problem, not a limit
        return None


def _document_problem(path: Path, size: int | None = None) -> str | None:
    """Why this file must not be processed, or None when it may be.

    The rule lives here once, and each entry point decides what to do with the
    answer: the upload refuses with 413, the watched folder leaves the file
    alone, the mailbox ignores the attachment.
    """
    actual = path.stat().st_size if size is None else size
    if actual > settings.MAX_UPLOAD_BYTES:
        return f'file is larger than {settings.MAX_UPLOAD_MB:g} MB'
    pages = _pdf_page_count(path)
    if pages is not None and pages > settings.MAX_PDF_PAGES:
        return f'PDF has {pages} pages, the limit is {settings.MAX_PDF_PAGES}'
    return None


def _process(
    doc_id: str,
    filename: str,
    path: Path,
    source: str = 'upload',
    extra: dict | None = None,
    persist: bool = True,
) -> dict:
    text = extract_text(path)
    items = parse_items(text)
    # First pass to get tax_id/supplier for supplier profile
    parsed_preview = parse_invoice(text, items=items)
    preview_tax_id = parsed_preview['fields'].get('tax_id', {}).get('value')
    preview_supplier = parsed_preview['fields'].get('supplier', {}).get('value')
    # Second pass with supplier profile
    parsed = parse_invoice(
        text, items=items, tax_id=preview_tax_id, supplier_name=preview_supplier
    )
    meta = {
        'id': doc_id,
        'filename': filename,
        'uploaded_at': docstore._now(),
        'parsed': parsed,
        'items': items,
        'text_preview': text[:4000],
        'suffix': path.suffix.lower(),
        'source': source,
    }
    if extra:
        meta.update(extra)
    if persist:
        # Signed in, the document's state is the row in the database, and a pending
        # JSON next to it would be a second truth to keep in sync.
        docstore._meta_path(doc_id).write_text(
            json.dumps(meta, ensure_ascii=False), encoding='utf-8'
        )
    return meta


async def process_async(
    doc_id: str,
    filename: str,
    path: Path,
    source: str = 'upload',
    extra: dict | None = None,
    persist: bool = True,
) -> dict:
    """`_process` off the event loop, behind the extraction gate.

    Reading a scan takes tens of seconds and happens in Python and Tesseract, so
    running it in the event loop stopped the whole service — health checks
    included — for the length of one upload. The work goes to a worker thread;
    the gate keeps at most `settings.EXTRACTIONS` of them running at once.
    """
    acquired = await asyncio.to_thread(GATE.acquire, settings.EXTRACTION_WAIT_SECONDS)
    if not acquired:
        raise ExtractionBusy(
            f'all {settings.EXTRACTIONS} extraction slot(s) are busy'
        )
    try:
        return await asyncio.to_thread(
            _process, doc_id, filename, path, source=source, extra=extra, persist=persist
        )
    finally:
        GATE.release()


def _process_guarded(*args, **kwargs) -> dict:
    """The same gate for a caller that is already on a worker thread."""
    if not GATE.acquire(settings.EXTRACTION_WAIT_SECONDS):
        raise ExtractionBusy(f'all {settings.EXTRACTIONS} extraction slot(s) are busy')
    try:
        return _process(*args, **kwargs)
    finally:
        GATE.release()


def _ingest_folder_once() -> list[dict]:
    """Pick up documents dropped into the intake folder.

    A file younger than two seconds is skipped: a copy that is still being
    written must not be parsed half-done. Handled files are moved aside so a
    restart does not process them twice.
    """
    handled: list[dict] = []
    now = time.time()
    for path in sorted(settings.INBOX.iterdir()):
        if not path.is_file() or path.suffix.lower() not in settings.ALLOWED:
            continue
        try:
            if now - path.stat().st_mtime < 2:
                continue
            problem = _document_problem(path)
            if problem:
                # Left where it is: the folder is watched, not queued, and a
                # skipped file costs one stat per pass, not a retry that copies it.
                logger.warning('intake skipped: %s', problem)
                continue
            doc_id = uuid.uuid4().hex[:10]
            target = settings.UPLOADS / f'{doc_id}{path.suffix.lower()}'
            shutil.copyfile(path, target)
            _process_guarded(doc_id, path.name, target, source='folder')
            path.replace(settings.INBOX_DONE / f'{path.stem}-{doc_id}{path.suffix.lower()}')
            handled.append({'id': doc_id, 'filename': path.name})
        except Exception:
            # A broken file must not stop the folder from being watched.
            continue
    return handled


async def _folder_watcher() -> None:
    while True:
        # A broken folder must not stop the watch: the next pass tries again.
        with suppress(Exception):
            # Parsing a scan is seconds of CPU: the watch keeps running, the
            # event loop does not wait for it.
            await asyncio.to_thread(_ingest_folder_once)
        await asyncio.sleep(3)


def _mailbox_poll_once() -> dict:
    """Take the attachments of new mail through the same path as an upload.

    A document that arrives by e-mail is not special: it is recognised, put in
    the review queue and routed exactly like a dropped file. The only difference
    is the provenance kept in the meta file.
    """

    def handler(attachment: dict, message: dict):
        suffix = Path(attachment['filename']).suffix.lower()
        if suffix not in settings.ALLOWED:
            return None
        if len(attachment['data']) > settings.MAX_UPLOAD_BYTES:
            # Ignored on purpose, not retried: a message that can never be
            # ingested would otherwise come back on every poll.
            logger.warning('mailbox skipped: attachment over %g MB', settings.MAX_UPLOAD_MB)
            return None
        doc_id = uuid.uuid4().hex[:10]
        target = settings.UPLOADS / f'{doc_id}{suffix}'
        target.write_bytes(attachment['data'])
        problem = _document_problem(target)
        if problem:
            target.unlink(missing_ok=True)
            logger.warning('mailbox skipped: %s', problem)
            return None
        _process(
            doc_id,
            attachment['filename'],
            target,
            source='mailbox',
            extra={
                'email': {
                    'from': message.get('from'),
                    'subject': message.get('subject'),
                    'date': message.get('date'),
                    'message_id': message.get('message_id'),
                }
            },
        )
        return True

    return mailbox.poll(handler)


async def _mailbox_worker() -> None:
    # The IMAP client is blocking; keep it out of the event loop.
    while True:
        with suppress(Exception):
            await asyncio.to_thread(_mailbox_poll_once)
        await asyncio.sleep(mailbox.settings()['interval'])
