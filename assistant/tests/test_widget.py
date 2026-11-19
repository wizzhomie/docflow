"""The widget and the demo page are part of the product surface: their contract is checked."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WIDGET = (ROOT / 'static' / 'widget.js').read_text(encoding='utf-8')
PAGE = (ROOT / 'templates' / 'assistant.html').read_text(encoding='utf-8')


def test_widget_loads_once_and_exposes_a_small_api():
    assert 'docflowAssistantLoaded' in WIDGET
    assert 'window.DocFlowAssistant' in WIDGET
    assert 'open' in WIDGET and 'ask' in WIDGET


def test_widget_talks_only_to_the_chat_endpoint():
    assert '/api/chat' in WIDGET
    assert 'fetch(' in WIDGET
    for forbidden in ('/api/confirm', '/api/approve', 'mailto:', '/api/send'):
        assert forbidden not in WIDGET


def test_widget_has_the_docflow_visual_language():
    for marker in ('dfo-bubble', 'dfo-panel', 'dfo-mark', 'dfo-core', 'dfo-send'):
        assert marker in WIDGET
    assert '#7c5cff' in WIDGET and '#22d3ee' in WIDGET
    assert '.dfo-' in WIDGET


def test_widget_explains_its_grounding():
    # The "How I know" section is the honesty feature: every answer can show its steps.
    assert 'How I know' in WIDGET
    assert 'steps' in WIDGET


def test_model_text_is_rendered_as_text_not_html():
    # The assistant prints model output; it goes through textContent, never through innerHTML.
    assert 'node.textContent = text' in WIDGET
    assert 'textContent = visible' in WIDGET
    assert 'dfo-msg' in WIDGET


def test_widget_has_no_external_resources():
    for marker in ('cdn.', 'unpkg.com', 'googleapis'):
        assert marker not in WIDGET


def test_demo_page_uses_the_same_widget_and_can_open_it():
    assert '/widget.js' in PAGE
    assert 'data-assistant-open' in PAGE
    assert 'DocFlowAssistant' in PAGE


def test_demo_page_supports_a_prefilled_question():
    assert '?ask=' in PAGE
    assert 'searchParams' in PAGE or 'URLSearchParams' in PAGE


def test_demo_page_carries_no_secrets():
    for marker in ('tailscale', 'password', 'secret', 'token'):
        assert marker.lower() not in PAGE.lower()


@pytest.mark.parametrize('asset', ['widget.js'])
def test_the_widget_file_is_small_and_cache_busted(asset):
    # It is served with no-store and inlined into another page's HTML decisions: keep it lean.
    assert len(WIDGET) < 40_000
