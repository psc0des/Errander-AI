"""Playwright tests for UI HTTP Basic Auth (/ui/* protection).

Architecture:
- auth_base_url: server started with ui_user="admin", ui_password="testpass"
- open_base_url: server started with no credentials (auth disabled)
- Uses page.request.get() for raw HTTP status checks — avoids the browser
  credential dialog that Playwright/Chromium shows on 401.
- Uses /ui/settings (no trailing slash) to avoid aiohttp 301 redirect stripping
  the Authorization header.
- /metrics and /health must remain open regardless of auth config.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from typing import TYPE_CHECKING

import pytest

from errander.observability.metrics import start_metrics_server
from errander.safety.audit import AuditStore
from errander.safety.overrides import OverridesStore

if TYPE_CHECKING:
    from playwright.sync_api import Page

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basic_auth_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _start_server(
    ui_user: str = "", ui_password: str = "",
) -> tuple[str, asyncio.AbstractEventLoop, threading.Thread, dict[str, object]]:
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        audit = AuditStore(":memory:")
        await audit.initialize()
        overrides = OverridesStore(":memory:")
        await overrides.initialize()

        runner = await start_metrics_server(
            port=0,
            audit_store=audit,
            overrides_store=overrides,
            ui_user=ui_user,
            ui_password=ui_password,
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
    """Server with Basic Auth enabled (admin / testpass)."""
    base_url, loop, t, ctx = _start_server(ui_user="admin", ui_password="testpass")
    yield base_url
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


@pytest.fixture(scope="module")
def open_base_url() -> str:  # type: ignore[return]
    """Server with auth disabled (empty credentials)."""
    base_url, loop, t, ctx = _start_server(ui_user="", ui_password="")
    yield base_url
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# Auth-enabled server: /ui/* requires credentials
# ---------------------------------------------------------------------------

class TestAuthEnabled:

    def test_ui_settings_without_credentials_returns_401(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(f"{auth_base_url}/ui/settings")
        assert response.status == 401

    def test_ui_inventory_without_credentials_returns_401(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(f"{auth_base_url}/ui/inventory")
        assert response.status == 401

    def test_ui_settings_with_correct_credentials_returns_200(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/settings",
            headers=_basic_auth_header("admin", "testpass"),
        )
        assert response.status == 200

    def test_ui_inventory_with_correct_credentials_returns_200(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/inventory",
            headers=_basic_auth_header("admin", "testpass"),
        )
        assert response.status == 200

    def test_ui_with_wrong_password_returns_401(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/settings",
            headers=_basic_auth_header("admin", "wrongpassword"),
        )
        assert response.status == 401

    def test_ui_with_wrong_username_returns_401(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(
            f"{auth_base_url}/ui/settings",
            headers=_basic_auth_header("hacker", "testpass"),
        )
        assert response.status == 401

    def test_401_response_contains_www_authenticate_header(
        self, page: Page, auth_base_url: str,
    ) -> None:
        response = page.request.get(f"{auth_base_url}/ui/settings")
        # aiohttp lowercases header names
        headers_lower = {k.lower(): v for k, v in response.headers.items()}
        assert "www-authenticate" in headers_lower

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
