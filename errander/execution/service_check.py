"""Post-action service health regression detection.

Captures systemd service states before and after a maintenance action.
Any service that was `active` before but is not `active` after is a regression.

No auto-restart is performed.  Detection only — emits SERVICE_HEALTH_REGRESSION.

Probe design:
- Uses `systemctl is-active` per service name.
- If systemctl is absent (minimal image, Docker), state is recorded as "unknown".
- A pre-existing non-active service is NOT a regression — only services that
  were active before and stopped/failed after the action are flagged.
- SSH failure → empty result (best-effort, never blocks maintenance runs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceStatus:
    """Observed state of a single systemd service."""

    name: str
    active: bool
    state: str  # "active", "inactive", "failed", "unknown", etc.


def service_status_command(services: tuple[str, ...]) -> str:
    """Return a shell command that probes the active state of each service.

    Args:
        services: Service names to probe (e.g. ("nginx", "postgresql")).

    Returns:
        Shell command that outputs "name=state" lines; always exits 0.
        Returns "true" when services is empty (safe no-op).

    Output format:
        nginx=active
        postgresql=inactive
        sshd=unknown   # systemctl absent
    """
    if not services:
        return "true"
    svc_list = " ".join(services)
    return (
        f"for svc in {svc_list}; do"
        ' state=$(systemctl is-active "$svc" 2>/dev/null);'
        ' [ -z "$state" ] && state=unknown;'
        ' echo "$svc=$state";'
        " done"
    )


def parse_service_statuses(
    stdout: str,
    services: tuple[str, ...],
) -> dict[str, ServiceStatus]:
    """Parse service_status_command output into ServiceStatus records.

    Args:
        stdout: Raw stdout from service_status_command().
        services: The set of services that were probed (used to fill in
            any services missing from output as "unknown").

    Returns:
        Mapping of service name → ServiceStatus.
    """
    parsed: dict[str, ServiceStatus] = {}
    for line in stdout.strip().splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        name, _, state = line.partition("=")
        name = name.strip()
        state = state.strip() or "unknown"
        if name:
            parsed[name] = ServiceStatus(
                name=name,
                active=(state == "active"),
                state=state,
            )

    # Fill in any services not present in output as unknown
    for svc in services:
        if svc not in parsed:
            parsed[svc] = ServiceStatus(name=svc, active=False, state="unknown")

    return parsed


def find_regressions(
    pre: dict[str, ServiceStatus],
    post: dict[str, ServiceStatus],
) -> list[str]:
    """Return service names that were active before but not active after.

    A pre-existing non-active service is NOT a regression — we only flag
    services that the action knocked offline.

    Args:
        pre: Service states captured before the action.
        post: Service states captured after the action.

    Returns:
        List of service names that regressed (was active, now not active).
    """
    return [
        name
        for name, pre_status in pre.items()
        if pre_status.active and not post.get(name, pre_status).active
    ]


async def check_services(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    services: tuple[str, ...],
) -> dict[str, ServiceStatus]:
    """Probe service states on a remote VM.

    SSH failure is treated as an empty result — the probe never blocks runs.

    Args:
        executor: SSH executor.
        vm_id: VM identifier for logging.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.
        services: Service names to probe.

    Returns:
        Mapping of service name → ServiceStatus.  Empty dict on SSH failure
        or when services is empty.
    """
    if not services:
        return {}

    cmd = service_status_command(services)
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=cmd,
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "Service health probe failed on %s (treating all as unknown): %s",
            vm_id, result.stderr[:120],
        )
        return {}
    return parse_service_statuses(result.stdout, services)
