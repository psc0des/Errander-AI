"""Live VM resource metrics — collection, storage, and query.

Collects CPU, memory, and per-mountpoint disk utilisation from target VMs
via SSH every 60 seconds.  Stores results in the ``vm_metrics`` table of the
audit DB (migration 0004).  The web UI reads from this table to render
Metricbeat-style sparkline trends.

Probe design:
  - Pure POSIX shell — no Python, no extra packages on target VMs.
  - One SSH connection per VM per cycle; connection opened, command run, closed.
  - vmstat 1 2 → CPU% (second sample, not since-boot average).
  - /proc/meminfo → mem% (MemAvailable / MemTotal, falls back to MemFree).
  - df -P → disk% per real mountpoint (pseudo-FS filtered out).
  - Each probe has an 8-second hard timeout (SSH + vmstat 1s + overhead).

Retention: 8 days of raw 60-second data → cleanup_old_metrics() called hourly.
Query: returns bucketed averages per time window for the API endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from errander.models.vm import VMTarget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Probe command
# ---------------------------------------------------------------------------

# One compound command — single SSH round-trip per VM.
# Output lines:
#   CPU=<int>
#   MEM=<int>
#   DISK=<mountpoint>=<int>   (one line per real mountpoint, up to 10)
#
# CPU: vmstat 1 2 → tail -1 picks the second sample (not since-boot average).
#      Column 15 is idle%; 100-idle = utilisation%.
# MEM: MemAvailable preferred (Linux 3.14+); fallback to MemFree for older kernels.
# DISK: df -P with $2>0 skips pseudo-FS (tmpfs, devtmpfs, overlay).
#       Excludes /proc /sys /run/user /snap /dev/shm which are always noise.
_PROBE_CMD = (
    r"vmstat 1 2 | tail -1 | awk '{printf \"CPU=%d\n\", 100-$15}';"
    r"awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}/MemFree/{f=$2}"
    r"END{if(!a)a=f; printf \"MEM=%d\n\", int((t-a)/t*100+0.5)}' /proc/meminfo;"
    r"df -P | awk 'NR>1 && $2>0 && $6!~/^\/(proc|sys|run\/user|snap|dev\/shm)/"
    r"{gsub(/%/,\"\",$5); printf \"DISK=%s=%s\n\",$6,$5}' | head -10"
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_probe_output(output: str) -> dict[str, float]:
    """Parse the compound probe command output into a metric dict.

    Returns keys like 'cpu', 'mem', 'disk_/', 'disk_/var', etc.
    Values are 0-100 float percentage utilisation.
    Malformed lines are silently skipped.
    """
    metrics: dict[str, float] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        tag, rest = line.split("=", 1)
        try:
            if tag == "CPU":
                val = float(rest)
                if 0.0 <= val <= 100.0:
                    metrics["cpu"] = val
            elif tag == "MEM":
                val = float(rest)
                if 0.0 <= val <= 100.0:
                    metrics["mem"] = val
            elif tag == "DISK":
                # rest is "<mountpoint>=<pct>", e.g.  "/=38"  or  "/var=52"
                path, pct_str = rest.rsplit("=", 1)
                if path:
                    val = float(pct_str)
                    if 0.0 <= val <= 100.0:
                        metrics[f"disk_{path}"] = val
        except ValueError:
            continue
    return metrics


# ---------------------------------------------------------------------------
# Single-VM probe
# ---------------------------------------------------------------------------

async def probe_vm(target: VMTarget, timeout: float = 8.0) -> dict[str, float] | None:
    """SSH into one VM, run the probe command, return parsed metrics.

    Opens a fresh connection for each probe (no pooling — metrics collection
    is lightweight and doesn't need the full SSHConnectionManager).

    Args:
        target: VMTarget from inventory.
        timeout: Hard wall-clock timeout in seconds.

    Returns:
        Dict of metric → float, or None if the probe fails for any reason.
    """
    try:
        import asyncssh  # already a project dependency
    except ImportError:
        logger.warning("asyncssh not available — vm_metrics probe disabled")
        return None

    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                target.hostname,
                username=target.ssh_user,
                client_keys=[target.ssh_key_path],
                known_hosts=None,      # TOFU — same relaxed posture the main agent uses when known_hosts not pinned
                password=None,
                connect_timeout=5,
            ),
            timeout=timeout,
        )
        async with conn:
            result = await asyncio.wait_for(
                conn.run(_PROBE_CMD, check=False),
                timeout=timeout,
            )
        if result.exit_status != 0 and not result.stdout:
            logger.debug("probe_vm %s: non-zero exit %d", target.vm_id, result.exit_status)
            return None
        metrics = parse_probe_output(result.stdout or "")
        if not metrics:
            logger.debug("probe_vm %s: empty parse result", target.vm_id)
            return None
        logger.debug("probe_vm %s: %s", target.vm_id, metrics)
        return metrics

    except TimeoutError:
        logger.debug("probe_vm %s: SSH timeout after %.0fs", target.vm_id, timeout)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("probe_vm %s: %s", target.vm_id, exc)
        return None


# ---------------------------------------------------------------------------
# Batch collection
# ---------------------------------------------------------------------------

async def collect_all(
    db: aiosqlite.Connection,
    targets: list[VMTarget],
) -> None:
    """Probe all VMs concurrently and write results to the DB.

    Silently skips unreachable VMs.  A single VM failure never blocks
    the rest of the fleet.

    Args:
        db:      Open aiosqlite connection (vm_metrics table must exist).
        targets: List of VMTarget from inventory.
    """
    if not targets:
        return

    ts = int(time.time())

    async def _probe_and_write(target: VMTarget) -> None:
        metrics = await probe_vm(target)
        if not metrics:
            return
        rows = [
            (target.hostname, metric, value, ts)
            for metric, value in metrics.items()
        ]
        try:
            await db.executemany(
                "INSERT OR REPLACE INTO vm_metrics "
                "(hostname, metric, value_pct, ts) VALUES (?, ?, ?, ?)",
                rows,
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("vm_metrics write failed for %s: %s", target.vm_id, exc)

    await asyncio.gather(*(_probe_and_write(t) for t in targets))
    logger.debug("collect_all: probed %d VMs at ts=%d", len(targets), ts)


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

async def cleanup_old_metrics(db: aiosqlite.Connection, retention_days: int = 8) -> None:
    """Delete rows older than retention_days from vm_metrics.

    Intended to be called hourly.

    Args:
        db:             Open aiosqlite connection.
        retention_days: Rows older than this are deleted.
    """
    cutoff = int(time.time()) - retention_days * 86400
    try:
        await db.execute("DELETE FROM vm_metrics WHERE ts < ?", (cutoff,))
        await db.commit()
        logger.debug("cleanup_old_metrics: deleted rows older than %d days", retention_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup_old_metrics failed: %s", exc)


# ---------------------------------------------------------------------------
# Query — time-windowed with downsampling
# ---------------------------------------------------------------------------

# Window spec: (lookback_seconds, bucket_seconds)
# For 15m and 1h: bucket=60 (raw 1-min data).
# For 24h:        bucket=300 (5-min averages  → max 288 pts).
# For 7d:         bucket=3600 (1-hr averages  → max 168 pts).
_WINDOWS: dict[str, tuple[int, int]] = {
    "15m": (900,    60),
    "1h":  (3600,   60),
    "24h": (86400,  300),
    "7d":  (604800, 3600),
}


async def query_metrics(
    db: aiosqlite.Connection,
    hostname: str,
    window: str,
) -> dict[str, Any]:
    """Return time-bucketed metrics for hostname over the given window.

    Args:
        db:       Open aiosqlite connection.
        hostname: Target hostname (matches vm_metrics.hostname).
        window:   One of '15m', '1h', '24h', '7d'.

    Returns:
        Dict with keys:
          'cpu':  list of [ts, value_pct]  (ordered ASC)
          'mem':  list of [ts, value_pct]
          'disk': dict[mountpoint, list of [ts, value_pct]]
        Empty lists/dicts when no data is available.
    """
    lookback, bucket = _WINDOWS.get(window, _WINDOWS["24h"])
    since = int(time.time()) - lookback

    # SQLite integer bucketing: CAST(ts / bucket AS INTEGER) * bucket
    sql = """
        SELECT
            CAST(ts / :b AS INTEGER) * :b AS bucket,
            metric,
            AVG(value_pct)          AS avg_val
        FROM vm_metrics
        WHERE hostname = :h
          AND ts >= :since
        GROUP BY bucket, metric
        ORDER BY bucket ASC
    """

    result: dict[str, Any] = {"cpu": [], "mem": [], "disk": {}}
    try:
        cursor = await db.execute(sql, {"b": bucket, "h": hostname, "since": since})
        rows = await cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_metrics %s %s: %s", hostname, window, exc)
        return result

    for ts_bucket, metric, avg_val in rows:
        entry = [int(ts_bucket), round(float(avg_val), 1)]
        if metric == "cpu":
            result["cpu"].append(entry)
        elif metric == "mem":
            result["mem"].append(entry)
        elif metric.startswith("disk_"):
            mount = metric[5:]   # strip "disk_" prefix → "/" or "/var" etc.
            result["disk"].setdefault(mount, []).append(entry)

    return result
