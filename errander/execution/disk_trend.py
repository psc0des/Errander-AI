"""Disk usage capture and growth trend detection.

Records `df -B1` byte-level disk measurements to VMDiskHistoryStore after
each VM maintenance run.  After recording, queries the trailing window and
flags any mountpoint that grew by more than the configured threshold.

Design choices:
- `df -B1` (GNU coreutils) gives unambiguous byte counts on all modern Linux.
- tmpfs, devtmpfs, udev pseudo-filesystems are excluded from records and alerts.
- SSH failure → empty result (best-effort — never blocks maintenance runs).
- A single data point (< 2 readings in window) is never an alert.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.config.settings import DiskGrowthSettings
    from errander.execution.sandbox import SandboxExecutor
    from errander.models.reports import DiskGrowth
    from errander.safety.disk_history import DiskDataPoint, VMDiskHistoryStore

logger = logging.getLogger(__name__)

# Filesystem types that are always pseudo / memory-backed — never record.
_SKIP_FS_TYPES: frozenset[str] = frozenset({
    "tmpfs", "devtmpfs", "udev", "sysfs", "proc", "cgroup", "cgroup2",
    "pstore", "mqueue", "hugetlbfs", "debugfs", "securityfs",
})


def disk_bytes_command() -> str:
    """Return the shell command to capture per-mountpoint disk usage in bytes.

    Returns:
        Shell command string that always exits 0.  Outputs `df -B1` (1-byte
        blocks) filtering out virtual/pseudo filesystems.  Falls back to
        empty output on systems without GNU df — parse treats that as no data.
    """
    return "df -B1 2>/dev/null || true"


def parse_df_bytes(stdout: str) -> list[tuple[str, int, int]]:
    """Parse `df -B1` output into (mountpoint, used_bytes, total_bytes) tuples.

    Args:
        stdout: Raw stdout from disk_bytes_command().

    Returns:
        List of (mountpoint, used_bytes, total_bytes).  Skips header,
        pseudo-filesystems, and lines with non-integer values.
    """
    import contextlib

    results: list[tuple[str, int, int]] = []
    lines = stdout.strip().splitlines()
    if not lines:
        return results

    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 6:
            continue
        filesystem = parts[0]
        # Skip pseudo filesystems by filesystem name prefix
        if any(filesystem.startswith(skip) for skip in ("tmpfs", "devtmpfs", "udev")):
            continue
        mountpoint = parts[5]
        total_str = parts[1]
        used_str = parts[2]
        total = 0
        used = 0
        with contextlib.suppress(ValueError):
            total = int(total_str)
            used = int(used_str)
        if total > 0:
            results.append((mountpoint, used, total))

    return results


def compute_growth_alert(
    datapoints: list[DiskDataPoint],
    threshold_pct: float,
) -> DiskGrowth | None:
    """Return a DiskGrowth alert if the mountpoint grew beyond threshold_pct.

    Args:
        datapoints: Ordered oldest → newest measurements for one mountpoint.
        threshold_pct: Alert when used% delta (end - start) >= this value.

    Returns:
        DiskGrowth alert or None when no threshold breach or insufficient data.
    """
    from errander.models.reports import DiskGrowth

    if len(datapoints) < 2:
        return None
    oldest = datapoints[0]
    newest = datapoints[-1]
    delta = newest.used_pct - oldest.used_pct
    if delta < threshold_pct:
        return None
    return DiskGrowth(
        vm_id=oldest.vm_id,
        mountpoint=oldest.mountpoint,
        used_pct_start=round(oldest.used_pct, 1),
        used_pct_end=round(newest.used_pct, 1),
        window_start=oldest.captured_at,
        window_end=newest.captured_at,
    )


async def detect_growth_alerts(
    disk_history_store: VMDiskHistoryStore,
    vm_id: str,
    settings: DiskGrowthSettings,
) -> list[DiskGrowth]:
    """Query the trailing window for all recorded mountpoints and detect growth.

    Args:
        disk_history_store: Store to query.
        vm_id: VM identifier.
        settings: Disk growth settings (window_days, threshold_pct).

    Returns:
        List of DiskGrowth alerts for mountpoints that exceeded the threshold.
    """
    mountpoints = await disk_history_store.get_distinct_mountpoints(vm_id)
    alerts: list[DiskGrowth] = []
    for mp in mountpoints:
        datapoints = await disk_history_store.get_window(
            vm_id, mp, settings.window_days,
        )
        alert = compute_growth_alert(datapoints, settings.threshold_pct)
        if alert is not None:
            alerts.append(alert)
            logger.info(
                "Disk growth alert on %s %s: %.1f%% → %.1f%% (+%.1f%%) in %dd window",
                vm_id, mp,
                alert.used_pct_start, alert.used_pct_end, alert.delta_pct,
                settings.window_days,
            )
    return alerts


async def record_and_detect_disk_growth(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    disk_history_store: VMDiskHistoryStore,
    settings: DiskGrowthSettings,
) -> list[DiskGrowth]:
    """Capture disk usage via SSH, record to history, and return growth alerts.

    SSH failure → empty result (best-effort, never blocks maintenance runs).
    Dry-run flag on executor is bypassed — we always read real VM state.

    Args:
        executor: SSH executor.
        vm_id: VM identifier for logging and records.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.
        disk_history_store: Store for recording and querying history.
        settings: Threshold and window configuration.

    Returns:
        List of DiskGrowth alerts (may be empty).
    """
    cmd = disk_bytes_command()
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=cmd,
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "Disk snapshot SSH failed on %s (skipping): %s",
            vm_id, result.stderr[:120],
        )
        return []

    datapoints = parse_df_bytes(result.stdout)
    if not datapoints:
        logger.debug("No disk data points parsed on %s", vm_id)
        return []

    await disk_history_store.record_batch(vm_id, datapoints)
    logger.debug("Recorded %d disk data points on %s", len(datapoints), vm_id)

    return await detect_growth_alerts(disk_history_store, vm_id, settings)
