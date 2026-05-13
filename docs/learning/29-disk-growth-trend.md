# 29 — Disk Growth Trend Detection (PR-1.4)

## What was built and why

After each maintenance run, Errander now records per-mountpoint disk usage (used bytes, total bytes) to `VMDiskHistoryStore`, then queries the trailing 7-day window to detect filesystems that have grown beyond a configurable percentage threshold. This catches runaway logs, database swell, or application data growth *before* the disk fills and causes an outage.

Components:
- `errander/execution/disk_trend.py` — pure functions + SSH probe
- `errander/agent/vm_graph.py` — `disk_snapshot_node` + conditional wiring in `build_vm_graph`
- `errander/safety/disk_history.py` — store (built in PR-G)

## Key concepts

### `df -B1` — unambiguous byte counts

`df -B1` (GNU coreutils) reports every column in exact 1-byte blocks, eliminating the ambiguity of 512-byte vs 1 K blocks that plague `df` on different platforms:

```
Filesystem     1B-blocks         Used    Available Use% Mounted on
/dev/sda1  52428800000  20971520000  31457280000  40% /
```

Column indices (0-based): `[0]` = filesystem, `[1]` = total, `[2]` = used, `[3]` = available, `[4]` = use%, `[5]` = mountpoint.

### Pseudo-filesystem filtering

Many Linux mountpoints are memory-backed and have nothing to do with disk health:

```python
_SKIP_FS_TYPES: frozenset[str] = frozenset({
    "tmpfs", "devtmpfs", "udev", "sysfs", "proc", "cgroup", "cgroup2",
    "pstore", "mqueue", "hugetlbfs", "debugfs", "securityfs",
})
```

`parse_df_bytes` checks `filesystem.startswith(skip)` — a prefix check rather than equality to handle variants like `tmpfs@shm`.

### Growth as a percentage-point delta

Absolute byte growth is misleading. A 10 GB increase on a 20 GB disk is catastrophic; the same growth on a 10 TB disk is noise. We compare *used%* between the oldest and newest data point in the window:

```python
delta = newest.used_pct - oldest.used_pct
if delta < threshold_pct:
    return None
```

`DiskDataPoint.used_pct` is a `@property`: `100.0 * used_bytes / total_bytes`.

### Best-effort probe: SSH failure never blocks

```python
result = await executor.execute(..., dry_run=False)
if not result.success:
    logger.warning(...)
    return []
```

Same pattern used in reboot detection and service health: the maintenance run continues regardless. The `dry_run=False` override is intentional — reading disk state is never destructive, and we always want real numbers.

### Conditional graph wiring

`disk_snapshot_node` is inserted between `discover` and `drift_check` only when a `disk_history_store` is provided. Without one, the graph short-circuits directly to `drift_check` (original behavior):

```python
if disk_history_store is not None:
    builder.add_node("disk_snapshot", _disk_snapshot)
    def _route_after_discover(state) -> str:
        return "audit_results" if state.get("error") else "disk_snapshot"
    builder.add_conditional_edges("discover", _route_after_discover, ...)
    builder.add_edge("disk_snapshot", "drift_check")
else:
    builder.add_conditional_edges("discover", route_after_discover, ...)
```

This keeps the feature entirely opt-in — existing callers of `build_vm_graph` are unaffected.

## Code walkthrough

### `disk_bytes_command`

```python
def disk_bytes_command() -> str:
    return "df -B1 2>/dev/null || true"
```

`2>/dev/null` silences `df` errors on systems that don't support `-B1`. `|| true` ensures exit code 0 so SSH success detection works correctly — parse treats empty output as "no data".

### `parse_df_bytes`

```python
def parse_df_bytes(stdout: str) -> list[tuple[str, int, int]]:
    for line in lines[1:]:          # skip header
        parts = line.split()
        if len(parts) < 6:
            continue
        if any(filesystem.startswith(skip) for skip in ("tmpfs", "devtmpfs", "udev")):
            continue
        with contextlib.suppress(ValueError):
            total = int(total_str)
            used  = int(used_str)
        if total > 0:               # skip zero-total mountpoints (empty/bind mounts)
            results.append((mountpoint, used, total))
```

`contextlib.suppress(ValueError)` keeps `total` and `used` at 0 if parsing fails, and the `total > 0` guard filters those out.

### `compute_growth_alert`

```python
def compute_growth_alert(datapoints, threshold_pct) -> DiskGrowth | None:
    if len(datapoints) < 2:
        return None
    oldest = datapoints[0]
    newest = datapoints[-1]
    delta = newest.used_pct - oldest.used_pct
    if delta < threshold_pct:      # negative delta (disk freed) → no alert
        return None
    return DiskGrowth(...)
```

Single data points are never alerts — we need at least two readings to establish a trend.

### `detect_growth_alerts`

```python
async def detect_growth_alerts(store, vm_id, settings):
    mountpoints = await store.get_distinct_mountpoints(vm_id)
    for mp in mountpoints:
        datapoints = await store.get_window(vm_id, mp, settings.window_days)
        alert = compute_growth_alert(datapoints, settings.threshold_pct)
        if alert:
            alerts.append(alert)
```

Each mountpoint is checked independently. This means `/` can alert while `/var` stays quiet.

## Gotchas

- **Column 1 is total, column 2 is used** — not the other way around. Easy to mix up when reading `df` output by hand where "Used" appears second but the numbers look similar.
- **`|| true` on the command line** — without this, if `df` is absent (Alpine minimal image), the SSH result would have exit_code=127 and the probe would return `[]`. With `|| true` it exits 0 and parse handles the empty output gracefully.
- **`days_ago` in tests** — `DiskDataPoint.captured_at` must be timezone-aware. `_make_datapoint` uses `datetime.now(tz=UTC)` so the delta comparison works correctly regardless of local timezone.

## Quiz

1. Why does `parse_df_bytes` use `contextlib.suppress(ValueError)` instead of a `try/except` block that returns early?
2. If `disk_history_store` is `None`, does `disk_snapshot_node` run? How is this enforced?
3. Why is `dry_run=False` hard-coded in `record_and_detect_disk_growth`?
4. If a VM has `/`, `/var`, and `/home` mounted, how many `get_window` calls are made?
5. Why do we compare `used_pct` (a percentage) instead of `used_bytes` (absolute)?
