"""Drift detection — compare current VM state against a stored baseline.

Baselines are stored as audit events in SQLite. After each successful
VM maintenance run, the discovered state is saved. Before execution,
the current state is compared to the baseline.

Drift types:
- OS version changed
- Disk usage changed significantly (>20% delta)
- Docker availability changed
- VM rebooted (uptime reset)
- Pending packages changed significantly (delta > 5)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from errander.models.events import AuditEvent, EventType
from errander.safety.audit import AuditStore

logger = logging.getLogger(__name__)


@dataclass
class DriftResult:
    """Result of comparing current state to baseline."""

    has_drift: bool
    drifts: list[str]       # human-readable drift descriptions
    baseline_found: bool    # False on first run (no baseline stored yet)


async def save_baseline(
    audit_store: AuditStore,
    vm_id: str,
    vm_info: dict[str, object],
) -> None:
    """Save current VM state as the drift baseline.

    Stores as a JSON blob in the audit event metadata.
    """
    await audit_store.log_event(
        AuditEvent(
            event_type=EventType.DRIFT_BASELINE_SAVED,
            batch_id="",
            vm_id=vm_id,
            detail="Baseline snapshot saved",
            timestamp=datetime.now(tz=UTC),
            metadata={"baseline": json.dumps(vm_info, default=str)},
        )
    )
    logger.info("Saved drift baseline for %s", vm_id)


async def load_baseline(
    audit_store: AuditStore,
    vm_id: str,
) -> dict[str, object] | None:
    """Load the most recent baseline for a VM.

    Returns None if no baseline exists (first run).
    """
    events = await audit_store.get_events(
        vm_id=vm_id,
        event_type=EventType.DRIFT_BASELINE_SAVED,
        limit=1,
    )
    if not events:
        return None

    metadata = events[0].metadata
    baseline_json = metadata.get("baseline")
    if baseline_json and isinstance(baseline_json, str):
        return json.loads(baseline_json)  # type: ignore[no-any-return]
    return None


def compare_states(
    baseline: dict[str, object],
    current: dict[str, object],
    disk_threshold: float = 20.0,
) -> DriftResult:
    """Compare baseline to current state and detect drifts.

    Args:
        baseline: Previously saved vm_info dict.
        current: Just-discovered vm_info dict.
        disk_threshold: Min disk usage delta (percentage points) to flag.

    Returns:
        DriftResult with detected drifts.
    """
    drifts: list[str] = []

    # OS version drift
    if baseline.get("os_version") != current.get("os_version"):
        drifts.append(
            f"OS version changed: {baseline.get('os_version')} -> "
            f"{current.get('os_version')}"
        )

    # Disk usage drift
    baseline_disk = baseline.get("disk_usage", {})
    current_disk = current.get("disk_usage", {})
    if isinstance(baseline_disk, dict) and isinstance(current_disk, dict):
        for mount, baseline_pct in baseline_disk.items():
            current_pct = current_disk.get(mount)
            if current_pct is not None:
                delta = abs(float(current_pct) - float(baseline_pct))
                if delta > disk_threshold:
                    drifts.append(
                        f"Disk usage on {mount}: "
                        f"{baseline_pct}% -> {current_pct}% "
                        f"(delta={delta:.1f}%)"
                    )

    # Docker availability drift
    if baseline.get("docker_available") != current.get("docker_available"):
        drifts.append(
            f"Docker availability changed: "
            f"{baseline.get('docker_available')} -> "
            f"{current.get('docker_available')}"
        )

    # Uptime reset (VM rebooted)
    baseline_uptime = float(baseline.get("uptime_seconds", 0))
    current_uptime = float(current.get("uptime_seconds", 0))
    if current_uptime < baseline_uptime and baseline_uptime > 0:
        drifts.append(
            f"VM was rebooted "
            f"(uptime: {baseline_uptime:.0f}s -> {current_uptime:.0f}s)"
        )

    # Package count drift (> 5 change)
    baseline_pkgs = int(baseline.get("pending_packages", 0))
    current_pkgs = int(current.get("pending_packages", 0))
    if abs(current_pkgs - baseline_pkgs) > 5:
        drifts.append(
            f"Pending packages changed: {baseline_pkgs} -> {current_pkgs}"
        )

    return DriftResult(
        has_drift=len(drifts) > 0,
        drifts=drifts,
        baseline_found=True,
    )
