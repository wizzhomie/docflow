"""Static contract of the two pages: what a link may do and what must not be published."""
from __future__ import annotations

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'
INDEX = (TEMPLATES / 'index.html').read_text(encoding='utf-8')
LANDING = (TEMPLATES / 'landing.html').read_text(encoding='utf-8')


def test_a_proposal_link_opens_the_document_in_the_workspace():
    # The assistant sends /app?doc=<id>; the page must know how to open that document.
    assert 'openDocument(' in INDEX
    assert "get('doc')" in INDEX
    assert '/api/documents/' in INDEX


def test_internal_plans_are_not_on_the_public_page():
    assert '#path' not in LANDING
    assert 'See the path' not in LANDING
    assert 'Our path' not in INDEX

def test_a_value_the_rules_did_not_confirm_is_marked_in_the_review_form():
    # The weight alone is not enough: the person reviewing must see that the rules
    # did not check this field.
    assert 'item.verified === false' in INDEX
    assert 'unverified' in INDEX
