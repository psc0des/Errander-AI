"""Authorized-keys drift check.

Captures ~/.ssh/authorized_keys for every non-system user (UID 1000–65533) on
the target VM.  Each user is a separate scope_key so a key added for one user
does not falsely flag another user's keys as changed.

A single SSH command enumerates users and dumps their keys in one round-trip.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.safety.baselines import BaselineCapture

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

KIND = "authorized_keys"

# Lists users with UID 1000–65533 as "username:home" then cats their keys.
# The USER: prefix delimits sections in the output.
_CMD = (
    "getent passwd 2>/dev/null"
    " | awk -F: '$3>=1000 && $3<65534{print $1\":\"$6}'"
    " | while IFS=: read u h; do"
    " echo \"USER:$u\";"
    " cat \"$h/.ssh/authorized_keys\" 2>/dev/null;"
    " done"
    " || true"
)


def authorized_keys_command() -> str:
    """Return shell command that enumerates user authorized_keys on stdout."""
    return _CMD


def parse_authorized_keys(stdout: str) -> list[tuple[str, str]]:
    """Parse combined user-keys output into (username, canonicalized_content) pairs.

    Args:
        stdout: Raw output from authorized_keys_command().

    Returns:
        List of (username, canonicalized_keys) — one entry per user found.
    """
    results: list[tuple[str, str]] = []
    current_user: str | None = None
    current_lines: list[str] = []

    for line in stdout.splitlines():
        if line.startswith("USER:"):
            if current_user is not None:
                results.append((current_user, _canonicalize(current_lines)))
            current_user = line[5:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_user is not None:
        results.append((current_user, _canonicalize(current_lines)))

    return results


def _canonicalize(lines: list[str]) -> str:
    """Strip blank lines and comment lines, then sort."""
    filtered = [
        line for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(sorted(filtered))


async def capture_authorized_keys(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
) -> list[BaselineCapture]:
    """Capture authorized_keys baselines for all non-system users.

    SSH failure returns empty list (best-effort — never blocks maintenance).

    Args:
        executor: SSH executor.
        vm_id: VM identifier.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.

    Returns:
        One BaselineCapture per non-system user, including users with no keys
        (empty content = no keys = valid safe baseline).
    """
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=authorized_keys_command(),
        dry_run=False,
    )
    if not result.success:
        logger.warning("authorized_keys: SSH failed on %s (skipping)", vm_id)
        return []

    return [
        BaselineCapture(kind=KIND, scope_key=user, content=content)
        for user, content in parse_authorized_keys(result.stdout)
    ]
