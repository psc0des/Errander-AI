"""Tests for SSH connection and command execution."""

from __future__ import annotations

from automaint.execution.ssh import SSHResult


class TestSSHResult:
    """Tests for SSHResult model."""

    def test_success_property(self) -> None:
        """SSHResult.success reflects exit code."""
        assert SSHResult(exit_code=0, stdout="ok", stderr="", command="echo ok").success
        assert not SSHResult(exit_code=1, stdout="", stderr="fail", command="false").success
