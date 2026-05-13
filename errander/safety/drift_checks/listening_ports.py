"""Listening ports drift check.

Captures TCP listening ports via `ss -tlnp` (preferred) with a fallback to
`netstat -tlnp` on older systems.  The header line is stripped, ephemeral
PID/fd values are removed, and remaining lines are sorted so port order
changes and service restarts don't trigger false alerts.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from errander.safety.baselines import BaselineCapture

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

KIND = "listening_ports"

_CMD = "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || true"

# Strip ephemeral pid= and fd= values that change on every service restart.
# Keeps the process name so new services are still detected as drift.
# Example: users:(("sshd",pid=1234,fd=4)) → users:(("sshd"))
_EPHEMERAL_RE = re.compile(r",pid=\d+|,fd=\d+")


def listening_ports_command() -> str:
    """Return shell command that lists TCP listening ports."""
    return _CMD


def parse_listening_ports(stdout: str) -> str:
    """Canonicalize raw ss/netstat output.

    Strips the header line, removes ephemeral pid/fd values, and sorts
    the remaining data lines so service restarts and enumeration-order
    changes don't produce false drift alerts.

    Args:
        stdout: Raw output from listening_ports_command().

    Returns:
        Sorted, header-stripped, pid-stripped string suitable for baseline hashing.
    """
    lines = stdout.strip().splitlines()
    if not lines:
        return ""
    # First line is always the column header
    data_lines = sorted(
        _EPHEMERAL_RE.sub("", line).strip()
        for line in lines[1:]
        if line.strip()
    )
    return "\n".join(data_lines)


async def capture_listening_ports(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
) -> list[BaselineCapture]:
    """Capture the listening ports baseline for a VM.

    SSH failure returns empty list (best-effort).

    Args:
        executor: SSH executor.
        vm_id: VM identifier.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.

    Returns:
        Single-element list with scope_key="" (one global ports snapshot).
    """
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=listening_ports_command(),
        dry_run=False,
    )
    if not result.success:
        logger.warning("listening_ports: SSH failed on %s (skipping)", vm_id)
        return []

    content = parse_listening_ports(result.stdout)
    return [BaselineCapture(kind=KIND, scope_key="", content=content)]
