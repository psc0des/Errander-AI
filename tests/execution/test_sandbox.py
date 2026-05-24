"""Tests for dry-run / sandbox execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult


def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(
        exit_code=exit_code, stdout=stdout, stderr="", command="mocked",
    )


class TestSandboxDryRun:
    """Tests for dry-run mode."""

    async def test_synthetic_result_when_no_simulate_command(self) -> None:
        """Without simulate_command, returns synthetic DRY-RUN result."""
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        result = await executor.execute(
            "vm-1", "10.0.1.10", "errander-ai", "/key",
            command="apt-get upgrade -y",
        )

        assert result.success
        assert "[DRY-RUN]" in result.stdout
        assert "apt-get upgrade -y" in result.stdout
        assert result.duration_seconds == 0.0

    async def test_simulate_command_executed_via_ssh(self) -> None:
        """With simulate_command, executes it via SSH."""
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        ssh_result = _make_result("2 packages would be upgraded")
        execute_mock = AsyncMock(return_value=ssh_result)

        with patch.object(mgr, "execute", execute_mock):
            result = await executor.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key",
                command="apt-get upgrade -y",
                simulate_command="apt-get --simulate upgrade",
            )

        assert result.stdout == "2 packages would be upgraded"
        execute_mock.assert_awaited_once_with(
            "vm-1", "10.0.1.10", "errander-ai", "/key",
            "apt-get --simulate upgrade", None,
        )

    async def test_dry_run_property(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)
        assert executor.dry_run is True

    async def test_command_logged_in_dry_run(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        await executor.execute(
            "vm-1", "10.0.1.10", "errander-ai", "/key",
            command="rm -rf /tmp/old-files",
        )

        assert len(executor.command_log) == 1
        record = executor.command_log[0]
        assert record.command == "rm -rf /tmp/old-files"
        assert record.dry_run is True
        assert record.vm_id == "vm-1"


class TestSandboxLive:
    """Tests for live execution mode."""

    async def test_live_executes_real_command(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=False)

        ssh_result = _make_result("packages upgraded")
        execute_mock = AsyncMock(return_value=ssh_result)

        with patch.object(mgr, "execute", execute_mock):
            result = await executor.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key",
                command="apt-get upgrade -y",
                simulate_command="apt-get --simulate upgrade",  # ignored in live mode
            )

        assert result.stdout == "packages upgraded"
        # Should execute the real command, not the simulate one
        execute_mock.assert_awaited_once_with(
            "vm-1", "10.0.1.10", "errander-ai", "/key",
            "apt-get upgrade -y", None,
        )

    async def test_live_property(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=False)
        assert executor.dry_run is False

    async def test_command_logged_in_live(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=False)

        ssh_result = _make_result("done")
        with patch.object(mgr, "execute", AsyncMock(return_value=ssh_result)):
            await executor.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key",
                command="apt-get upgrade -y",
            )

        record = executor.command_log[0]
        assert record.dry_run is False
        assert record.simulate_command is None


class TestCommandLog:
    """Tests for command log tracking."""

    async def test_multiple_commands_logged(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        await executor.execute("vm-1", "h", "u", "k", command="cmd1")
        await executor.execute("vm-2", "h", "u", "k", command="cmd2")
        await executor.execute("vm-1", "h", "u", "k", command="cmd3")

        assert len(executor.command_log) == 3
        assert executor.command_log[0].command == "cmd1"
        assert executor.command_log[1].vm_id == "vm-2"
        assert executor.command_log[2].command == "cmd3"

    async def test_clear_log(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        await executor.execute("vm-1", "h", "u", "k", command="cmd1")
        assert len(executor.command_log) == 1

        executor.clear_log()
        assert len(executor.command_log) == 0

    async def test_command_log_is_copy(self) -> None:
        """command_log property returns a copy, not the internal list."""
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=True)

        await executor.execute("vm-1", "h", "u", "k", command="cmd1")
        log = executor.command_log
        log.clear()  # should not affect internal list
        assert len(executor.command_log) == 1

    async def test_timeout_passed_through(self) -> None:
        mgr = SSHConnectionManager()
        executor = SandboxExecutor(mgr, dry_run=False)

        ssh_result = _make_result("ok")
        execute_mock = AsyncMock(return_value=ssh_result)

        with patch.object(mgr, "execute", execute_mock):
            await executor.execute(
                "vm-1", "10.0.1.10", "errander-ai", "/key",
                command="long-cmd", timeout=600,
            )

        execute_mock.assert_awaited_once_with(
            "vm-1", "10.0.1.10", "errander-ai", "/key",
            "long-cmd", 600,
        )
