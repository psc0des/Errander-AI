"""Tests for listening ports drift check (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.drift_checks.listening_ports import (
    capture_listening_ports,
    listening_ports_command,
    parse_listening_ports,
)


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


_SS_OUTPUT = (
    "Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    'tcp   LISTEN 0      128    0.0.0.0:22  0.0.0.0:* users:(("sshd",pid=1234,fd=4))\n'
    'tcp   LISTEN 0      511    0.0.0.0:80  0.0.0.0:* users:(("nginx",pid=5678,fd=6))\n'
)


# --- listening_ports_command ---

class TestListeningPortsCommand:
    def test_contains_ss(self) -> None:
        assert "ss" in listening_ports_command()

    def test_has_netstat_fallback(self) -> None:
        assert "netstat" in listening_ports_command()

    def test_exits_zero(self) -> None:
        assert "|| true" in listening_ports_command()

    def test_lists_tcp_listening(self) -> None:
        # Combined -tlnp flag: t=tcp, l=listening, n=numeric, p=processes
        assert "-tlnp" in listening_ports_command()


# --- parse_listening_ports ---

class TestParseListeningPorts:
    def test_strips_header_line(self) -> None:
        result = parse_listening_ports(_SS_OUTPUT)
        assert "Netid" not in result

    def test_data_lines_present(self) -> None:
        result = parse_listening_ports(_SS_OUTPUT)
        assert "0.0.0.0:22" in result
        assert "0.0.0.0:80" in result

    def test_lines_are_sorted(self) -> None:
        result = parse_listening_ports(_SS_OUTPUT)
        lines = result.splitlines()
        assert lines == sorted(lines)

    def test_empty_output_returns_empty(self) -> None:
        assert parse_listening_ports("") == ""

    def test_header_only_returns_empty(self) -> None:
        result = parse_listening_ports(
            "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
        )
        assert result == ""


# --- capture_listening_ports ---

class TestCaptureListeningPorts:
    async def test_returns_single_capture(self) -> None:
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(_SS_OUTPUT))):
            captures = await capture_listening_ports(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        assert len(captures) == 1
        assert captures[0].kind == "listening_ports"
        assert captures[0].scope_key == ""

    async def test_ssh_failure_returns_empty(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            captures = await capture_listening_ports(
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
            await capture_listening_ports(executor, "vm1", "10.0.0.1", "user", "/key")

        assert calls[0]["dry_run"] is False

    async def test_content_hash_stable(self) -> None:
        executor = _make_executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(_SS_OUTPUT))):
            captures = await capture_listening_ports(
                executor, "vm1", "10.0.0.1", "user", "/key",
            )
        h = captures[0].content_hash
        assert len(h) == 64  # SHA-256 hex
