"""Deterministic checks for Uspacy entity matching. No network involved.

The live API ignores search/filter parameters, so matching is client-side; these
tests pin the behaviour that prevents a confident wrong company or a false
duplicate from blocking a document.

Run: python3 tests/test_uspacy_match.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uspacy  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


COMPANIES = [
    {'id': 1, 'title': 'Test Company LLC', 'company_name': 'Test Company LLC', 'comments': ''},
    {
        'id': 2,
        'title': 'ТОВ «Чистий дім»',
        'company_name': 'ТОВ «Чистий дім»',
        'comments': 'ЄДРПОУ/ІПН: 23456789',
    },
]

DEALS = [
    {
        'id': 9,
        'title': 'Рахунок №2026-455 — ТОВ «Інший»',
        'comments': 'Imported by DocFlow. Tax ID: 23456789.',
    },
    {
        'id': 10,
        'title': 'Рахунок №55 — ТОВ «Чистий дім»',
        'comments': 'Imported by DocFlow. Tax ID: 23456789.',
    },
]


def serve(companies=None, deals=None):
    def fake_get(path, params=None):
        data = (companies if 'companies' in path else deals) or []
        return FakeResponse({'data': data, 'meta': {'meta': {'last_page': 1, 'current_page': 1}}})

    uspacy._get = fake_get


def test_company_found_by_tax_id_in_comments():
    serve(COMPANIES)
    assert (uspacy.find_company_by_tax_id('23456789') or {}).get('id') == 2


def test_unknown_tax_id_returns_none():
    serve(COMPANIES)
    assert uspacy.find_company_by_tax_id('99999999') is None


def test_short_tax_id_is_ignored():
    serve(COMPANIES)
    assert uspacy.find_company_by_tax_id('2') is None


def test_company_found_ignoring_quotes_and_case():
    serve(COMPANIES)
    assert (uspacy.find_company_by_name('ТОВ Чистий дім') or {}).get('id') == 2


def test_unknown_company_is_none_not_the_first_row():
    # Regression: the API returns every company, so a blind "data[0]" linked the
    # supplier of every invoice to whatever company happened to come first.
    serve(COMPANIES)
    assert uspacy.find_company_by_name('ТОВ «Немає такого»') is None


def test_company_legal_form_variant_matches():
    serve(COMPANIES)
    assert (uspacy.find_company_by_name('Чистий дім') or {}).get('id') == 2


def test_invoice_number_matches_as_whole_token():
    serve(deals=DEALS)
    assert (uspacy.find_deal_by_invoice('55', '23456789') or {}).get('id') == 10


def test_invoice_number_is_not_a_substring():
    serve(deals=DEALS)
    # "55" must not match "2026-455"; that would block a real invoice as a duplicate.
    assert uspacy.find_deal_by_invoice('2026-455') .get('id') == 9
    assert uspacy.find_deal_by_invoice('2026-441', '23456789') is None


def test_duplicate_requires_matching_supplier():
    serve(deals=DEALS)
    assert uspacy.find_deal_by_invoice('55', '99999999') is None


def test_number_only_lookup_still_works():
    serve(deals=DEALS)
    assert (uspacy.find_deal_by_invoice('55') or {}).get('id') == 10


def test_paging_reads_every_page():
    calls = []

    def fake_get(path, params=None):
        page = (params or {}).get('page', 1)
        calls.append(page)
        pages = {
            1: [{'id': 1}],
            2: [{'id': 2}],
            3: [{'id': 3}],
        }
        return FakeResponse(
            {'data': pages.get(page, []), 'meta': {'meta': {'last_page': 3, 'current_page': page}}}
        )

    uspacy._get = fake_get
    assert uspacy._page_through('/crm/v1/entities/deals') == [
        {'id': 1},
        {'id': 2},
        {'id': 3},
    ]
    assert calls == [1, 2, 3]


def test_paging_stops_when_a_page_is_empty():
    calls = []

    def fake_get(path, params=None):
        page = (params or {}).get('page', 1)
        calls.append(page)
        return FakeResponse({'data': [{'id': 1}] if page == 1 else [], 'meta': {'meta': {}}})

    uspacy._get = fake_get
    assert uspacy._page_through('/crm/v1/entities/deals') == [{'id': 1}]
    assert calls == [1, 2]


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('uspacy matching checks passed')


if __name__ == '__main__':
    main()
