"""Dry-run / sandbox execution mode.

When dry_run=True, commands are simulated rather than executed.
Simulation strategies:
- If a simulate_command is provided: execute that instead (e.g., apt --simulate)
- If no simulate_command: return a synthetic result recording what WOULD run

The sandbox layer wraps SSHConnectionManager and intercepts commands
when dry_run is enabled. All dry-run results are clearly marked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from automaint.execution.ssh import SSHConnectionManager, SSHResult

logger = logging.getLogger(__name__)


@dataclass
class CommandRecord:
    """Record of a command that was executed or simulated.

    Tracks both the intent (command) and the outcome (result),
    plus whether it was a dry-run.
    """

    command: str
    dry_run: bool
    simulate_command: str | None
    result: SSHResult
    vm_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class SandboxExecutor:
    """Wraps SSHConnectionManager with dry-run/live mode support.

    In dry-run mode:
    - If a simulate_command is provided, executes it via SSH
      (e.g., `apt-get --simulate upgrade`)
    - If no simulate_command, returns a synthetic DRY_RUN result
      recording what WOULD have been executed

    In live mode:
    - Executes the real command via SSH

    All commands (dry-run and live) are recorded in the command log.

    Usage:
        async with SandboxExecutor(ssh_manager, dry_run=True) as executor:
            result = await executor.execute(
                vm_id="dev/web-01",
                hostname="10.0.1.10",
                username="automaint",
                key_path="~/.ssh/key",
                command="apt-get upgrade -y",
                simulate_command="apt-get --simulate upgrade",
            )
    """

    def __init__(
        self,
        ssh_manager: SSHConnectionManager,
        dry_run: bool = True,
    ) -> None:
        self._ssh = ssh_manager
        self._dry_run = dry_run
        self._command_log: list[CommandRecord] = []

    @property
    def dry_run(self) -> bool:
        """Whether the executor is in dry-run mode."""
        return self._dry_run

    @property
    def command_log(self) -> list[CommandRecord]:
        """All commands executed or simulated in this session."""
        return list(self._command_log)

    async def execute(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
        command: str,
        simulate_command: str | None = None,
        timeout: int | None = None,
    ) -> SSHResult:
        """Execute a command or its dry-run equivalent.

        Args:
            vm_id: VM identifier.
            hostname: Target host.
            username: SSH username.
            key_path: Path to private key file.
            command: The real command to execute in live mode.
            simulate_command: Alternative command for dry-run mode.
                If None and dry_run=True, returns a synthetic result.
            timeout: Command timeout in seconds.

        Returns:
            SSHResult from real execution or simulation.
        """
        if self._dry_run:
            result = await self._execute_dry_run(
                vm_id, hostname, username, key_path,
                command, simulate_command, timeout,
            )
        else:
            result = await self._execute_live(
                vm_id, hostname, username, key_path,
                command, timeout,
            )

        record = CommandRecord(
            command=command,
            dry_run=self._dry_run,
            simulate_command=simulate_command if self._dry_run else None,
            result=result,
            vm_id=vm_id,
        )
        self._command_log.append(record)

        return result

    async def _execute_dry_run(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
        command: str,
        simulate_command: str | None,
        timeout: int | None,
    ) -> SSHResult:
        """Handle dry-run execution."""
        if simulate_command is not None:
            # Execute the simulation command via SSH
            logger.info(
                "[DRY-RUN] %s: executing simulate command: %s",
                vm_id, simulate_command,
            )
            return await self._ssh.execute(
                vm_id, hostname, username, key_path,
                simulate_command, timeout,
            )

        # No simulate command — return synthetic result
        logger.info(
            "[DRY-RUN] %s: would execute: %s",
            vm_id, command,
        )
        now = datetime.now(tz=timezone.utc)
        return SSHResult(
            exit_code=0,
            stdout=f"[DRY-RUN] Would execute: {command}",
            stderr="",
            command=command,
            duration_seconds=0.0,
            started_at=now,
            completed_at=now,
        )

    async def _execute_live(
        self,
        vm_id: str,
        hostname: str,
        username: str,
        key_path: str,
        command: str,
        timeout: int | None,
    ) -> SSHResult:
        """Handle live execution."""
        logger.info("[LIVE] %s: executing: %s", vm_id, command)
        return await self._ssh.execute(
            vm_id, hostname, username, key_path,
            command, timeout,
        )

    def clear_log(self) -> None:
        """Clear the command log."""
        self._command_log.clear()
