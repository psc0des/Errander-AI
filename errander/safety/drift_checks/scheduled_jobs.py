"""Scheduled jobs drift check.

Captures scheduled jobs from four sources in one SSH call:
  - The invoking user's crontab (crontab -l)
  - /etc/crontab (system-wide crontab)
  - /etc/cron.d/* (package-dropped cron snippets)
  - systemd timer unit names (systemctl list-timers)

Comment lines and blank lines are stripped before hashing to avoid
false alerts from comment-only changes.  The systemd section captures
timer unit names only — the volatile "next trigger" time is excluded
to prevent false drift every time the timer fires.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.safety.baselines import BaselineCapture

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

KIND = "scheduled_jobs"

_CMD = (
    "{ crontab -l 2>/dev/null;"
    " cat /etc/crontab 2>/dev/null;"
    " for f in /etc/cron.d/*; do [ -f \"$f\" ] && cat \"$f\"; done;"
    " echo '=== SYSTEMD_TIMERS ===';"
    " systemctl list-timers --all --no-legend --no-pager 2>/dev/null"
    " | awk '{print $NF}';"
    " } 2>/dev/null || true"
)


def scheduled_jobs_command() -> str:
    """Return shell command that dumps all cron job definitions to stdout."""
    return _CMD


def parse_scheduled_jobs(stdout: str) -> str:
    """Canonicalize raw crontab output.

    Strips comment lines (starting with #) and blank lines, then sorts the
    remaining schedule lines so order changes don't produce false diffs.

    Args:
        stdout: Raw output from scheduled_jobs_command().

    Returns:
        Canonicalized string suitable for baseline hashing.
    """
    lines = [
        line for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(sorted(lines))


async def capture_scheduled_jobs(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
) -> list[BaselineCapture]:
    """Capture the scheduled jobs baseline for a VM.

    SSH failure returns empty list (best-effort).

    Args:
        executor: SSH executor.
        vm_id: VM identifier.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.

    Returns:
        Single-element list with scope_key="" (one global jobs snapshot).
    """
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=scheduled_jobs_command(),
        dry_run=False,
    )
    if not result.success:
        logger.warning("scheduled_jobs: SSH failed on %s (skipping)", vm_id)
        return []

    content = parse_scheduled_jobs(result.stdout)
    return [BaselineCapture(kind=KIND, scope_key="", content=content)]
