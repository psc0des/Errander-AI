"""Smoke tests for the Glossary & Agent Workflow page module.

Replaces the glossary coverage that lived in tests/ui/test_web_server_smoke.py
before the legacy demo server was deleted (2026-07-10).
"""

from errander.web.glossary import _GLOSS, GLOSS_CSS, page_glossary


def test_page_glossary_renders() -> None:
    html = page_glossary()
    # Workflow diagram renders before the glossary grid (owner-requested order).
    assert html.index("Agent Workflow") < html.index("Glossary</div>")
    assert 'id="wf-diagram"' in html
    assert "selectNode" in html  # click-to-expand JS wired


def test_every_glossary_term_renders() -> None:
    html = page_glossary()
    for term, *_ in _GLOSS:
        assert term in html, f"glossary term missing from page: {term}"


def test_gloss_css_nonempty() -> None:
    assert len(GLOSS_CSS) > 100
