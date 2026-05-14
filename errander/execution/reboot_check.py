"""Post-patching reboot-required detection.

Two OS probes:
- Debian/Ubuntu: /var/run/reboot-required flag file; packages listed in
  /var/run/reboot-required.pkgs.
- RHEL: needs-restarting -r (part of yum-utils); exit 1 = reboot needed.
  If the binary is absent (minimal images), treated as "unknown — no reboot
  flagged" so the probe never blocks runs on stripped-down hosts.

No auto-reboot is ever performed.  Detection only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebootStatus:
    """Result of a post-patch reboot-required probe."""

    needs_reboot: bool
    reason: str | None
    pkgs_requiring: tuple[str, ...]


def reboot_required_command(os_family: str) -> str:
    """Return the shell command to probe reboot-required status.

    Args:
        os_family: Detected OS family ('ubuntu', 'debian', 'rhel').

    Returns:
        Shell command string — always exits 0.
    """
    if os_family in ("ubuntu", "debian"):
        # Flag file written by kernel/libc postinst scripts.
        # .pkgs file lists the triggering package names (one per line).
        return (
            "if [ -f /var/run/reboot-required ]; then"
            " echo 'REBOOT=1';"
            " cat /var/run/reboot-required.pkgs 2>/dev/null || true;"
            " else echo 'REBOOT=0';"
            " fi"
        )
    # RHEL / CentOS / Rocky — needs-restarting is part of dnf-utils.
    # Requires sudo -n for reliable process inspection.
    # Exit 1 = reboot needed; 0 = no; absent binary → EXIT=unknown.
    return (
        "if command -v needs-restarting >/dev/null 2>&1; then"
        " sudo -n /usr/bin/needs-restarting -r >/dev/null 2>&1;"
        " echo \"EXIT=$?\";"
        " else echo 'EXIT=unknown';"
        " fi"
    )


def parse_reboot_status(stdout: str, os_family: str) -> RebootStatus:
    """Parse detect_reboot output into a RebootStatus.

    Args:
        stdout: Raw stdout from reboot_required_command().
        os_family: Detected OS family.

    Returns:
        RebootStatus with needs_reboot, optional reason, and packages list.
    """
    lines = stdout.strip().splitlines()
    if not lines:
        return RebootStatus(needs_reboot=False, reason=None, pkgs_requiring=())

    if os_family in ("ubuntu", "debian"):
        first = lines[0].strip()
        if first != "REBOOT=1":
            return RebootStatus(needs_reboot=False, reason=None, pkgs_requiring=())
        pkgs = tuple(ln.strip() for ln in lines[1:] if ln.strip())
        return RebootStatus(
            needs_reboot=True,
            reason="packages require reboot",
            pkgs_requiring=pkgs,
        )

    # RHEL
    for line in lines:
        line = line.strip()
        if line.startswith("EXIT="):
            code = line[5:]
            if code == "1":
                return RebootStatus(
                    needs_reboot=True,
                    reason="system requires reboot",
                    pkgs_requiring=(),
                )
            # "0" = clean; "unknown" = binary absent → treat as no reboot
            return RebootStatus(needs_reboot=False, reason=None, pkgs_requiring=())

    return RebootStatus(needs_reboot=False, reason=None, pkgs_requiring=())


async def detect_reboot_required(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    os_family: str,
) -> RebootStatus:
    """Run the reboot-required probe on a remote VM.

    SSH failure is treated as "no reboot required" — best-effort probe,
    never blocks runs or disrupts existing audit trail.

    Args:
        executor: SSH executor.
        vm_id: VM identifier for logging.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.
        os_family: Detected OS family (selects correct probe command).

    Returns:
        RebootStatus describing whether a reboot is needed.
    """
    cmd = reboot_required_command(os_family)
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=cmd,
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "Reboot probe failed on %s (treating as no reboot): %s",
            vm_id, result.stderr[:120],
        )
        return RebootStatus(needs_reboot=False, reason=None, pkgs_requiring=())
    return parse_reboot_status(result.stdout, os_family)
