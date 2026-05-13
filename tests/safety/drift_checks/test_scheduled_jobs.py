"""Tests for scheduled jobs drift check (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.drift_checks.scheduled_jobs import (
    capture_scheduled_jobs,
    parse_scheduled_jobs,
    scheduled_jobs_command,
)


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


_CRON_OUTPUT = (
    "# system cron\n"
    "0 2 * * 0 root /usr/sbin/logrotate /etc/logrotate.conf\n"
    "30 3 * * * backup /usr/local/bin/backup.sh\n"
)


# --- scheduled_jobs_command ---

class TestScheduledJobsCommand:
    def test_includes_crontab(self) -> None:
        assert "crontab -l" in scheduled_jobs_command()

    def test_includes_etc_crontab(self) -> None:
        assert "/etc/crontab" in scheduled_jobs_command()

    def test_includes_cron_d(self) -> None:
        assert "/etc/cron.d/" in scheduled_jobs_command()

    def test_exits_zero(self) -> None:
        assert "|| true" in scheduled_jobs_command()


# --- parse_scheduled_jobs ---

class TestParseScheduledJobs:
    def test_strips_comment_lines(self) -> None:
        result = parse_scheduled_jobs(_CRON_OUTPUT)
        assert "# system cron" not in result

    def test_retains_schedule_lines(self) -> None:
        result = parse_scheduled_jobs(_CRON_OUTPUT)
        assert "logrotate" in result
        assert "backup.sh" in result

    def test_strips_blank_lines(self) -> None:
        stdout = "0 * * * * root cmd\n\n"
        result = parse_scheduled_jobs(stdout)
        assert result == "0 * * * * root cmd"

    def test_lines_sorted(self) -> None:
        result = parse_scheduled_jobs(_CRON_OUTPUT)
        lines = result.splitlines()
        assert lines == sorted(lines)

    def test_empty_returns_empty(self) -> None:
        assert parse_scheduled_jobs("") == ""

    def test_only_comments_returns_empty(self) -> None:
        assert parse_scheduled_jobs("# nothing here\n# or here\n") == ""


# --- capture_scheduled_jobs ---

class TestCaptureScheduledJobs:
    async def test_returns_single_capture(self) -> None:
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(_CRON_OUTPUT))):
            captures = await capture_scheduled_jobs(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert len(captures) == 1
        assert captures[0].kind == "scheduled_jobs"
        assert captures[0].scope_key == ""

    async def test_ssh_failure_returns_empty(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            captures = await capture_scheduled_jobs(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert captures == []

    async def test_uses_dry_run_false(self) -> None:
        executor = _make_executor()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("")

        with patch.object(executor, "execute", side_effect=capture):
            await capture_scheduled_jobs(executor, "vm1", "10.0.0.1", "user", "/key")

        assert calls[0]["dry_run"] is False

    async def test_content_excludes_comments(self) -> None:
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(_CRON_OUTPUT))):
            captures = await capture_scheduled_jobs(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert "system cron" not in captures[0].content
