"""Invariants for the profile passport: the core stays market-neutral.

A document profile is the only place that knows about a market. These checks pin
that contract, because the alternative — one country quietly hardcoded in the
parser, the UI labels and the CRM comment — is exactly the debt this registry was
introduced to remove.

Run: python3 tests/test_profiles.py
"""

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import profiles  # noqa: E402
from extractor import parse_invoice  # noqa: E402
from profiles import act_ua, invoice_ua  # noqa: E402

CYRILLIC = re.compile(r'[\u0400-\u04FF]')

# The core: everything that must not know which country's documents it reads.
CORE_FILES = [
    ROOT / 'app.py',
    ROOT / 'extractor.py',
    ROOT / 'validation.py',
    ROOT / 'routing.py',
    ROOT / 'mailbox.py',
    ROOT / 'uspacy.py',
    *sorted((ROOT / 'connectors').glob('*.py')),
]

INVOICE_TEXT = (
    'Рахунок на оплату № 55 від 10.11.2026\n'
    'Постачальник: ТОВ «Чистий дім», ЄДРПОУ 23456789\n'
    'Сума до сплати: 150 000,00 грн\n'
)


def _code_strings(path: Path):
    """String literals of a module, excluding docstrings (examples may quote a document)."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and CYRILLIC.search(node.value)
        ):
            yield node.value


def test_catalog_lists_both_profiles():
    catalog = profiles.catalog()
    codes = {item['code'] for item in catalog}
    assert codes == {'invoice_ua', 'act_ua'}
    for item in catalog:
        assert item['default_currency'] == 'UAH'
        labels = {field['key']: field['label'] for field in item['fields']}
        assert labels['tax_id'], 'the tax-field label must come from the profile'
        assert labels['total']


def test_act_carries_its_own_labels():
    invoice_labels = profiles.ui_labels('invoice_ua')
    act_labels = profiles.ui_labels('act_ua')
    assert invoice_labels['number'].startswith('Invoice')
    assert act_labels['number'].startswith('Act')
    assert act_labels['supplier'] == 'Executor'


def test_comment_labels_come_from_the_profile():
    labels = profiles.comment_labels('act_ua')
    assert labels['tax_id'] == 'ЄДРПОУ/ІПН'
    assert labels['bank'] == 'Банк'


def test_default_currency_is_a_profile_property():
    assert profiles.default_currency('invoice_ua') == 'UAH'
    assert profiles.default_currency('act_ua') == 'UAH'


def test_ocr_languages_are_the_union_of_profiles():
    assert profiles.ocr_langs() == 'ukr+eng'
    os.environ['DOCFLOW_OCR_LANGS'] = 'eng+deu'
    try:
        assert profiles.ocr_langs() == 'eng+deu'
    finally:
        os.environ.pop('DOCFLOW_OCR_LANGS', None)


def test_extractor_reports_the_profile_passport():
    parsed = parse_invoice(INVOICE_TEXT)
    assert parsed['type'] == 'invoice_ua'
    assert parsed['default_currency'] == 'UAH'
    assert 'ЄДРПОУ' in parsed['field_labels']['tax_id']
    assert parsed['fields']['tax_id']['source'] == 'ЄДРПОУ / ІПН'


def test_validation_takes_value_formats_from_the_profile():
    from validation import validate

    fields = {
        'tax_id': {'value': '12345678'},
        'currency': {'value': 'UAH'},
        'total': {'value': 100.0},
    }
    assert validate(fields, profile=invoice_ua)['ok']['tax_id'] is True
    assert validate(fields, profile=act_ua)['ok']['tax_id'] is True
    fields['tax_id']['value'] = '12345'
    assert validate(fields, profile=invoice_ua)['ok']['tax_id'] is False
    fields['currency']['value'] = 'GBP'
    assert validate(fields, profile=invoice_ua)['ok']['currency'] is False
    assert validate(fields, profile=act_ua)['ok']['currency'] is False


def test_core_contains_no_market_strings():
    offenders = {
        str(path.relative_to(ROOT)): list(_code_strings(path)) for path in CORE_FILES
    }
    offenders = {path: values for path, values in offenders.items() if values}
    assert not offenders, (
        'market-specific strings belong in a profile, not in the core: '
        f'{offenders}'
    )


def main():
    for name, case in sorted(globals().items()):
        if name.startswith('test_') and callable(case):
            case()
            print(f'ok  {name}')
    print('profile passport checks passed')


if __name__ == '__main__':
    main()
