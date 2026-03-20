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

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncssh

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSHResult:
    """Result of an SSH command execution.

    Attributes:
        exit_code: Process exit code (0 = success).
        stdout: Standard output.
        stderr: Standard error.
        command: The command that was executed.
        duration_seconds: How long the command took.
        started_at: When execution began.
        completed_at: When execution finished.
    """

    exit_code: int
    stdout: str
    stderr: str
    command: str
    duration_seconds: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def success(self) -> bool:
        """Whether the command exited successfully."""
        return self.exit_code == 0


class SSHConnectionManager:
    """Manages persistent SSH connections per VM.

    Connections are opened lazily and reused for the duration of a
    maintenance run. Supports reconnection with exponential backoff.

    Usage:
        async with SSHConnectionManager() as mgr:
            result = await mgr.execute("vm-id", "10.0.1.10", "automaint",
                                        "~/.ssh/key", "uptime")
    """

    def __init__(
        self,
        command_timeout: int = 300,
        reconnect_attempts: int = 3,
        reconnect_backoff: list[int] | None = None,
    ) -> None:
        self._command_timeout = command_timeout
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_backoff = reconnect_backoff or [5, 15, 45]
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}

    async def __aenter__(self) -> SSHConnectionManager:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()

    async def _connect(
        self,
        hostname: str,
        username: str,
        key_path: str,
    ) -> asyncssh.SSHClientConnection:
        """Open a new SSH connection with key-based auth only."""
        return await asyncssh.connect(
            hostname,
            username=username,
            client_keys=[key_path],
            known_hosts=None,  # TODO: enforce known_hosts in production
            password=None,  # key-based only
        )

    async def get_connection(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
    ) -> asyncssh.SSHClientConnection:
        """Get or create a persistent connection for a VM.

        Reuses existing connections. If the connection is closed,
        reconnects with backoff.

        Args:
            vm_id: Unique VM identifier for connection pooling.
            hostname: Target host.
            username: SSH username.
            key_path: Path to private key file.

        Returns:
            Active SSH connection.

        Raises:
            ConnectionError: If connection fails after all retries.
        """
        # Check if we have a live connection
        existing = self._connections.get(vm_id)
        if existing is not None:
            # asyncssh connections don't have a simple is_closed check,
            # but we try to use it and reconnect on failure
            return existing

        # Try to connect with retries
        return await self._connect_with_retry(vm_id, hostname, username, key_path)

    async def _connect_with_retry(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
    ) -> asyncssh.SSHClientConnection:
        """Connect with exponential backoff retries."""
        last_error: Exception | None = None

        for attempt in range(self._reconnect_attempts):
            try:
                conn = await self._connect(hostname, username, key_path)
                self._connections[vm_id] = conn
                logger.info("SSH connected to %s (%s)", vm_id, hostname)
                return conn
            except (OSError, asyncssh.Error) as e:
                last_error = e
                if attempt < self._reconnect_attempts - 1:
                    backoff = self._reconnect_backoff[
                        min(attempt, len(self._reconnect_backoff) - 1)
                    ]
                    logger.warning(
                        "SSH connection to %s failed (attempt %d/%d), "
                        "retrying in %ds: %s",
                        vm_id, attempt + 1, self._reconnect_attempts,
                        backoff, e,
                    )
                    await asyncio.sleep(backoff)

        msg = f"SSH connection to {vm_id} ({hostname}) failed after {self._reconnect_attempts} attempts"
        raise ConnectionError(msg) from last_error

    async def execute(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
        command: str,
        timeout: int | None = None,
    ) -> SSHResult:
        """Execute a command on a remote host via a pooled connection.

        Args:
            vm_id: VM identifier for connection pooling.
            hostname: Target host.
            username: SSH username.
            key_path: Path to private key file.
            command: Shell command to execute.
            timeout: Command timeout in seconds (overrides default).

        Returns:
            SSHResult with stdout, stderr, and exit code.

        Raises:
            ConnectionError: If SSH connection fails.
            TimeoutError: If command exceeds timeout.
        """
        effective_timeout = timeout if timeout is not None else self._command_timeout
        conn = await self.get_connection(vm_id, hostname, username, key_path)

        started_at = datetime.now(tz=timezone.utc)
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            completed_at = datetime.now(tz=timezone.utc)
            duration = (completed_at - started_at).total_seconds()
            msg = f"Command timed out after {effective_timeout}s on {vm_id}: {command}"
            raise TimeoutError(msg) from None
        except (OSError, asyncssh.Error) as e:
            # Connection might have dropped — remove from pool
            self._connections.pop(vm_id, None)
            msg = f"SSH command failed on {vm_id}: {e}"
            raise ConnectionError(msg) from e

        completed_at = datetime.now(tz=timezone.utc)
        duration = (completed_at - started_at).total_seconds()

        exit_code = result.exit_status if result.exit_status is not None else -1

        return SSHResult(
            exit_code=exit_code,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            command=command,
            duration_seconds=duration,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def close(self, vm_id: str) -> None:
        """Close connection for a specific VM."""
        conn = self._connections.pop(vm_id, None)
        if conn is not None:
            conn.close()
            logger.info("SSH closed for %s", vm_id)

    async def close_all(self) -> None:
        """Close all managed connections."""
        for vm_id, conn in list(self._connections.items()):
            conn.close()
            logger.info("SSH closed for %s", vm_id)
        self._connections.clear()

    @property
    def active_connections(self) -> list[str]:
        """List VM IDs with active connections."""
        return list(self._connections.keys())


async def execute_ssh(
    hostname: str,
    username: str,
    key_path: str,
    command: str,
    timeout_seconds: int = 60,
) -> SSHResult:
    """Execute a command on a remote host via SSH (one-shot, no pooling).

    Convenience function for single commands. For batch operations,
    use SSHConnectionManager instead.

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
    async with SSHConnectionManager(command_timeout=timeout_seconds) as mgr:
        return await mgr.execute(
            vm_id=f"{hostname}",
            hostname=hostname,
            username=username,
            key_path=key_path,
            command=command,
        )


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
    try:
        conn = await asyncssh.connect(
            hostname,
            username=username,
            client_keys=[key_path],
            known_hosts=None,
            password=None,
        )
        conn.close()
        return True
    except (OSError, asyncssh.Error):
        return False
