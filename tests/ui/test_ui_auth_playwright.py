"""Playwright tests for UI session-cookie auth (/ui/* protection).

Architecture (R2 — DB-backed users):
- auth_base_url: server started with an admin/testpass user account
- open_base_url: server started with zero users (read-only bootstrap mode)
- Unauthenticated /ui/* requests receive a 302 redirect to /ui/login (not 401).
- /ui/login POST with correct credentials issues a session cookie and redirects to /ui.
- /metrics and /health must remain open regardless of auth config.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from errander.safety.audit import AuditStore
from errander.safety.overrides import OverridesStore
from errander.safety.user_store import SessionStore, UserStore
from errander.web.ui import start_web_server
from tests.conftest import make_test_db

if TYPE_CHECKING:
    from playwright.sync_api import Page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_server(
    ui_user: str = "", ui_password: str = "",
) -> tuple[str, asyncio.AbstractEventLoop, threading.Thread, dict[str, object]]:
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        audit = AuditStore(make_test_db())
        await audit.initialize()
        overrides = OverridesStore(make_test_db())
        await overrides.initialize()
        user_db = make_test_db()
        user_store = UserStore(user_db)
        session_store = SessionStore(user_db, user_store)
        if ui_user and ui_password:
            await user_store.create_user(
                ui_user, ui_password, groups=["admin"], actor="test",
            )

        runner = await start_web_server(
            port=0,
            audit_store=audit,
            overrides_store=overrides,
            user_store=user_store,
            session_store=session_store,
        )
        site = list(runner.sites)[0]
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        stop: asyncio.Event = asyncio.Event()
        ctx["runner"] = runner
        ctx["stop"] = stop
        ctx["port"] = port
        ready.set()
        await stop.wait()
        await runner.cleanup()
        await overrides.close()
        await audit.close()

    loop = asyncio.new_event_loop()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    ready.wait(timeout=10)
    base_url = f"http://localhost:{ctx['port']}"
    return base_url, loop, t, ctx


@pytest.fixture(scope="module")
def auth_base_url() -> str:  # type: ignore[return]
    """Server with one admin account (admin / testpass) — login required."""
    base_url, loop, t, ctx = _start_server(ui_user="admin", ui_password="testpass")
    yield base_url
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


@pytest.fixture(scope="module")
def open_base_url() -> str:  # type: ignore[return]
    """Server with zero users — read-only bootstrap mode (GETs open on loopback)."""
    base_url, loop, t, ctx = _start_server(ui_user="", ui_password="")
    yield base_url
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


def _login(page: Page, base_url: str, user: str, password: str) -> None:
    """Navigate to login page, fill credentials, and submit."""
    page.goto(f"{base_url}/ui/login")
    page.fill("input[name=username]", user)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")


# ---------------------------------------------------------------------------
# Auth-enabled server: /ui/* requires session cookie
# ---------------------------------------------------------------------------

class TestAuthEnabled:

    def test_ui_settings_without_credentials_redirects_to_login(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/settings", max_redirects=0,
        )
        assert response.status == 302
        assert "/ui/login" in response.headers.get("location", "")

    def test_ui_inventory_without_credentials_redirects_to_login(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/inventory", max_redirects=0,
        )
        assert response.status == 302
        assert "/ui/login" in response.headers.get("location", "")

    def test_login_page_renders(
        self, page: Page, auth_base_url: str,
    ) -> None:
        page.goto(f"{auth_base_url}/ui/login")
        assert page.title() == "Errander-AI — Sign in"
        assert page.locator("input[name=username]").is_visible()
        assert page.locator("input[name=password]").is_visible()
        assert page.locator("button[type=submit]").is_visible()

    def test_correct_credentials_grant_access(
        self, page: Page, auth_base_url: str,
    ) -> None:
        _login(page, auth_base_url, "admin", "testpass")
        page.wait_for_url(f"{auth_base_url}/ui")
        assert page.url == f"{auth_base_url}/ui"

    def test_wrong_password_shows_error(
        self, page: Page, auth_base_url: str,
    ) -> None:
        page.goto(f"{auth_base_url}/ui/login")
        page.fill("input[name=username]", "admin")
        page.fill("input[name=password]", "wrongpassword")
        page.click("button[type=submit]")
        page.wait_for_url(f"{auth_base_url}/ui/login?err=1*")
        assert page.locator(".lc-err").is_visible()

    def test_wrong_username_shows_error(
        self, page: Page, auth_base_url: str,
    ) -> None:
        page.goto(f"{auth_base_url}/ui/login")
        page.fill("input[name=username]", "hacker")
        page.fill("input[name=password]", "testpass")
        page.click("button[type=submit]")
        page.wait_for_url(f"{auth_base_url}/ui/login?err=1*")
        assert page.locator(".lc-err").is_visible()

    def test_metrics_remains_open_when_auth_enabled(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(f"{auth_base_url}/metrics")
        assert response.status == 200

    def test_health_remains_open_when_auth_enabled(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(f"{auth_base_url}/health")
        assert response.status == 200


# ---------------------------------------------------------------------------
# Auth-disabled server: /ui/* accessible without credentials
# ---------------------------------------------------------------------------

class TestAuthDisabled:

    def test_ui_settings_accessible_without_credentials(
        self, page: Page, open_base_url: str,
    ) -> None:
        response = page.request.get(f"{open_base_url}/ui/settings")
        assert response.status == 200

    def test_ui_inventory_accessible_without_credentials(
        self, page: Page, open_base_url: str,
    ) -> None:
        response = page.request.get(f"{open_base_url}/ui/inventory")
        assert response.status == 200

    def test_metrics_accessible_when_auth_disabled(
        self, page: Page, open_base_url: str,
    ) -> None:
        response = page.request.get(f"{open_base_url}/metrics")
        assert response.status == 200

    def test_health_accessible_when_auth_disabled(
        self, page: Page, open_base_url: str,
    ) -> None:
        response = page.request.get(f"{open_base_url}/health")
        assert response.status == 200
