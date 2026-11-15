"""Document-type registry.

The parser is not told which document it reads: the first lines decide which
profile owns the text, and the profile carries every type-specific anchor. A
new document type is a new profile module registered here, not another branch
inside the extractor.

The registry is also the only layer that knows about a market. A profile
carries its passport — field labels, default currency, OCR language, value
formats, legal forms — and the core asks the registry instead of hardcoding one
country. The first implemented profiles describe Ukrainian documents; adding a
second market means adding a profile, not editing the parser.
"""

import os
import re

from profiles import act_ua, delivery_ua, invoice_ua

# The delivery note markers are the most specific; the invoice is the fallback
# type, so it comes last.
PROFILES = [delivery_ua, act_ua, invoice_ua]
DEFAULT = invoice_ua

DEFAULT_PROFILE = invoice_ua

# Used when a document has no recognisable type: generic English labels, never
# a wrong local term.
NEUTRAL_COMMENT_LABELS = {'tax_id': 'Tax ID', 'bank': 'Bank'}


def detect_profile(text: str):
    """Pick the profile by the title lines; an unknown document stays an invoice."""
    head = '\n'.join((text or '').strip().splitlines()[:4]).lower()
    for profile in PROFILES:
        for marker in getattr(profile, 'DETECT_MARKERS', []):
            if re.search(marker, head, re.I | re.M):
                return profile
    return DEFAULT


def get_profile(code: str | None):
    """The profile that owns a document type code; unknown codes fall back to the default."""
    for profile in PROFILES:
        if profile.CODE == code:
            return profile
    return DEFAULT_PROFILE


def catalog() -> list[dict]:
    """What the UI and the site may show about the registered document types."""
    return [
        {
            'code': profile.CODE,
            'name': profile.NAME,
            'default_currency': profile.DEFAULT_CURRENCY,
            'fields': [
                {'key': key, 'label': label}
                for key, label in getattr(profile, 'UI_LABELS', {}).items()
            ],
        }
        for profile in PROFILES
    ]


def ui_labels(code: str | None) -> dict:
    """Labels of the review form for a document type."""
    return dict(getattr(get_profile(code), 'UI_LABELS', {}))


def source_labels(code: str | None) -> dict:
    """Evidence labels: where a value was taken from, shown next to it."""
    return dict(getattr(get_profile(code), 'SOURCE_LABELS', {}))


def comment_labels(code: str | None) -> dict:
    """Local requisites in a CRM comment; neutral English when the type is unknown."""
    return {**NEUTRAL_COMMENT_LABELS, **getattr(get_profile(code), 'COMMENT_LABELS', {})}


def default_currency(code: str | None) -> str:
    return getattr(get_profile(code), 'DEFAULT_CURRENCY', '') or ''


def currencies(code: str | None) -> set[str]:
    """Currency codes the profile knows how to read."""
    return set(getattr(get_profile(code), 'CURRENCY', {}))


def ocr_langs() -> str:
    """Languages the OCR must read: the union of the registered profiles.

    OCR runs before the document type is known, so the list is deployment-wide;
    ``DOCFLOW_OCR_LANGS`` overrides it for a market that is not registered yet.
    """
    override = (os.environ.get('DOCFLOW_OCR_LANGS') or '').strip()
    if override:
        return override
    langs: list[str] = []
    for profile in PROFILES:
        for lang in (getattr(profile, 'OCR_LANGS', '') or '').split('+'):
            if lang and lang not in langs:
                langs.append(lang)
    return '+'.join(langs) or 'eng'


def legal_forms() -> tuple[str, ...]:
    """Legal forms the CRM matching strips from a company name, across profiles."""
    forms: list[str] = []
    for profile in PROFILES:
        for form in getattr(profile, 'LEGAL_FORMS', ()):
            if form not in forms:
                forms.append(form)
    return tuple(forms)


def number_chars() -> str:
    """Characters allowed inside a document number, across profiles."""
    chars = ''
    for profile in PROFILES:
        for char in getattr(profile, 'NUMBER_CHARS', ''):
            if char not in chars:
                chars += char
    return chars
