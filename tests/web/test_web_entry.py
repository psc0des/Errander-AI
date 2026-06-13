"""Smoke tests for the errander.web entry point (R3 process split)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from errander.web.ui import start_web_server


class TestCLIHelp:
    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "errander.web", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert "errander-web" in result.stdout
        assert "--public-mode" in result.stdout


class TestStartWebServer:
    @pytest.mark.asyncio
    async def test_starts_and_returns_running_app(self) -> None:
        runner = await start_web_server(port=0, bind_address="127.0.0.1")
        try:
            assert runner.app is not None
            paths = {route.resource.canonical for route in runner.app.router.routes()}
            assert "/ui" in paths
            assert "/metrics" in paths
            assert "/health" in paths
        finally:
            await runner.cleanup()
