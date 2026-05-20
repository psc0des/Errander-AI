"""Live VM resource metrics — collection, storage, and query.

Source selection (per VM, auto-detected at startup):
  Node Exporter (preferred)
    HTTP scrape on :{node_exporter_port} (default 9100).
    Zero SSH cost, richer data, stateless for mem/disk.
    CPU% computed from node_cpu_seconds_total counter delta (two samples needed).
    Configurable port: ERRANDER_NODE_EXPORTER_PORT env var.

  SSH probe (fallback — any VM where :9100 is unreachable)
    Pure POSIX shell: vmstat + /proc/meminfo + timeout 3 df -P.
    No agent required on target. Persistent SSH connection reused across cycles.
    Auth events in sshd logs: 1 per agent restart, not 1 per probe cycle.
    If Node Exporter disappears mid-session the VM is automatically demoted
    to SSH probe; a restart of the agent restores NE if it comes back.

Interval: ERRANDER_METRICS_INTERVAL_SECONDS (default 60, clamped [30, 300]).
Retention: 8 days raw data, cleaned hourly by cleanup_old_metrics().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    import aiosqlite

    from errander.models.vm import VMTarget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSH probe command
# ---------------------------------------------------------------------------

# One compound command — single SSH round-trip per VM.
# Output lines:
#   CPU=<int>
#   MEM=<int>
#   DISK=<mountpoint>=<int>   (one per real mountpoint, up to 10)
#
# CPU: vmstat 1 2 → tail -1 picks second sample (not since-boot average).
# MEM: MemAvailable (Linux 3.14+) with MemFree fallback.
# DISK: timeout 3 df -P — the 3s shell timeout kills df if any mount
#       (NFS, stale bind-mount) is unresponsive.
_PROBE_CMD = (
    r"vmstat 1 2 | tail -1 | awk '{printf \"CPU=%d\n\", 100-$15}';"
    r"awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}/MemFree/{f=$2}"
    r"END{if(!a)a=f; printf \"MEM=%d\n\", int((t-a)/t*100+0.5)}' /proc/meminfo;"
    r"timeout 3 df -P | awk 'NR>1 && $2>0 && $6!~/^\/(proc|sys|run\/user|snap|dev\/shm)/"
    r"{gsub(/%/,\"\",$5); printf \"DISK=%s=%s\n\",$6,$5}' | head -10"
)

# ---------------------------------------------------------------------------
# Node Exporter — Prometheus text format parser + metric extraction
# ---------------------------------------------------------------------------

# Compiled once; matches "metric_name{labels} value" or "metric_name value"
_PROM_LINE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([\-\d.e+]+)"
)

# Filesystem types that are always noise (pseudo, overlay, memory-backed).
_NOISY_FSTYPES = frozenset({
    "tmpfs", "rootfs", "devtmpfs", "overlay", "squashfs",
    "cgroup", "cgroup2", "sysfs", "proc", "devpts", "mqueue",
    "hugetlbfs", "debugfs", "fusectl", "securityfs", "pstore",
    "bpf", "tracefs", "autofs", "ramfs",
})

# Mountpoints that are always noise regardless of fstype.
_NOISY_MOUNT_RE = re.compile(
    r"^/(proc|sys|run/user|snap|dev/shm|boot/efi)($|/)"
)


def _parse_prom_text(
    text: str,
) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse Prometheus exposition format into {metric: [(labels, value)]}.

    Skips HELP/TYPE comment lines and any malformed entries.
    Values may be in scientific notation (e.g. 1.23e+09).
    """
    parsed: dict[str, list[tuple[dict[str, str], float]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE_RE.match(line)
        if not m:
            continue
        name, labels_str, value_str = m.groups()
        labels: dict[str, str] = {}
        if labels_str:
            for k, v in re.findall(r'(\w+)="([^"]*)"', labels_str):
                labels[k] = v
        try:
            parsed.setdefault(name, []).append((labels, float(value_str)))
        except ValueError:
            continue
    return parsed


# ---------------------------------------------------------------------------
# SSH probe output parser (pure function — also used by tests)
# ---------------------------------------------------------------------------

def parse_probe_output(output: str) -> dict[str, float]:
    """Parse SSH probe output into a metric dict.

    Returns keys like 'cpu', 'mem', 'disk_/', 'disk_/var'.
    Values are 0–100 float utilisation percentages.
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
                path, pct_str = rest.rsplit("=", 1)
                if path:
                    val = float(pct_str)
                    if 0.0 <= val <= 100.0:
                        metrics[f"disk_{path}"] = val
        except ValueError:
            continue
    return metrics


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Per-VM metrics source strategy, connection state, and collection logic.

    Lifecycle:
        collector = MetricsCollector()
        await collector.discover(targets)       # once at startup
        # APScheduler calls:
        await collector.collect_all(db, targets)  # every N seconds
        # On shutdown:
        await collector.close()
    """

    def __init__(self, node_exporter_port: int = 9100) -> None:
        self._ne_port = node_exporter_port
        # vm_id → "node_exporter" | "ssh_probe"
        self._source: dict[str, str] = {}
        # Persistent HTTP session (reused across all NE scrapes)
        self._http_session: aiohttp.ClientSession | None = None
        # Persistent SSH connections (one per SSH-probe VM)
        self._ssh_conns: dict[str, Any] = {}
        # CPU delta state: vm_id → (timestamp, idle_sum, total_sum)
        self._cpu_prev: dict[str, tuple[float, float, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def discover(self, targets: list[VMTarget]) -> None:
        """Set metrics source per VM from the inventory node_exporter flag.

        If node_exporter=True, verifies :9100 is actually responding.
        If it is not (NE crashed or was never installed), logs a warning and
        falls back to SSH probe — never silently drops metrics.

        Run configure.sh to install/fix Node Exporter and update inventory.
        """
        async def _check(target: VMTarget) -> None:
            if not target.node_exporter:
                self._source[target.vm_id] = "ssh_probe"
                logger.info(
                    "metrics: %s → SSH probe (node_exporter=false in inventory)",
                    target.vm_id,
                )
                return

            # node_exporter=true: verify :9100 is actually reachable.
            url = f"http://{target.hostname}:{self._ne_port}/metrics"
            try:
                async with self._session().get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=3.0),
                ) as resp:
                    if resp.status == 200:
                        self._source[target.vm_id] = "node_exporter"
                        logger.info(
                            "metrics: %s → Node Exporter :%d",
                            target.vm_id, self._ne_port,
                        )
                        return
                    logger.warning(
                        "metrics: %s node_exporter=true but :%d returned HTTP %d "
                        "— SSH probe fallback. Re-run configure.sh to fix.",
                        target.vm_id, self._ne_port, resp.status,
                    )
            except Exception as exc:
                logger.warning(
                    "metrics: %s node_exporter=true but :%d unreachable (%s) "
                    "— SSH probe fallback. Re-run configure.sh to fix.",
                    target.vm_id, self._ne_port, exc,
                )

            self._source[target.vm_id] = "ssh_probe"

        await asyncio.gather(*(_check(t) for t in targets))

    async def collect_all(
        self,
        db: aiosqlite.Connection,
        targets: list[VMTarget],
    ) -> None:
        """Probe all VMs concurrently and write results to the DB.

        A single VM failure never blocks the rest of the fleet.
        """
        if not targets:
            return
        ts = int(time.time())

        async def _probe_and_write(target: VMTarget) -> None:
            metrics = await self.probe(target)
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
                logger.warning(
                    "vm_metrics write failed for %s: %s", target.vm_id, exc
                )

        await asyncio.gather(*(_probe_and_write(t) for t in targets))
        logger.debug("collect_all: %d VMs at ts=%d", len(targets), ts)

    async def probe(self, target: VMTarget) -> dict[str, float] | None:
        """Probe one VM using its detected source. Returns None on failure."""
        source = self._source.get(target.vm_id, "ssh_probe")
        if source == "node_exporter":
            return await self._probe_node_exporter(target)
        return await self._probe_ssh(target)

    async def close(self) -> None:
        """Release HTTP session and all persistent SSH connections."""
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
            logger.debug("MetricsCollector: HTTP session closed")
        for vm_id, conn in self._ssh_conns.items():
            with contextlib.suppress(Exception):
                conn.close()
            logger.debug("MetricsCollector: SSH connection closed for %s", vm_id)
        self._ssh_conns.clear()
        self._source.clear()
        self._cpu_prev.clear()

    @property
    def source_map(self) -> dict[str, str]:
        """Snapshot of {vm_id → source} for logging/status."""
        return dict(self._source)

    # ------------------------------------------------------------------
    # Node Exporter path
    # ------------------------------------------------------------------

    async def _probe_node_exporter(
        self,
        target: VMTarget,
        timeout: float = 5.0,
    ) -> dict[str, float] | None:
        url = f"http://{target.hostname}:{self._ne_port}/metrics"
        try:
            async with self._session().get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        "probe_ne %s: HTTP %d", target.vm_id, resp.status
                    )
                    return None
                text = await resp.text()
        except Exception as exc:
            logger.debug("probe_ne %s: %s — demoting to SSH probe", target.vm_id, exc)
            # Node Exporter disappeared — demote transparently.
            self._source[target.vm_id] = "ssh_probe"
            return await self._probe_ssh(target)

        parsed = _parse_prom_text(text)
        metrics = self._extract_ne_metrics(target.vm_id, parsed, time.time())
        if not metrics:
            logger.debug("probe_ne %s: empty result", target.vm_id)
            return None
        logger.debug("probe_ne %s: %s", target.vm_id, metrics)
        return metrics

    def _extract_ne_metrics(
        self,
        vm_id: str,
        parsed: dict[str, list[tuple[dict[str, str], float]]],
        now: float,
    ) -> dict[str, float]:
        """Extract cpu/mem/disk_* from a parsed Node Exporter scrape."""
        metrics: dict[str, float] = {}

        # Memory — stateless ratio
        mem_total_rows = parsed.get("node_memory_MemTotal_bytes", [])
        mem_avail_rows = parsed.get("node_memory_MemAvailable_bytes", [])
        if mem_total_rows and mem_avail_rows:
            mem_total = mem_total_rows[0][1]
            mem_avail = mem_avail_rows[0][1]
            if mem_total > 0:
                metrics["mem"] = round((1.0 - mem_avail / mem_total) * 100.0, 1)

        # Disk — stateless ratio, noisy mounts excluded
        size_by_mount = {
            lbl.get("mountpoint", ""): val
            for lbl, val in parsed.get("node_filesystem_size_bytes", [])
        }
        avail_by_mount = {
            lbl.get("mountpoint", ""): val
            for lbl, val in parsed.get("node_filesystem_avail_bytes", [])
        }
        fstype_by_mount = {
            lbl.get("mountpoint", ""): lbl.get("fstype", "")
            for lbl, _val in parsed.get("node_filesystem_size_bytes", [])
        }
        for mount, size in size_by_mount.items():
            if not mount:
                continue
            if fstype_by_mount.get(mount, "") in _NOISY_FSTYPES:
                continue
            if _NOISY_MOUNT_RE.match(mount):
                continue
            avail = avail_by_mount.get(mount)
            if avail is not None and size > 0:
                pct = round((1.0 - avail / size) * 100.0, 1)
                if 0.0 <= pct <= 100.0:
                    metrics[f"disk_{mount}"] = pct

        # CPU — stateful counter delta (needs two samples)
        cpu_rows = parsed.get("node_cpu_seconds_total", [])
        if cpu_rows:
            total_now = sum(val for _, val in cpu_rows)
            idle_now = sum(
                val for lbl, val in cpu_rows if lbl.get("mode") == "idle"
            )
            prev = self._cpu_prev.get(vm_id)
            if prev is not None:
                _prev_ts, prev_idle, prev_total = prev
                total_delta = total_now - prev_total
                idle_delta = idle_now - prev_idle
                if total_delta > 0:
                    cpu_pct = (1.0 - idle_delta / total_delta) * 100.0
                    metrics["cpu"] = round(max(0.0, min(100.0, cpu_pct)), 1)
            # Always update prev so next call has a baseline.
            self._cpu_prev[vm_id] = (now, idle_now, total_now)

        return metrics

    # ------------------------------------------------------------------
    # SSH probe path
    # ------------------------------------------------------------------

    async def _probe_ssh(
        self,
        target: VMTarget,
        timeout: float = 8.0,
    ) -> dict[str, float] | None:
        try:
            import asyncssh  # already a project dependency
        except ImportError:
            logger.warning(
                "asyncssh not available — SSH probe disabled for %s", target.vm_id
            )
            return None

        async def _run(conn: Any) -> Any:
            return await asyncio.wait_for(
                conn.run(_PROBE_CMD, check=False), timeout=timeout
            )

        # Try cached connection first.
        conn = self._ssh_conns.get(target.vm_id)
        result = None

        if conn is not None:
            try:
                result = await _run(conn)
            except Exception:
                # Stale — evict and reconnect below.
                self._ssh_conns.pop(target.vm_id, None)
                conn = None

        # Open a fresh connection if needed.
        if conn is None:
            try:
                conn = await asyncio.wait_for(
                    asyncssh.connect(
                        target.hostname,
                        username=target.ssh_user,
                        client_keys=[target.ssh_key_path],
                        known_hosts=None,
                        password=None,
                        connect_timeout=5,
                    ),
                    timeout=timeout,
                )
                self._ssh_conns[target.vm_id] = conn
            except TimeoutError:
                logger.debug("probe_ssh %s: connect timeout", target.vm_id)
                return None
            except Exception as exc:  # noqa: BLE001
                logger.debug("probe_ssh %s: connect failed: %s", target.vm_id, exc)
                return None

            try:
                result = await _run(conn)
            except Exception as exc:  # noqa: BLE001
                logger.debug("probe_ssh %s: run failed: %s", target.vm_id, exc)
                self._ssh_conns.pop(target.vm_id, None)
                return None

        if result is None or (result.exit_status != 0 and not result.stdout):
            logger.debug(
                "probe_ssh %s: bad exit %s",
                target.vm_id,
                result.exit_status if result else "no result",
            )
            return None

        metrics = parse_probe_output(result.stdout or "")
        if not metrics:
            logger.debug("probe_ssh %s: empty parse", target.vm_id)
            return None
        logger.debug("probe_ssh %s: %s", target.vm_id, metrics)
        return metrics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=32),
            )
        return self._http_session


# ---------------------------------------------------------------------------
# Retention cleanup (module-level — called by scheduler independently)
# ---------------------------------------------------------------------------

async def cleanup_old_metrics(
    db: aiosqlite.Connection,
    retention_days: int = 8,
) -> None:
    """Delete rows older than retention_days from vm_metrics. Called hourly."""
    cutoff = int(time.time()) - retention_days * 86400
    try:
        await db.execute("DELETE FROM vm_metrics WHERE ts < ?", (cutoff,))
        await db.commit()
        logger.debug(
            "cleanup_old_metrics: deleted rows older than %d days", retention_days
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup_old_metrics failed: %s", exc)


# ---------------------------------------------------------------------------
# Query — time-windowed with downsampling
# ---------------------------------------------------------------------------

# Window spec: (lookback_seconds, bucket_seconds)
# 15m / 1h: bucket=60 (raw 1-min rows).
# 24h:      bucket=300 (5-min AVG → max 288 pts).
# 7d:       bucket=3600 (1-hr AVG → max 168 pts).
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

    Returns:
        {
          'cpu':  [[ts, value_pct], ...],   # ordered ASC
          'mem':  [[ts, value_pct], ...],
          'disk': {mountpoint: [[ts, value_pct], ...], ...}
        }
        Empty lists/dicts when no data is available.
    """
    lookback, bucket = _WINDOWS.get(window, _WINDOWS["24h"])
    since = int(time.time()) - lookback

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
            mount = metric[5:]
            result["disk"].setdefault(mount, []).append(entry)

    return result
