"""Tests for UI security hardening (finding #14): bind address, mandatory auth, CSRF.

Covers errander.web.ui — the production web UI process (R3 process split).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestBindAddressSecurity:
    """start_web_server rejects non-loopback bind without any user account
    (finding #14, extended by R2: auth = named users, not a shared credential)."""

    @pytest.mark.asyncio
    async def test_nonloopback_without_users_raises(self) -> None:
        """Binding to 0.0.0.0 with zero user accounts must raise RuntimeError."""
        from errander.safety.user_store import UserStore
        from errander.web.ui import start_web_server
        from tests.conftest import make_test_db

        db = make_test_db()
        try:
            with pytest.raises(RuntimeError, match="non-loopback"):
                await start_web_server(
                    bind_address="0.0.0.0",
                    user_store=UserStore(db),
                )
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_nonloopback_without_user_store_raises(self) -> None:
        """No user store wired at all counts as zero users — refuse."""
        from errander.web.ui import start_web_server

        with pytest.raises(RuntimeError, match="non-loopback"):
            await start_web_server(bind_address="0.0.0.0")

    @pytest.mark.asyncio
    async def test_nonloopback_with_users_allowed(self) -> None:
        """Binding to 0.0.0.0 WITH at least one account must NOT raise."""
        from unittest.mock import AsyncMock

        from errander.safety.user_store import UserStore
        from errander.web.ui import start_web_server
        from tests.conftest import make_test_db

        db = make_test_db()
        user_store = UserStore(db)
        await user_store.create_user("admin", "s3cret", groups=["admin"], actor="t")
        try:
            with patch("aiohttp.web.TCPSite") as mock_site_cls, \
                 patch("aiohttp.web.AppRunner") as mock_runner_cls:
                mock_site = mock_site_cls.return_value
                mock_site.start = AsyncMock()
                mock_runner = mock_runner_cls.return_value
                mock_runner.setup = AsyncMock()

                # Must not raise — a named account exists
                await start_web_server(
                    bind_address="0.0.0.0",
                    user_store=user_store,
                )
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_loopback_without_users_allowed(self) -> None:
        """127.0.0.1 with zero users must NOT raise (read-only bootstrap mode)."""
        from unittest.mock import AsyncMock

        from errander.web.ui import start_web_server

        with patch("aiohttp.web.TCPSite") as mock_site_cls, \
             patch("aiohttp.web.AppRunner") as mock_runner_cls:
            mock_site = mock_site_cls.return_value
            mock_site.start = AsyncMock()
            mock_runner = mock_runner_cls.return_value
            mock_runner.setup = AsyncMock()

            await start_web_server(bind_address="127.0.0.1")


class TestCSRFMiddleware:
    """_csrf_middleware blocks POST /ui/* without valid CSRF token."""

    @pytest.mark.asyncio
    async def test_missing_csrf_token_returns_403(self) -> None:
        """POST /ui/approvals/X/approve without CSRF token → 403 Forbidden."""
        from aiohttp import web

        from errander.web.ui import _csrf_middleware

        async def dummy_handler(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        # Simulate a POST request with no CSRF token
        app = web.Application()
        request = web.Request.__new__(web.Request)
        # Build a minimal fake request
        from aiohttp.test_utils import make_mocked_request
        request = make_mocked_request("POST", "/ui/approvals/batch-001/approve", app=app)

        with pytest.raises(web.HTTPForbidden):
            await _csrf_middleware(request, dummy_handler)

    @pytest.mark.asyncio
    async def test_get_request_passes_without_csrf(self) -> None:
        """GET requests are not CSRF-checked."""
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from errander.web.ui import _csrf_middleware

        async def dummy_handler(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        request = make_mocked_request("GET", "/ui/approvals", app=app)
        response = await _csrf_middleware(request, dummy_handler)
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_non_ui_post_passes_without_csrf(self) -> None:
        """POST to /metrics is not CSRF-checked."""
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from errander.web.ui import _csrf_middleware

        async def dummy_handler(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        request = make_mocked_request("POST", "/metrics", app=app)
        response = await _csrf_middleware(request, dummy_handler)
        assert response.status == 200


class TestCsrfInjectHelper:
    """_re_inject_csrf injects hidden fields into form tags."""

    def test_injects_hidden_field_after_form_tag(self) -> None:
        from errander.web.ui import _re_inject_csrf

        html = '<form method="post" action="/ui/approvals"><input type="submit"></form>'
        result = _re_inject_csrf(html, '<input type="hidden" name="_csrf_token" value="abc">')
        assert '<input type="hidden" name="_csrf_token" value="abc">' in result
        # Hidden field must appear AFTER the opening form tag
        form_pos = result.index('<form ')
        hidden_pos = result.index('_csrf_token')
        assert hidden_pos > form_pos

    def test_multiple_forms_all_injected(self) -> None:
        from errander.web.ui import _re_inject_csrf

        html = '<form id="a"><input></form><form id="b"><input></form>'
        result = _re_inject_csrf(html, '<input type="hidden" name="_csrf_token" value="x">')
        assert result.count("_csrf_token") == 2
