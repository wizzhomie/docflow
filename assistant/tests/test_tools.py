"""Tool tests against a fake DocFlow, so they run without the live service."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow_operator.tools import (
    ArgumentError,
    Toolbox,
    ToolError,
    result_text,
    validate_arguments,
)


class FakeDocFlow:
    def __init__(self, inbox: list[dict], documents: list[dict], extra: dict | None = None):
        self._inbox = inbox
        self._documents = documents
        self._extra = extra or {}

    def inbox(self, limit: int = 20) -> list[dict]:
        return self._inbox[:limit]

    def documents(self, limit: int = 50) -> list[dict]:
        return self._documents[:limit]

    def document(self, doc_id: str) -> dict:
        for row in self._inbox + self._documents:
            if row.get('id') == doc_id:
                return row
        if doc_id in self._extra:
            return self._extra[doc_id]
        raise ToolError('not found')

    def health(self) -> dict:
        return {'status': 'ok'}


def pending(doc_id: str, number: str, supplier: str, total: float, route: str = 'Payments',
            route_code: str = 'payments', date: str = '2026-11-02') -> dict:
    return {
        'id': doc_id,
        'filename': f'{doc_id}.pdf',
        'uploaded_at': '2026-11-15T09:00:00+00:00',
        'source': 'sample',
        'confidence': 0.9,
        'doc_type': 'Ukrainian invoice (Рахунок на оплату)',
        'issues': [],
        'route': {'destination': route, 'destination_code': route_code, 'reason': f'goes to {route}'},
        'fields': {'number': number, 'supplier': supplier, 'date': date, 'total': total,
                   'currency': 'UAH', 'tax_id': '33445566'},
    }


def confirmed(doc_id: str, number: str, supplier: str, total: float) -> dict:
    return {
        'id': doc_id,
        'filename': f'{doc_id}.pdf',
        'confirmed_at': '2026-11-10T09:00:00+00:00',
        'fields': {'number': number, 'supplier': supplier, 'date': '2026-11-02', 'total': total},
        'destination': 'Payments',
        'destination_code': 'payments',
    }


@pytest.fixture()
def inbox() -> list[dict]:
    return [
        pending('aaaaaaaaaa', '2026-501', 'ТОВ «Терра-Буд»', 128000.0),
        pending('bbbbbbbbbb', '2026-503', 'ТОВ «Агро-Південь»', 99300.0, route='Accounting', route_code='accounting'),
    ]


@pytest.fixture()
def history() -> list[dict]:
    return [
        confirmed('cccccccccc', '2026-501', 'ТОВ «Терра-Буд»', 128000.0),
        confirmed('dddddddddd', '2026-400', 'ТОВ «Інший»', 5000.0),
    ]


@pytest.fixture()
def toolbox(inbox, history, tmp_path: Path) -> Toolbox:
    box = Toolbox(FakeDocFlow(inbox, history), tmp_path / 'drafts')
    return box


def test_list_queue_returns_briefs(toolbox):
    result = toolbox.run('list_queue', {'limit': 5})
    assert result['count'] == 2
    assert result['routes'] == {'Payments': 1, 'Accounting': 1}
    assert result['documents'][0]['supplier'] == 'ТОВ «Терра-Буд»'


def test_get_document_known_and_unknown(toolbox):
    result = toolbox.run('get_document', {'doc_id': 'aaaaaaaaaa'})
    assert result['document']['number'] == '2026-501'
    assert result['document']['route'] == 'Payments'
    with pytest.raises(ToolError):
        toolbox.run('get_document', {'doc_id': 'ffffffffff'})


def test_check_duplicate_found(toolbox):
    result = toolbox.run('check_duplicate', {'doc_id': 'aaaaaaaaaa'})
    assert result['duplicate'] is True
    assert [m['id'] for m in result['matches']] == ['cccccccccc']
    assert result['checked_against'] == 2


def test_check_duplicate_not_found(toolbox):
    result = toolbox.run('check_duplicate', {'doc_id': 'bbbbbbbbbb'})
    assert result['duplicate'] is False
    assert result['matches'] == []


def test_summarise_counts_routes(toolbox):
    result = toolbox.run('summarise', {'period': 'all'})
    assert result['waiting']['count'] == 2
    assert result['waiting']['to_pay'] == 1
    assert result['waiting']['to_book'] == 1
    assert result['confirmed']['count'] == 2


def test_draft_letter_writes_file_only(toolbox):
    result = toolbox.run('draft_letter', {'doc_id': 'aaaaaaaaaa', 'kind': 'payment_request'})
    path = Path(result['path'])
    assert path.exists()
    text = path.read_text(encoding='utf-8')
    assert '2026-501' in text and '128,000.00' in text
    assert 'not by this assistant' in text


def test_propose_confirm_never_confirms(toolbox):
    result = toolbox.run('propose_confirm', {'doc_id': 'aaaaaaaaaa'})
    assert result['confirmed'] is False
    assert 'person' in result['human_action'].lower()
    assert 'cannot confirm' in result['human_action'].lower()


def test_arguments_are_validated():
    with pytest.raises(ArgumentError):
        validate_arguments('list_queue', {})
    with pytest.raises(ArgumentError):
        validate_arguments('list_queue', {'limit': 0})
    with pytest.raises(ArgumentError):
        validate_arguments('list_queue', {'limit': 5, 'offset': 2})
    with pytest.raises(ArgumentError):
        validate_arguments('draft_letter', {'doc_id': 'x', 'kind': 'love_letter'})
    with pytest.raises(ArgumentError):
        validate_arguments('make_coffee', {})
    validate_arguments('list_queue', {'limit': 5})


def test_latest_selector_avoids_invented_ids(toolbox):
    result = toolbox.run('get_document', {'doc_id': 'latest'})
    assert result['document']['id'] == 'aaaaaaaaaa'
    result = toolbox.run('propose_confirm', {'doc_id': 'latest'})
    assert result['doc_id'] == 'aaaaaaaaaa'


def test_summarise_today_counts_the_whole_current_queue(toolbox):
    """A document uploaded before today is still waiting for review today."""
    result = toolbox.run('summarise', {'period': 'today'})
    assert result['waiting']['count'] == 2
    assert result['confirmed']['count'] == 0  # history is period-filtered: those confirmations are older


# --- queue counting, selectors and route flags ------------------------------

def test_queue_count_reports_the_whole_queue_not_the_five_briefs():
    inbox = [pending(f'{index:010x}', f'2026-{index}', f'Supplier {index}', 100.0 + index)
             for index in range(6)]
    box = Toolbox(FakeDocFlow(inbox, []), Path('/tmp/drafts-unused'))
    result = box.run('list_queue', {'limit': 10})
    assert result['count'] == 6
    assert len(result['documents']) == 5  # the prompt gets a short list, the count stays true
    assert '6 document(s)' in result['summary']


def test_queue_route_flags_split_the_queue():
    inbox = [
        pending('aaaaaaaaaa', '1', 'A', 128000.0, route='Approval', route_code='approval'),
        pending('bbbbbbbbbb', '2', 'B', 1000.0),
        pending('cccccccccc', '3', 'C', 2000.0, route='Accounting', route_code='accounting'),
    ]
    box = Toolbox(FakeDocFlow(inbox, []), Path('/tmp/drafts-unused'))
    result = box.run('list_queue', {'limit': 5})
    assert result['needs_approval'] == ['aaaaaaaaaa']
    assert result['to_pay'] == ['bbbbbbbbbb']
    assert result['to_book'] == ['cccccccccc']


@pytest.mark.parametrize('selector', ['latest', 'first', 'newest', 'next', 'LATEST'])
def test_every_latest_selector_resolves_to_the_newest_document(selector):
    box = Toolbox(FakeDocFlow([pending('aaaaaaaaaa', '1', 'A', 10.0)], []), Path('/tmp/drafts-unused'))
    assert box.run('get_document', {'doc_id': selector})['document']['id'] == 'aaaaaaaaaa'


def test_an_empty_queue_is_explained_to_the_model():
    box = Toolbox(FakeDocFlow([], []), Path('/tmp/drafts-unused'))
    with pytest.raises(ToolError, match='review queue is empty'):
        box.run('check_duplicate', {'doc_id': 'latest'})


def test_an_unknown_id_says_where_to_look():
    box = Toolbox(FakeDocFlow([pending('aaaaaaaaaa', '1', 'A', 10.0)], []), Path('/tmp/drafts-unused'))
    with pytest.raises(ToolError, match='not in the review queue'):
        box.run('check_duplicate', {'doc_id': 'ffffffffff'})


def test_a_confirmed_document_can_still_be_read_by_id():
    history = [confirmed('cccccccccc', '2026-500', 'ТОВ «Старий»', 700.0)]
    box = Toolbox(FakeDocFlow([], history), Path('/tmp/drafts-unused'))
    result = box.run('get_document', {'doc_id': 'cccccccccc'})
    assert result['document']['number'] == '2026-500'


# --- full document brief ----------------------------------------------------

def test_document_brief_exposes_issues_and_reason():
    inbox = [pending('aaaaaaaaaa', '2026-501', 'ТОВ «Терра-Буд»', 128000.0)]
    inbox[0]['issues'] = ['amounts do not add up', 'missing tax id', 'low OCR', 'extra']
    box = Toolbox(FakeDocFlow(inbox, []), Path('/tmp/drafts-unused'))
    document = box.run('get_document', {'doc_id': 'aaaaaaaaaa'})['document']
    assert document['type'] == 'Ukrainian invoice (Рахунок на оплату)'
    assert document['confidence'] == 0.9
    assert document['source'] == 'sample'
    assert document['issues'] == ['amounts do not add up', 'missing tax id', 'low OCR']
    assert document['route_reason'] == 'goes to Payments'


def test_fields_wrapped_with_confidence_are_unwrapped():
    wrapped = {
        'id': 'eeeeeeeeee',
        'doc_type': 'invoice',
        'route': {'destination': 'Payments', 'destination_code': 'payments'},
        'fields': {'number': {'value': '2026-777', 'confidence': 0.42},
                   'supplier': {'value': 'Wrapped Supplier', 'confidence': 0.7},
                   'total': {'value': 15.0, 'confidence': 0.9},
                   'currency': 'UAH'},
    }
    box = Toolbox(FakeDocFlow([], [], extra={'eeeeeeeeee': wrapped}), Path('/tmp/drafts-unused'))
    document = box.run('get_document', {'doc_id': 'eeeeeeeeee'})['document']
    assert document['number'] == '2026-777'
    assert document['supplier'] == 'Wrapped Supplier'
    assert document['total'] == 15.0


# --- duplicate matching rules ----------------------------------------------

def test_duplicate_by_same_supplier_and_date():
    inbox = [pending('aaaaaaaaaa', '2026-999', 'ТОВ «Терра-Буд»', 5.0, date='2026-11-02')]
    history = [confirmed('cccccccccc', '2026-501', 'ТОВ «Терра-Буд»', 999.0)]
    # the helper stores date 2026-11-02 as well: same supplier and date, different number/amount
    box = Toolbox(FakeDocFlow(inbox, history), Path('/tmp/drafts-unused'))
    result = box.run('check_duplicate', {'doc_id': 'aaaaaaaaaa'})
    assert result['duplicate'] is True
    assert result['matches'][0]['reason'] == 'same supplier and date/amount'


def test_duplicate_by_same_tax_id_and_number():
    inbox = [pending('aaaaaaaaaa', '2026-501', 'ТОВ «Терра-Буд»', 128000.0)]
    history = [confirmed('cccccccccc', '2026-501', 'Інший Постачальник', 128000.0)]
    history[0]['fields']['tax_id'] = '33445566'
    box = Toolbox(FakeDocFlow(inbox, history), Path('/tmp/drafts-unused'))
    assert box.run('check_duplicate', {'doc_id': 'aaaaaaaaaa'})['duplicate'] is True


def test_duplicate_check_with_empty_history():
    box = Toolbox(FakeDocFlow([pending('aaaaaaaaaa', '1', 'A', 10.0)], []), Path('/tmp/drafts-unused'))
    result = box.run('check_duplicate', {'doc_id': 'aaaaaaaaaa'})
    assert result['duplicate'] is False
    assert result['checked_against'] == 0


# --- summarise: only confirmed history is period-filtered -------------------

def _confirmed_at(days_ago: int) -> str:
    from datetime import datetime, timedelta, timezone
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.isoformat()


def test_week_summary_filters_history_but_not_the_queue():
    inbox = [pending('aaaaaaaaaa', '1', 'A', 100.0)]
    history = [
        {'id': 'cccccccccc', 'confirmed_at': _confirmed_at(2), 'fields': {'total': 50.0}},
        {'id': 'dddddddddd', 'confirmed_at': _confirmed_at(30), 'fields': {'total': 70.0}},
        {'id': 'eeeeeeeeee', 'confirmed_at': 'not a timestamp', 'fields': {'total': 10.0}},
    ]
    box = Toolbox(FakeDocFlow(inbox, history), Path('/tmp/drafts-unused'))
    result = box.run('summarise', {'period': 'week'})
    assert result['waiting']['count'] == 1  # the queue is "now", not "this week"
    assert result['confirmed']['count'] == 2  # recent + unparsable (treated as now), old one excluded
    assert result['confirmed']['amount'] == 60.0


def test_summarise_rejects_an_unknown_period():
    box = Toolbox(FakeDocFlow([], []), Path('/tmp/drafts-unused'))
    with pytest.raises(ArgumentError):
        box.run('summarise', {'period': 'year'})  # schema enum stops it first
    with pytest.raises(ArgumentError, match='unknown period'):
        box.summarise('year')


# --- letters: three kinds, local files only --------------------------------

@pytest.mark.parametrize('kind,phrase', [
    ('payment_request', 'Please prepare a payment'),
    ('approval_request', 'needs approval'),
    ('supplier_reply', 'We received your document'),
])
def test_every_letter_kind_is_written_locally(kind, phrase, tmp_path):
    inbox = [pending('aaaaaaaaaa', '2026-501', 'ТОВ «Терра-Буд»', 128000.0)]
    box = Toolbox(FakeDocFlow(inbox, []), tmp_path / 'drafts')
    result = box.run('draft_letter', {'doc_id': 'aaaaaaaaaa', 'kind': kind})
    path = Path(result['path'])
    assert path.parent == tmp_path / 'drafts'
    assert path.suffix == '.md'
    assert phrase in path.read_text(encoding='utf-8')
    assert result['letter'] == path.read_text(encoding='utf-8')


def test_an_unknown_letter_kind_is_rejected_before_writing():
    box = Toolbox(FakeDocFlow([], []), Path('/tmp/drafts-unused'))
    with pytest.raises(ArgumentError):
        box.run('draft_letter', {'doc_id': 'x', 'kind': 'love_letter'})  # schema enum
    with pytest.raises(ArgumentError, match='unknown letter kind'):
        box.draft_letter('x', 'love_letter')


# --- compact results for the model -----------------------------------------

def test_result_text_keeps_the_letter_preview_but_not_the_letter():
    result = {'path': '/tmp/a.md', 'letter': 'x' * 500, 'kind': 'payment_request'}
    text = result_text(result)
    assert 'letter_preview' in text
    assert '"letter":' not in text
    assert len(text) <= 700


def test_result_text_truncates_long_payloads():
    text = result_text({'documents': [{'id': 'x' * 200}] * 10})
    assert len(text) <= 703  # the cap plus the ellipsis
    assert text.endswith('...')


def test_result_text_serialises_dates():
    text = result_text({'when': __import__('datetime').date(2026, 11, 17)})
    assert '2026-11-17' in text


def test_slug_is_lowercase_ascii_with_dashes():
    from docflow_operator.tools import slug
    assert slug('Invoice 2026/501 — ТОВ') == 'invoice-2026-501'
    assert slug('') == ''


# --- schema, prompt and validation consistency -----------------------------

def test_step_schema_and_tool_catalogue_cannot_drift():
    from docflow_operator.tools import FINISH_TOOL, TOOLS, step_schema, tool_prompt, tools_by_name
    schema = step_schema()
    assert len(schema['oneOf']) == len(TOOLS) + 1
    catalogue = tools_by_name()
    assert set(catalogue) == {tool['name'] for tool in TOOLS}
    for variant in schema['oneOf']:
        name = variant['properties']['tool']['const']
        if name == FINISH_TOOL['name']:
            continue
        tool = catalogue[name]
        assert set(variant['properties']) == {'tool', *tool['args']['properties']}
        assert variant['required'] == ['tool', *tool['args'].get('required', [])]
        assert variant['additionalProperties'] is False
    prompt = tool_prompt()
    for tool in TOOLS:
        assert tool['name'] in prompt
    assert FINISH_TOOL['name'] in prompt


@pytest.mark.parametrize('tool,arguments', [
    ('list_queue', {'limit': True}),            # bool is not an integer
    ('list_queue', {'limit': 51}),
    ('list_queue', {'limit': 1}),               # valid
    ('get_document', {'doc_id': '   '}),
    ('draft_letter', {'doc_id': 'x', 'kind': 'payment_request'}),  # valid
])
def test_argument_types_and_bounds(tool, arguments):
    valid = (tool == 'list_queue' and type(arguments.get('limit')) is int
             and arguments.get('limit') == 1) or \
        (tool == 'draft_letter' and arguments.get('kind') == 'payment_request')
    if valid:
        validate_arguments(tool, arguments)
    else:
        with pytest.raises(ArgumentError):
            validate_arguments(tool, arguments)


def test_arguments_must_be_a_json_object():
    with pytest.raises(ArgumentError, match='must be a JSON object'):
        validate_arguments('list_queue', ['limit', 5])


def test_finish_is_not_a_toolbox_tool():
    # finish is the agent loop's own step, not something the toolbox executes
    box = Toolbox(FakeDocFlow([], []), Path('/tmp/drafts-unused'))
    with pytest.raises(ArgumentError, match='unknown tool'):
        box.run('finish', {'answer': 'done'})
