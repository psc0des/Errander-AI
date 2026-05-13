"""Sudoers drift check.

Captures /etc/sudoers and every file under /etc/sudoers.d/ in a single SSH
call.  Comment lines and blank lines are stripped before hashing so trivial
formatting changes don't trigger false alerts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.safety.baselines import BaselineCapture

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

KIND = "sudoers"

_CMD = (
    "{ cat /etc/sudoers 2>/dev/null;"
    " for f in /etc/sudoers.d/*; do [ -f \"$f\" ] && cat \"$f\"; done;"
    " } 2>/dev/null || true"
)


def sudoers_command() -> str:
    """Return shell command that dumps all sudoers content to stdout."""
    return _CMD


def parse_sudoers(stdout: str) -> str:
    """Canonicalize raw sudoers output.

    Strips comment lines (starting with #) and blank lines, then sorts the
    remaining lines so minor re-orderings don't trigger a diff.

    Args:
        stdout: Raw output from sudoers_command().

    Returns:
        Canonicalized string suitable for baseline hashing.
    """
    lines = [
        line for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(sorted(lines))


async def capture_sudoers(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
) -> list[BaselineCapture]:
    """Capture the sudoers baseline for a VM.

    SSH failure returns empty list (best-effort).

    Args:
        executor: SSH executor.
        vm_id: VM identifier.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.

    Returns:
        Single-element list with scope_key="" (sudoers has one global scope).
    """
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=sudoers_command(),
        dry_run=False,
    )
    if not result.success:
        logger.warning("sudoers: SSH failed on %s (skipping)", vm_id)
        return []

    content = parse_sudoers(result.stdout)
    return [BaselineCapture(kind=KIND, scope_key="", content=content)]
