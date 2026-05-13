"""Failed SSH login detection.

Scans journald (preferred) and auth log fallbacks for failed SSH login events
within a configurable trailing window.  Returns aggregated counts by username
and source IP — this is a security snapshot, not an alert system.

Design choices:
- journalctl is tried first (systemd systems); auth log fallback covers syslog.
- SSH failure → None (best-effort — never blocks maintenance runs).
- Always uses dry_run=False — reading auth logs is never destructive.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.config.settings import FailedSSHLoginsSettings
    from errander.execution.sandbox import SandboxExecutor
    from errander.models.reports import FailedLoginSummary

logger = logging.getLogger(__name__)

# Matches both "Failed password for USER from IP" and "Invalid user USER from IP"
_FAIL_RE = re.compile(
    r"(?:Failed password for (?:invalid user )?|Invalid user )(\S+) from (\S+)"
)


def failed_logins_command(window_hours: int = 24) -> str:
    """Return shell command that dumps recent failed SSH login log lines.

    Tries journald first (systemd), falls back to auth.log / secure.

    Args:
        window_hours: How far back to search for failures.

    Returns:
        Shell command string that always exits 0.
    """
    return (
        "{ journalctl -u ssh -u sshd"
        f' --since "{window_hours} hours ago"'
        " --no-pager --output=short-unix 2>/dev/null"
        " | grep -E 'Failed password|Invalid user';"
        " grep -h -E 'Failed password|Invalid user'"
        " /var/log/auth.log /var/log/secure 2>/dev/null | tail -5000;"
        " } 2>/dev/null || true"
    )


def parse_failed_logins(
    stdout: str,
    vm_id: str,
    window_hours: int,
) -> FailedLoginSummary:
    """Parse failed login log lines into an aggregated summary.

    Args:
        stdout: Raw output from failed_logins_command().
        vm_id: VM identifier (stored in summary for traceability).
        window_hours: Window that was searched (stored verbatim).

    Returns:
        FailedLoginSummary with total count, top-5 users, top-5 source IPs.
    """
    from errander.models.reports import FailedLoginSummary

    user_counts: Counter[str] = Counter()
    ip_counts: Counter[str] = Counter()

    for line in stdout.splitlines():
        m = _FAIL_RE.search(line)
        if m:
            user_counts[m.group(1)] += 1
            ip_counts[m.group(2)] += 1

    return FailedLoginSummary(
        vm_id=vm_id,
        window_hours=window_hours,
        total_count=sum(user_counts.values()),
        top_users=tuple(user_counts.most_common(5)),
        top_source_ips=tuple(ip_counts.most_common(5)),
    )


async def detect_failed_logins(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    settings: FailedSSHLoginsSettings,
) -> FailedLoginSummary | None:
    """Probe a VM for failed SSH logins over the configured window.

    SSH failure → None (best-effort, never blocks maintenance runs).
    Always executes with dry_run=False — reading logs is non-destructive.

    Args:
        executor: SSH executor.
        vm_id: VM identifier.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.
        settings: Window and enable configuration.

    Returns:
        FailedLoginSummary or None on SSH failure.
    """
    cmd = failed_logins_command(settings.window_hours)
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=cmd,
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "failed_logins: SSH failed on %s (skipping): %s",
            vm_id, result.stderr[:120],
        )
        return None

    return parse_failed_logins(result.stdout, vm_id, settings.window_hours)
