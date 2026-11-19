"""State that one suite writes must not reach the next suite.

Each file under `tests/` was written to run alone (`python3 tests/test_x.py`):
it redirects the storage paths to a temporary folder, swaps a network call for a
fake, or resizes the pool, and the process exits before anyone notices what it
left behind. `pytest` runs all of them in one process, where "nobody noticed"
becomes a failure three files later (a pool stuck at one connection, a fake
`uspacy` still installed).

This fixture snapshots the mutable module state before every test and puts it
back afterwards. It is deliberately narrow: the settings knobs files are allowed
to redirect, the `uspacy` callables tests replace, and the pool size. Everything
else is expected to be restored by the test that changed it, with `try/finally`.
"""

import pytest

import db
import settings
import uspacy

SETTINGS_KNOBS = (
    'UPLOADS',
    'PENDING',
    'INBOX',
    'INBOX_DONE',
    'CONFIRMED',
    'DEMO_MODE',
    'MAX_UPLOAD_BYTES',
    'MAX_PDF_PAGES',
    'MAX_OCR_PAGES',
    'MAX_OCR_SECONDS',
    'EXTRACTIONS',
    'EXTRACTION_WAIT_SECONDS',
    'MANUAL_BASELINE_MINUTES',
)

USPACY_CALLABLES = ('_patch', '_post', '_page_through', 'find_or_create_company')


@pytest.fixture(autouse=True)
def _restore_module_state():
    knobs = {name: getattr(settings, name) for name in SETTINGS_KNOBS}
    callables = {name: getattr(uspacy, name) for name in USPACY_CALLABLES}
    pool_size = (db.POOL_MIN, db.POOL_MAX)
    try:
        yield
    finally:
        for name, value in knobs.items():
            setattr(settings, name, value)
        for name, value in callables.items():
            setattr(uspacy, name, value)
        db.POOL_MIN, db.POOL_MAX = pool_size
        # A pool built with a test's size would keep that size for every suite
        # after it; the next test opens its own from the restored knobs.
        db.close_pool()


def pytest_collection_modifyitems(items):
    """Suites that need PostgreSQL skip as a whole, not test by test.

    They already return early when the database is unreachable, which the
    old per-file runner showed as a silent pass. A skip makes the missing
    database visible in the summary instead of hiding it.
    """
    for item in items:
        module = getattr(item, 'module', None)
        if module is None:
            continue
        available = getattr(module, 'POSTGRES_AVAILABLE', None)
        if available is False:
            item.add_marker(pytest.mark.skip(reason='PostgreSQL is not reachable'))
