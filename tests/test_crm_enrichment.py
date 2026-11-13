"""Deterministic checks for CRM enrichment. No network involved.

Covers the mapping from invoice fields to the Uspacy company card and the
contact record: only empty fields are filled, existing values are never
overwritten, and a contact is reused instead of duplicated.

Run: python3 tests/test_crm_enrichment.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
import uspacy  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


FIELDS = {
    'supplier': 'ТОВ «Терра-Буд»',
    'tax_id': '33445566',
    'address': 'м. Київ, вул. Хрещатик, 12',
    'phone': '380441112233',
    'email': 'billing@terra-bud.ua',
    'bank': 'АТ «Ощадбанк»',
    'iban': 'UA123456789012345678901234567',
    'contact_person': 'Олена Ткаченко',
}


def test_supplier_details_maps_requisites():
    details = app._supplier_details(FIELDS)
    assert details['tax_id'] == '33445566'
    assert details['phone'] == '380441112233'
    assert details['email'] == 'billing@terra-bud.ua'
    assert details['address'] == 'м. Київ, вул. Хрещатик, 12'
    assert 'ЄДРПОУ/ІПН: 33445566' in details['comment_lines']
    assert 'IBAN: UA123456789012345678901234567' in details['comment_lines']
    assert 'Банк: АТ «Ощадбанк»' in details['comment_lines']


def test_supplier_details_skips_empty_values():
    details = app._supplier_details({'supplier': 'ТОВ «Без реквізитів»'})
    assert details['comment_lines'] == []
    assert details['phone'] is None and details['iban'] is None


def test_enrich_company_fills_only_empty_fields():
    patched = {}

    def fake_patch(path, payload):
        patched['path'] = path
        patched['payload'] = payload
        return FakeResponse({'id': 5, **payload})

    uspacy._patch = fake_patch
    company = {'id': 5, 'title': 'ТОВ «Терра-Буд»', 'phone': '', 'email': '', 'comments': 'ЄДРПОУ/ІПН: 33445566'}
    uspacy.enrich_company(company, app._supplier_details(FIELDS))
    assert patched['payload']['phone'] == [{'value': '380441112233'}]
    assert patched['payload']['email'] == [{'value': 'billing@terra-bud.ua'}]
    assert patched['payload']['address'] == 'м. Київ, вул. Хрещатик, 12'
    # The tax ID is already in the comment, so it must not be duplicated.
    assert patched['payload']['comments'].count('ЄДРПОУ/ІПН') == 1
    assert 'IBAN: UA123456789012345678901234567' in patched['payload']['comments']


def test_enrich_company_does_not_overwrite_human_values():
    patched = {}

    def fake_patch(path, payload):
        patched['payload'] = payload
        return FakeResponse({'id': 5, **payload})

    uspacy._patch = fake_patch
    company = {
        'id': 5,
        'title': 'ТОВ «Терра-Буд»',
        'phone': [{'value': '380500000000'}],
        'email': [{'value': 'sales@terra-bud.ua'}],
        'address': 'м. Київ, вул. Інша, 1',
        'comments': 'ЄДРПОУ/ІПН: 33445566',
    }
    uspacy.enrich_company(company, app._supplier_details(FIELDS))
    assert 'phone' not in patched['payload']
    assert 'email' not in patched['payload']
    assert 'address' not in patched['payload']


def test_enrich_company_is_noop_when_nothing_new():
    def fake_patch(path, payload):
        raise AssertionError('must not patch')

    uspacy._patch = fake_patch
    company = {
        'id': 5,
        'title': 'ТОВ «Терра-Буд»',
        'phone': [{'value': '380441112233'}],
        'email': [{'value': 'billing@terra-bud.ua'}],
        'address': 'м. Київ, вул. Хрещатик, 12',
        'comments': 'ЄДРПОУ/ІПН: 33445566\nIBAN: UA123456789012345678901234567\nБанк: АТ «Ощадбанк»',
    }
    assert uspacy.enrich_company(company, app._supplier_details(FIELDS)) is company


def test_existing_contact_is_reused():
    posted = []

    def fake_page(path):
        return [{'id': 7, 'title': 'Олена Ткаченко', 'companies': [{'id': 5, 'title': 'ТОВ «Терра-Буд»'}]}]

    def fake_post(path, payload):
        posted.append(payload)
        return FakeResponse({'id': 99})

    uspacy._page_through = fake_page
    uspacy._post = fake_post
    contact = uspacy.find_or_create_contact('Олена Ткаченко', 5)
    assert contact['id'] == 7
    assert posted == []


def test_contact_from_another_company_is_not_reused():
    posted = []

    def fake_page(path):
        return [{'id': 7, 'title': 'Олена Ткаченко', 'companies': [{'id': 6, 'title': 'Інша компанія'}]}]

    def fake_post(path, payload):
        posted.append(payload)
        return FakeResponse({'id': 99, 'title': payload['title']})

    uspacy._page_through = fake_page
    uspacy._post = fake_post
    contact = uspacy.find_or_create_contact('Олена Ткаченко', 5, phone='380441112233', email='billing@terra-bud.ua')
    assert contact['id'] == 99
    assert posted[0]['companies'] == [5]
    assert posted[0]['phone'] == [{'value': '380441112233'}]
    assert posted[0]['email'] == [{'value': 'billing@terra-bud.ua'}]
    assert 'first_name' not in posted[0], 'name must not be split by guesswork'


def test_company_lookup_error_is_not_swallowed():
    # Regression: a failed enrichment used to be swallowed, so the deal looked
    # complete while the company card stayed empty.
    def boom(*args, **kwargs):
        raise RuntimeError('CRM is down')

    uspacy.find_or_create_company = boom
    try:
        app._find_or_create_supplier_company({'supplier': 'ТОВ «Терра-Буд»'})
    except RuntimeError as exc:
        assert 'CRM is down' in str(exc)
    else:
        raise AssertionError('error must propagate to the confirm response')


def test_missing_form_keys_fall_back_to_parsed_values():
    # Regression: a cached UI without the requisites inputs sent only the legacy
    # keys, and the parsed address/phone/contact disappeared from the record.
    parsed = {
        'number': '2026-501',
        'supplier': 'ТОВ «Терра-Буд»',
        'phone': '380441112233',
        'contact_person': 'Олена Ткаченко',
    }
    submitted = {'number': '2026-501', 'supplier': 'ТОВ «Терра-Буд»'}
    merged = app._effective_fields(parsed, submitted)
    assert merged['phone'] == '380441112233'
    assert merged['contact_person'] == 'Олена Ткаченко'


def test_edited_value_wins_over_parsed_value():
    merged = app._effective_fields({'total': 100.0, 'phone': '380441112233'}, {'total': 90.0})
    assert merged['total'] == 90.0


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('crm enrichment checks passed')


if __name__ == '__main__':
    main()
