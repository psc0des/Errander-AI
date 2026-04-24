"""Tests for SSH connection and command execution (fully mocked)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.execution.ssh import SSHConnectionManager, SSHResult, check_connectivity, execute_ssh


class TestSSHResult:
    """Tests for SSHResult model."""

    def test_success_property(self) -> None:
        assert SSHResult(exit_code=0, stdout="ok", stderr="", command="echo ok").success
        assert not SSHResult(exit_code=1, stdout="", stderr="fail", command="false").success

    def test_frozen(self) -> None:
        result = SSHResult(exit_code=0, stdout="", stderr="", command="ls")
        with pytest.raises(AttributeError):
            result.exit_code = 1  # type: ignore[misc]


@dataclass
class FakeSSHProcess:
    """Fake asyncssh process result."""
    exit_status: int | None = 0
    stdout: str = ""
    stderr: str = ""


class FakeSSHConnection:
    """Fake asyncssh connection for testing."""

    def __init__(self, fail_on_run: bool = False) -> None:
        self.closed = False
        self._fail_on_run = fail_on_run

    async def run(self, command: str, check: bool = True) -> FakeSSHProcess:
        if self._fail_on_run:
            raise OSError("Connection lost")
        return FakeSSHProcess(exit_status=0, stdout=f"output of: {command}", stderr="")

    def close(self) -> None:
        self.closed = True


class TestSSHConnectionManager:
    """Tests for SSHConnectionManager (all SSH calls mocked)."""

    async def test_execute_success(self) -> None:
        mgr = SSHConnectionManager()
        fake_conn = FakeSSHConnection()

        with patch.object(mgr, "_connect", return_value=fake_conn):
            result = await mgr.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key", "uptime",
            )

        assert result.success
        assert result.exit_code == 0
        assert "output of: uptime" in result.stdout
        assert result.command == "uptime"
        assert result.duration_seconds >= 0

    async def test_connection_reused(self) -> None:
        """Second execute reuses the same connection."""
        mgr = SSHConnectionManager()
        fake_conn = FakeSSHConnection()

        connect_mock = AsyncMock(return_value=fake_conn)
        with patch.object(mgr, "_connect", connect_mock):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd1")
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd2")

        # _connect called only once — second call reuses
        connect_mock.assert_awaited_once()

    async def test_different_vms_get_different_connections(self) -> None:
        mgr = SSHConnectionManager()
        connect_mock = AsyncMock(side_effect=[FakeSSHConnection(), FakeSSHConnection()])

        with patch.object(mgr, "_connect", connect_mock):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd")
            await mgr.execute("vm-2", "10.0.2.10", "errander-ai", "/key", "cmd")

        assert connect_mock.await_count == 2

    async def test_timeout_raises(self) -> None:
        """Command exceeding timeout raises TimeoutError."""
        mgr = SSHConnectionManager(command_timeout=1)

        async def slow_run(command: str, check: bool = True) -> FakeSSHProcess:
            await asyncio.sleep(10)
            return FakeSSHProcess()

        fake_conn = FakeSSHConnection()
        fake_conn.run = slow_run  # type: ignore[assignment]

        with patch.object(mgr, "_connect", return_value=fake_conn):
            with pytest.raises(TimeoutError, match="timed out"):
                await mgr.execute(
                    "vm-1", "10.0.1.10", "errander-ai", "/key", "sleep 999",
                    timeout=1,
                )

    async def test_connection_error_removes_from_pool(self) -> None:
        """Connection error during execute removes conn from pool."""
        mgr = SSHConnectionManager()

        # First call succeeds, second call has connection that fails on run
        good_conn = FakeSSHConnection()
        bad_conn = FakeSSHConnection(fail_on_run=True)

        with patch.object(mgr, "_connect", return_value=good_conn):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd1")

        # Manually replace the connection with a bad one
        mgr._connections["vm-1"] = bad_conn  # type: ignore[assignment]

        with pytest.raises(ConnectionError, match="SSH command failed"):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd2")

        assert "vm-1" not in mgr._connections

    async def test_retry_on_connection_failure(self) -> None:
        """Retries connection with backoff on failure."""
        mgr = SSHConnectionManager(
            reconnect_attempts=3,
            reconnect_backoff=[0, 0, 0],  # no actual sleep in tests
        )

        fake_conn = FakeSSHConnection()
        connect_mock = AsyncMock(
            side_effect=[OSError("refused"), OSError("refused"), fake_conn],
        )

        with patch.object(mgr, "_connect", connect_mock):
            result = await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "uptime")

        assert result.success
        assert connect_mock.await_count == 3

    async def test_all_retries_exhausted_raises(self) -> None:
        """ConnectionError after all retries exhausted."""
        mgr = SSHConnectionManager(
            reconnect_attempts=2,
            reconnect_backoff=[0, 0],
        )

        connect_mock = AsyncMock(
            side_effect=[OSError("refused"), OSError("refused")],
        )

        with patch.object(mgr, "_connect", connect_mock):
            with pytest.raises(ConnectionError, match="failed after 2 attempts"):
                await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "uptime")

    async def test_close_single(self) -> None:
        mgr = SSHConnectionManager()
        fake_conn = FakeSSHConnection()

        with patch.object(mgr, "_connect", return_value=fake_conn):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "uptime")

        await mgr.close("vm-1")
        assert fake_conn.closed
        assert "vm-1" not in mgr._connections

    async def test_close_all(self) -> None:
        mgr = SSHConnectionManager()
        conn1 = FakeSSHConnection()
        conn2 = FakeSSHConnection()

        with patch.object(mgr, "_connect", side_effect=[conn1, conn2]):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd")
            await mgr.execute("vm-2", "10.0.2.10", "errander-ai", "/key", "cmd")

        await mgr.close_all()
        assert conn1.closed
        assert conn2.closed
        assert mgr.active_connections == []

    async def test_context_manager_closes_all(self) -> None:
        fake_conn = FakeSSHConnection()

        async with SSHConnectionManager() as mgr:
            with patch.object(mgr, "_connect", return_value=fake_conn):
                await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd")

        assert fake_conn.closed

    async def test_active_connections_property(self) -> None:
        mgr = SSHConnectionManager()
        conn1 = FakeSSHConnection()
        conn2 = FakeSSHConnection()

        with patch.object(mgr, "_connect", side_effect=[conn1, conn2]):
            await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd")
            await mgr.execute("vm-2", "10.0.2.10", "errander-ai", "/key", "cmd")

        assert set(mgr.active_connections) == {"vm-1", "vm-2"}

    async def test_custom_timeout_override(self) -> None:
        """Per-command timeout overrides default."""
        mgr = SSHConnectionManager(command_timeout=300)
        fake_conn = FakeSSHConnection()

        with patch.object(mgr, "_connect", return_value=fake_conn):
            # This should use timeout=5, not the default 300
            result = await mgr.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key", "echo fast",
                timeout=5,
            )
            assert result.success

    async def test_none_exit_status_becomes_255(self) -> None:
        """If asyncssh returns None exit_status, we use 255 (conventional SSH error code)."""
        mgr = SSHConnectionManager()

        async def null_exit_run(command: str, check: bool = True) -> FakeSSHProcess:
            return FakeSSHProcess(exit_status=None, stdout="", stderr="")

        fake_conn = FakeSSHConnection()
        fake_conn.run = null_exit_run  # type: ignore[assignment]

        with patch.object(mgr, "_connect", return_value=fake_conn):
            result = await mgr.execute("vm-1", "10.0.1.10", "errander-ai", "/key", "cmd")
            assert result.exit_code == 255
            assert not result.success

    async def test_timeout_clears_connection_from_pool(self) -> None:
        """Timeout during execute removes the stale connection from the pool."""
        mgr = SSHConnectionManager(command_timeout=1)

        async def timeout_run(command: str, check: bool = True) -> FakeSSHProcess:
            await asyncio.sleep(10)
            return FakeSSHProcess()

        fake_conn = FakeSSHConnection()
        fake_conn.run = timeout_run  # type: ignore[assignment]

        with patch.object(mgr, "_connect", return_value=fake_conn):
            with pytest.raises(TimeoutError):
                await mgr.execute(
                    "vm-1", "10.0.1.10", "errander-ai", "/key", "sleep 999",
                    timeout=1,
                )

        assert "vm-1" not in mgr._connections


class TestExecuteSSH:
    """Tests for the one-shot execute_ssh convenience function."""

    async def test_execute_ssh_delegates_to_manager(self) -> None:
        fake_result = SSHResult(
            exit_code=0, stdout="hello", stderr="", command="echo hello",
        )

        with patch(
            "errander.execution.ssh.SSHConnectionManager.execute",
            return_value=fake_result,
        ):
            result = await execute_ssh(
                "10.0.1.10", "errander-ai", "/key", "echo hello",
            )
            assert result.success
            assert result.stdout == "hello"


class TestCheckConnectivity:
    """Tests for SSH connectivity check."""

    async def test_success(self) -> None:
        fake_conn = MagicMock()
        fake_conn.close = MagicMock()

        with patch("errander.execution.ssh.asyncssh.connect", new_callable=AsyncMock, return_value=fake_conn):
            assert await check_connectivity("10.0.1.10", "errander-ai", "/key")

    async def test_failure(self) -> None:
        with patch("errander.execution.ssh.asyncssh.connect", new_callable=AsyncMock, side_effect=OSError("refused")):
            assert not await check_connectivity("10.0.1.10", "errander-ai", "/key")
