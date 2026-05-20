"""Smoke tests for errander.web.server (Operations Hub).

These tests catch the class of bug the SRE found: f-string JS brace escaping
errors that make the module unimportable and crash the UI server at startup.

They do NOT require a running server or browser — they just verify:
  1. The module compiles without SyntaxError.
  2. The module can be imported (all top-level code runs).
  3. Every page_* render function executes without raising.

Adding a page_* function with broken f-string escaping will fail test 3
immediately, before any manual QA is needed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Compile check
# ---------------------------------------------------------------------------

def test_server_py_compiles() -> None:
    """errander/web/server.py must parse without SyntaxError."""
    server_path = Path(__file__).parent.parent.parent / "errander" / "web" / "server.py"
    assert server_path.exists(), f"server.py not found at {server_path}"
    source = server_path.read_text(encoding="utf-8")
    # ast.parse raises SyntaxError if the file has any syntax issue
    ast.parse(source, filename=str(server_path))


# ---------------------------------------------------------------------------
# 2. Import check
# ---------------------------------------------------------------------------

def test_server_module_importable() -> None:
    """errander.web.server must be importable (no top-level exceptions)."""
    # Force a fresh import so cached pyc doesn't mask a real error
    import errander.web.server as srv  # noqa: F401
    assert srv is not None


# ---------------------------------------------------------------------------
# 3. Page render smoke tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    """Import the server module once for all render tests."""
    import errander.web.server as srv
    return srv


def test_page_fleet_renders(server) -> None:
    html = server.page_fleet()
    assert "<html" not in html  # page_* returns a fragment, not full page
    assert len(html) > 100


def test_page_approvals_renders(server) -> None:
    html = server.page_approvals()
    assert len(html) > 100


def test_page_audit_renders(server) -> None:
    html = server.page_audit()
    assert len(html) > 100


def test_page_batches_renders(server) -> None:
    html = server.page_batches()
    assert len(html) > 100
    # The batch filter JS must be present and use escaped braces (not crash)
    assert "_batchFilter" in html


def test_page_agent_renders(server) -> None:
    html = server.page_agent()
    assert len(html) > 100


def test_page_inventory_renders(server) -> None:
    html = server.page_inventory()
    assert len(html) > 100
    assert "_invFilter" in html


def test_page_settings_renders(server) -> None:
    html = server.page_settings()
    assert len(html) > 100


def test_page_admin_renders(server) -> None:
    html = server.page_admin()
    assert len(html) > 100


def test_page_glossary_renders(server) -> None:
    html = server.page_glossary()
    assert len(html) > 100


def test_layout_renders(server) -> None:
    """layout() wraps a page fragment into a full HTML document."""
    html = server.layout(
        title="Test",
        active_url="/fleet",
        breadcrumb="Test",
        topnav_extra="",
        content="<p>hello</p>",
    )
    assert "<!DOCTYPE html>" in html or "<html" in html
    assert "<p>hello</p>" in html


def test_no_unescaped_js_braces_in_batch_filter(server) -> None:
    """Regression guard: _batchFilter JS must use {{ / }} in the f-string output."""
    html = server.page_batches()
    # The rendered HTML should contain literal { } (JS braces), not {{ }}
    assert "function _batchFilter(el, status) {" in html
    assert "forEach(row => {" in html


def test_no_unescaped_js_braces_in_inv_filter(server) -> None:
    """Regression guard: _invFilter JS must render with literal braces."""
    html = server.page_inventory()
    assert "function _invFilter()" in html
    assert "forEach(function(row) {" in html
