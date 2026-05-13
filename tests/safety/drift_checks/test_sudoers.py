"""Tests for sudoers drift check (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.drift_checks.sudoers import (
    capture_sudoers,
    parse_sudoers,
    sudoers_command,
)


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


# --- sudoers_command ---

class TestSudoersCommand:
    def test_cats_etc_sudoers(self) -> None:
        assert "/etc/sudoers" in sudoers_command()

    def test_includes_sudoers_d(self) -> None:
        assert "/etc/sudoers.d/" in sudoers_command()

    def test_exits_zero(self) -> None:
        assert "|| true" in sudoers_command()


# --- parse_sudoers ---

class TestParseSudoers:
    def test_strips_comment_lines(self) -> None:
        stdout = "# comment\nroot ALL=(ALL) ALL\n"
        assert "comment" not in parse_sudoers(stdout)
        assert "root ALL=(ALL) ALL" in parse_sudoers(stdout)

    def test_strips_blank_lines(self) -> None:
        stdout = "\nroot ALL=(ALL) ALL\n\n"
        result = parse_sudoers(stdout)
        assert result == "root ALL=(ALL) ALL"

    def test_lines_are_sorted(self) -> None:
        stdout = "bob ALL=(ALL) ALL\nalice ALL=(ALL) ALL\n"
        result = parse_sudoers(stdout)
        lines = result.splitlines()
        assert lines == sorted(lines)

    def test_empty_returns_empty_string(self) -> None:
        assert parse_sudoers("") == ""

    def test_only_comments_returns_empty_string(self) -> None:
        stdout = "# Defaults env_reset\n# Defaults mail_badpass\n"
        assert parse_sudoers(stdout) == ""


# --- capture_sudoers ---

class TestCaptureSudoers:
    async def test_returns_single_capture_with_empty_scope(self) -> None:
        stdout = "root ALL=(ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(stdout))):
            captures = await capture_sudoers(executor, "vm1", "10.0.0.1", "user", "/key")
        assert len(captures) == 1
        assert captures[0].kind == "sudoers"
        assert captures[0].scope_key == ""

    async def test_ssh_failure_returns_empty(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            captures = await capture_sudoers(executor, "vm1", "10.0.0.1", "user", "/key")
        assert captures == []

    async def test_uses_dry_run_false(self) -> None:
        executor = _make_executor()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("")

        with patch.object(executor, "execute", side_effect=capture):
            await capture_sudoers(executor, "vm1", "10.0.0.1", "user", "/key")

        assert calls[0]["dry_run"] is False

    async def test_content_is_canonicalized(self) -> None:
        stdout = "# comment\n\nroot ALL=(ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(stdout))):
            captures = await capture_sudoers(executor, "vm1", "10.0.0.1", "user", "/key")
        content = captures[0].content
        assert "comment" not in content
        assert "root ALL" in content
