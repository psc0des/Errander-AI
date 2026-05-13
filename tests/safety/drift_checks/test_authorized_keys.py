"""Tests for authorized_keys drift check (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.drift_checks.authorized_keys import (
    authorized_keys_command,
    capture_authorized_keys,
    parse_authorized_keys,
)


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


# --- authorized_keys_command ---

class TestAuthorizedKeysCommand:
    def test_contains_getent_passwd(self) -> None:
        assert "getent passwd" in authorized_keys_command()

    def test_filters_uid_range(self) -> None:
        cmd = authorized_keys_command()
        assert "1000" in cmd and "65534" in cmd

    def test_exits_zero(self) -> None:
        assert "|| true" in authorized_keys_command()

    def test_has_user_prefix_echo(self) -> None:
        assert "USER:" in authorized_keys_command()


# --- parse_authorized_keys ---

class TestParseAuthorizedKeys:
    def test_single_user_with_key(self) -> None:
        stdout = "USER:alice\nssh-rsa AAAA alice@host\n"
        result = parse_authorized_keys(stdout)
        assert len(result) == 1
        user, content = result[0]
        assert user == "alice"
        assert "ssh-rsa AAAA alice@host" in content

    def test_multiple_users(self) -> None:
        stdout = (
            "USER:alice\n"
            "ssh-rsa AAAA alice@host\n"
            "USER:bob\n"
            "ssh-ed25519 BBBB bob@host\n"
        )
        result = parse_authorized_keys(stdout)
        assert len(result) == 2
        users = [u for u, _ in result]
        assert "alice" in users
        assert "bob" in users

    def test_user_with_no_keys_returns_empty_content(self) -> None:
        stdout = "USER:alice\n"
        result = parse_authorized_keys(stdout)
        assert len(result) == 1
        _, content = result[0]
        assert content == ""

    def test_strips_comment_lines(self) -> None:
        stdout = "USER:alice\n# this is a comment\nssh-rsa AAAA key\n"
        result = parse_authorized_keys(stdout)
        _, content = result[0]
        assert "comment" not in content
        assert "ssh-rsa" in content

    def test_strips_blank_lines(self) -> None:
        stdout = "USER:alice\n\nssh-rsa AAAA key\n\n"
        result = parse_authorized_keys(stdout)
        _, content = result[0]
        assert content == "ssh-rsa AAAA key"

    def test_keys_are_sorted(self) -> None:
        stdout = "USER:alice\nssh-rsa ZZZZ last\nssh-rsa AAAA first\n"
        result = parse_authorized_keys(stdout)
        _, content = result[0]
        lines = content.splitlines()
        assert lines == sorted(lines)

    def test_empty_stdout_returns_empty(self) -> None:
        assert parse_authorized_keys("") == []

    def test_no_user_prefix_returns_empty(self) -> None:
        assert parse_authorized_keys("ssh-rsa AAAA orphan\n") == []


# --- capture_authorized_keys ---

class TestCaptureAuthorizedKeys:
    async def test_returns_captures_on_success(self) -> None:
        stdout = "USER:alice\nssh-rsa AAAA alice@host\n"
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(stdout))):
            captures = await capture_authorized_keys(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert len(captures) == 1
        assert captures[0].kind == "authorized_keys"
        assert captures[0].scope_key == "alice"

    async def test_ssh_failure_returns_empty(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            captures = await capture_authorized_keys(
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
            await capture_authorized_keys(executor, "vm1", "10.0.0.1", "user", "/key")

        assert calls[0]["dry_run"] is False

    async def test_multiple_users_produce_multiple_captures(self) -> None:
        stdout = "USER:alice\nssh-rsa A a\nUSER:bob\nssh-rsa B b\n"
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(stdout))):
            captures = await capture_authorized_keys(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert len(captures) == 2
        scope_keys = {c.scope_key for c in captures}
        assert scope_keys == {"alice", "bob"}
