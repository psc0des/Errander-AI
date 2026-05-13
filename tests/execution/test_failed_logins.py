"""Tests for failed SSH login detection (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.config.settings import FailedSSHLoginsSettings
from errander.execution.failed_logins import (
    detect_failed_logins,
    failed_logins_command,
    parse_failed_logins,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


_SAMPLE_LOG = (
    "May 13 01:00:01 host sshd[1234]: Failed password for root from 1.2.3.4 port 12345 ssh2\n"
    "May 13 01:00:02 host sshd[1235]: Failed password for root from 1.2.3.4 port 12346 ssh2\n"
    "May 13 01:00:03 host sshd[1236]: Invalid user admin from 5.6.7.8 port 22222 ssh2\n"
    "May 13 01:00:04 host sshd[1237]:"
    " Failed password for invalid user ghost from 9.9.9.9 port 33333 ssh2\n"
)


# --- failed_logins_command ---

class TestFailedLoginsCommand:
    def test_includes_journalctl(self) -> None:
        assert "journalctl" in failed_logins_command()

    def test_includes_auth_log_fallback(self) -> None:
        assert "auth.log" in failed_logins_command()

    def test_includes_window_hours(self) -> None:
        cmd = failed_logins_command(window_hours=48)
        assert "48 hours ago" in cmd

    def test_default_window_hours(self) -> None:
        cmd = failed_logins_command()
        assert "24 hours ago" in cmd

    def test_exits_zero(self) -> None:
        assert "|| true" in failed_logins_command()

    def test_grep_pattern_covers_failed_password(self) -> None:
        assert "Failed password" in failed_logins_command()

    def test_grep_pattern_covers_invalid_user(self) -> None:
        assert "Invalid user" in failed_logins_command()


# --- parse_failed_logins ---

class TestParseFailedLogins:
    def test_counts_total_failures(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "vm1", 24)
        assert result.total_count == 4

    def test_top_users_correct(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "vm1", 24)
        top_user_names = [u for u, _ in result.top_users]
        assert "root" in top_user_names

    def test_root_appears_twice(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "vm1", 24)
        user_map = dict(result.top_users)
        assert user_map["root"] == 2

    def test_top_source_ips_correct(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "vm1", 24)
        ip_map = dict(result.top_source_ips)
        assert ip_map["1.2.3.4"] == 2

    def test_vm_id_stored(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "prod/db-01", 24)
        assert result.vm_id == "prod/db-01"

    def test_window_hours_stored(self) -> None:
        result = parse_failed_logins(_SAMPLE_LOG, "vm1", 48)
        assert result.window_hours == 48

    def test_empty_log_returns_zero(self) -> None:
        result = parse_failed_logins("", "vm1", 24)
        assert result.total_count == 0
        assert result.top_users == ()
        assert result.top_source_ips == ()

    def test_top_users_limited_to_five(self) -> None:
        # 6 distinct users
        lines = "\n".join(
            f"Failed password for user{i} from 1.2.3.4 port 1000{i} ssh2"
            for i in range(6)
        )
        result = parse_failed_logins(lines, "vm1", 24)
        assert len(result.top_users) <= 5

    def test_top_ips_limited_to_five(self) -> None:
        lines = "\n".join(
            f"Failed password for root from 10.0.0.{i} port 1000{i} ssh2"
            for i in range(6)
        )
        result = parse_failed_logins(lines, "vm1", 24)
        assert len(result.top_source_ips) <= 5

    def test_invalid_user_prefix_matched(self) -> None:
        line = "Invalid user hacker from 6.6.6.6 port 9999 ssh2"
        result = parse_failed_logins(line, "vm1", 24)
        assert result.total_count == 1
        assert result.top_users[0][0] == "hacker"


# --- detect_failed_logins ---

class TestDetectFailedLogins:
    async def test_returns_summary_on_success(self) -> None:
        executor = _make_executor()
        settings = FailedSSHLoginsSettings(enabled=True, window_hours=24)
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(_SAMPLE_LOG))):
            result = await detect_failed_logins(
                executor, "vm1", "10.0.0.1", "user", "/key", settings,
            )
        assert result is not None
        assert result.total_count == 4

    async def test_ssh_failure_returns_none(self) -> None:
        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            result = await detect_failed_logins(
                executor, "vm1", "10.0.0.1", "user", "/key", settings,
            )
        assert result is None

    async def test_uses_dry_run_false(self) -> None:
        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("")

        with patch.object(executor, "execute", side_effect=capture):
            await detect_failed_logins(executor, "vm1", "10.0.0.1", "user", "/key", settings)

        assert calls[0]["dry_run"] is False

    async def test_window_hours_passed_to_command(self) -> None:
        executor = _make_executor()
        settings = FailedSSHLoginsSettings(enabled=True, window_hours=48)
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("")

        with patch.object(executor, "execute", side_effect=capture):
            await detect_failed_logins(executor, "vm1", "10.0.0.1", "user", "/key", settings)

        assert "48 hours ago" in str(calls[0]["command"])
