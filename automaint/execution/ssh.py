"""Async SSH connection management and command execution.

Uses asyncssh for async-native SSH. Key-based auth only — passwords
are never accepted.

Features:
- Connection pooling (reuse connections within a batch run)
- Command timeout enforcement
- Structured result capture (stdout, stderr, exit code)
- Automatic retry with backoff for transient failures
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SSHResult:
    """Result of an SSH command execution.

    Attributes:
        exit_code: Process exit code (0 = success).
        stdout: Standard output.
        stderr: Standard error.
        command: The command that was executed.
    """

    exit_code: int
    stdout: str
    stderr: str
    command: str

    @property
    def success(self) -> bool:
        """Whether the command exited successfully."""
        return self.exit_code == 0


async def execute_ssh(
    hostname: str,
    username: str,
    key_path: str,
    command: str,
    timeout_seconds: int = 60,
) -> SSHResult:
    """Execute a command on a remote host via SSH.

    Args:
        hostname: Target host.
        username: SSH username.
        key_path: Path to private key file.
        command: Shell command to execute.
        timeout_seconds: Command timeout.

    Returns:
        SSHResult with stdout, stderr, and exit code.

    Raises:
        ConnectionError: If SSH connection fails.
        TimeoutError: If command exceeds timeout.
    """
    raise NotImplementedError("SSH execution not yet implemented")


async def check_connectivity(
    hostname: str,
    username: str,
    key_path: str,
) -> bool:
    """Verify SSH connectivity to a target host.

    Args:
        hostname: Target host.
        username: SSH username.
        key_path: Path to private key file.

    Returns:
        True if connection succeeds, False otherwise.
    """
    raise NotImplementedError("SSH connectivity check not yet implemented")
