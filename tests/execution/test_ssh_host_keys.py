"""Tests for SSH host key verification modes (finding #9)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from errander.execution.ssh import SSHConnectionManager


class TestSSHHostKeyModes:
    """SSHConnectionManager enforces host key verification correctly."""

    @pytest.mark.asyncio
    async def test_known_hosts_path_passed_to_asyncssh(self) -> None:
        """When known_hosts_path is set, asyncssh.connect receives it."""
        mgr = SSHConnectionManager(known_hosts_path="/etc/ssh/known_hosts")

        with patch("asyncssh.connect", new_callable=AsyncMock) as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            await mgr._connect("10.0.1.10", "errander", "/key")

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["known_hosts"] == "/etc/ssh/known_hosts"

    @pytest.mark.asyncio
    async def test_tofu_mode_uses_none_known_hosts(self) -> None:
        """TOFU mode (no known_hosts_path, strict=False) passes known_hosts=None."""
        mgr = SSHConnectionManager(known_hosts_path="", strict_host_keys=False)

        with patch("asyncssh.connect", new_callable=AsyncMock) as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            await mgr._connect("10.0.1.10", "errander", "/key")

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["known_hosts"] is None

    @pytest.mark.asyncio
    async def test_strict_mode_without_known_hosts_raises(self) -> None:
        """Strict mode (default) with no known_hosts_path refuses to connect."""
        mgr = SSHConnectionManager(known_hosts_path="", strict_host_keys=True)

        with pytest.raises(ConnectionError, match="ERRANDER_SSH_KNOWN_HOSTS"):
            await mgr._connect("10.0.1.10", "errander", "/key")

    @pytest.mark.asyncio
    async def test_default_is_strict(self) -> None:
        """Default construction has strict_host_keys=True."""
        mgr = SSHConnectionManager()
        assert mgr._strict_host_keys is True
        assert mgr._known_hosts_path == ""

    @pytest.mark.asyncio
    async def test_tofu_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """TOFU mode emits a security WARNING log per connection."""
        import logging
        mgr = SSHConnectionManager(known_hosts_path="", strict_host_keys=False)

        with patch("asyncssh.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="errander.execution.ssh"):
                await mgr._connect("10.0.1.10", "errander", "/key")

        assert any("TOFU" in r.message or "host key not verified" in r.message
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_password_always_none(self) -> None:
        """Password auth is never passed even in TOFU mode."""
        mgr = SSHConnectionManager(known_hosts_path="", strict_host_keys=False)

        with patch("asyncssh.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = MagicMock()
            await mgr._connect("10.0.1.10", "errander", "/key")

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs.get("password") is None
