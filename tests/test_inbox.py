"""Deterministic checks for the review inbox. No network involved.

The inbox shows uploaded documents that were not confirmed yet; a confirmation
leaves the pending snapshot on disk, so the history is what separates "waiting"
from "already saved".

Run: python3 tests/test_inbox.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def meta(doc_id, *, filename, uploaded_at, issues=None, supplier='ТОВ «Тест»', number='1'):
    return {
        'id': doc_id,
        'filename': filename,
        'uploaded_at': uploaded_at,
        'suffix': '.pdf',
        'source': 'upload',
        'parsed': {
            'confidence': 0.8,
            'issues': issues or [],
            'fields': {
                'supplier': {'value': supplier, 'confidence': 0.85},
                'number': {'value': number, 'confidence': 0.95},
                'total': {'value': 100.0, 'confidence': 0.9},
                'currency': {'value': 'UAH', 'confidence': 0.6},
            },
        },
    }


def with_storage(cases, confirmed_rows):
    """Run a check against a temporary pending/confirmed store."""
    pending = Path(tempfile.mkdtemp())
    history = pending / 'confirmed.jsonl'
    for item in cases:
        (pending / f'{item["id"]}.json').write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    history.write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in confirmed_rows),
        encoding='utf-8',
    )
    app.PENDING = pending
    app.CONFIRMED = history
    return app._pending_documents()


def test_unconfirmed_documents_are_listed():
    items = with_storage(
        [meta('a1', filename='new.pdf', uploaded_at='2026-11-14T10:00:00+00:00')],
        [],
    )
    assert [item['id'] for item in items] == ['a1']
    assert items[0]['fields']['supplier'] == 'ТОВ «Тест»'


def test_confirmed_documents_leave_the_inbox():
    items = with_storage(
        [meta('a1', filename='new.pdf', uploaded_at='2026-11-14T10:00:00+00:00')],
        [{'id': 'a1', 'filename': 'new.pdf'}],
    )
    assert items == []


def test_newest_document_comes_first():
    items = with_storage(
        [
            meta('old', filename='old.pdf', uploaded_at='2026-11-13T10:00:00+00:00'),
            meta('new', filename='new.pdf', uploaded_at='2026-11-14T10:00:00+00:00'),
        ],
        [],
    )
    assert [item['id'] for item in items] == ['new', 'old']


def test_validation_issues_are_carried_for_the_badge():
    items = with_storage(
        [
            meta(
                'bad',
                filename='bad.pdf',
                uploaded_at='2026-11-14T10:00:00+00:00',
                issues=['vat: invariant failed'],
            )
        ],
        [],
    )
    assert items[0]['issues'] == ['vat: invariant failed']


def test_broken_snapshot_is_skipped():
    pending = Path(tempfile.mkdtemp())
    (pending / 'broken.json').write_text('{not json', encoding='utf-8')
    (pending / 'good.json').write_text(
        json.dumps(meta('good', filename='good.pdf', uploaded_at='2026-11-14T10:00:00+00:00')),
        encoding='utf-8',
    )
    history = pending / 'confirmed.jsonl'
    history.write_text('', encoding='utf-8')
    app.PENDING = pending
    app.CONFIRMED = history
    assert [item['id'] for item in app._pending_documents()] == ['good']


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('inbox checks passed')


if __name__ == '__main__':
    main()
