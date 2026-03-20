"""Dry-run / sandbox execution mode.

When dry_run=True, commands are simulated rather than executed.
Simulation strategies:
- Package managers: use --dry-run / --simulate flags
- Docker: use --dry-run flag where available, otherwise parse state only
- Disk cleanup: list files that would be deleted without removing them
- Log rotation: report what would be rotated without acting

The sandbox layer wraps the SSH execution layer and intercepts commands
when dry_run is enabled.
"""

from __future__ import annotations

from automaint.execution.ssh import SSHResult


async def execute_or_simulate(
    hostname: str,
    username: str,
    key_path: str,
    command: str,
    dry_run: bool,
    simulate_command: str | None = None,
    timeout_seconds: int = 60,
) -> SSHResult:
    """Execute a command or its dry-run equivalent.

    Args:
        hostname: Target host.
        username: SSH username.
        key_path: Path to private key file.
        command: The real command to execute.
        dry_run: If True, run simulate_command instead (or skip).
        simulate_command: Alternative command for dry-run mode.
            If None and dry_run=True, returns a synthetic result.
        timeout_seconds: Command timeout.

    Returns:
        SSHResult from real execution or simulation.
    """
    raise NotImplementedError("Sandbox execution not yet implemented")
