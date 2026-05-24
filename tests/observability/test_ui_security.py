"""Tests for UI security hardening (finding #14): bind address, mandatory auth, CSRF."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestBindAddressSecurity:
    """start_metrics_server rejects non-loopback bind without auth credentials."""

    @pytest.mark.asyncio
    async def test_nonloopback_without_auth_raises(self) -> None:
        """Binding to 0.0.0.0 without credentials must raise RuntimeError."""
        from errander.observability.metrics import start_metrics_server

        with pytest.raises(RuntimeError, match="non-loopback"):
            await start_metrics_server(
                bind_address="0.0.0.0",
                ui_user="",
                ui_password="",
            )

    @pytest.mark.asyncio
    async def test_nonloopback_with_auth_allowed(self) -> None:
        """Binding to 0.0.0.0 WITH credentials must NOT raise RuntimeError."""
        from unittest.mock import AsyncMock

        from errander.observability.metrics import start_metrics_server

        with patch("aiohttp.web.TCPSite") as mock_site_cls, \
             patch("aiohttp.web.AppRunner") as mock_runner_cls:
            mock_site = mock_site_cls.return_value
            mock_site.start = AsyncMock()
            mock_runner = mock_runner_cls.return_value
            mock_runner.setup = AsyncMock()

            # Must not raise — auth is provided
            await start_metrics_server(
                bind_address="0.0.0.0",
                ui_user="admin",
                ui_password="s3cret",
            )

    @pytest.mark.asyncio
    async def test_loopback_without_auth_allowed(self) -> None:
        """127.0.0.1 without credentials must NOT raise RuntimeError."""
        from unittest.mock import AsyncMock

        from errander.observability.metrics import start_metrics_server

        with patch("aiohttp.web.TCPSite") as mock_site_cls, \
             patch("aiohttp.web.AppRunner") as mock_runner_cls:
            mock_site = mock_site_cls.return_value
            mock_site.start = AsyncMock()
            mock_runner = mock_runner_cls.return_value
            mock_runner.setup = AsyncMock()

            await start_metrics_server(
                bind_address="127.0.0.1",
                ui_user="",
                ui_password="",
            )


class TestCSRFMiddleware:
    """_csrf_middleware blocks POST /ui/* without valid CSRF token."""

    @pytest.mark.asyncio
    async def test_missing_csrf_token_returns_403(self) -> None:
        """POST /ui/approvals/X/approve without CSRF token → 403 Forbidden."""
        from aiohttp import web

        from errander.observability.metrics import _csrf_middleware

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

        from errander.observability.metrics import _csrf_middleware

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

        from errander.observability.metrics import _csrf_middleware

        async def dummy_handler(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        request = make_mocked_request("POST", "/metrics", app=app)
        response = await _csrf_middleware(request, dummy_handler)
        assert response.status == 200


class TestCsrfInjectHelper:
    """_re_inject_csrf injects hidden fields into form tags."""

    def test_injects_hidden_field_after_form_tag(self) -> None:
        from errander.observability.metrics import _re_inject_csrf

        html = '<form method="post" action="/ui/approvals"><input type="submit"></form>'
        result = _re_inject_csrf(html, '<input type="hidden" name="_csrf_token" value="abc">')
        assert '<input type="hidden" name="_csrf_token" value="abc">' in result
        # Hidden field must appear AFTER the opening form tag
        form_pos = result.index('<form ')
        hidden_pos = result.index('_csrf_token')
        assert hidden_pos > form_pos

    def test_multiple_forms_all_injected(self) -> None:
        from errander.observability.metrics import _re_inject_csrf

        html = '<form id="a"><input></form><form id="b"><input></form>'
        result = _re_inject_csrf(html, '<input type="hidden" name="_csrf_token" value="x">')
        assert result.count("_csrf_token") == 2
