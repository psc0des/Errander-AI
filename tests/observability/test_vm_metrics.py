"""Tests for errander.observability.vm_metrics.

Covers:
  - _parse_prom_text: Prometheus exposition format parser
  - MetricsCollector._extract_ne_metrics: mem / disk / CPU delta logic
  - parse_probe_output: SSH probe output parser
  - MetricsCollector.discover: source selection (Node Exporter vs SSH probe)
  - MetricsCollector.collect_all: end-to-end write to in-memory DB
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from errander.observability.vm_metrics import (
    MetricsCollector,
    _NOISY_FSTYPES,
    _parse_prom_text,
    parse_probe_output,
    query_metrics,
)
from errander.safety.migrations import run_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_target(
    vm_id: str = "test-vm-01",
    hostname: str = "10.0.0.1",
    node_exporter: bool = False,
) -> MagicMock:
    t = MagicMock()
    t.vm_id = vm_id
    t.hostname = hostname
    t.ssh_user = "ubuntu"
    t.ssh_key_path = "/keys/test.pem"
    t.node_exporter = node_exporter
    return t


async def _mem_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    await run_migrations(db)
    return db


# ---------------------------------------------------------------------------
# _parse_prom_text
# ---------------------------------------------------------------------------

class TestParsePromText:
    def test_basic_gauge_no_labels(self) -> None:
        text = "node_memory_MemTotal_bytes 8589934592\n"
        parsed = _parse_prom_text(text)
        assert "node_memory_MemTotal_bytes" in parsed
        entries = parsed["node_memory_MemTotal_bytes"]
        assert len(entries) == 1
        labels, value = entries[0]
        assert labels == {}
        assert value == pytest.approx(8589934592.0)

    def test_gauge_with_labels(self) -> None:
        text = 'node_filesystem_size_bytes{device="/dev/sda1",mountpoint="/"} 53687091200\n'
        parsed = _parse_prom_text(text)
        entries = parsed["node_filesystem_size_bytes"]
        assert len(entries) == 1
        labels, value = entries[0]
        assert labels["device"] == "/dev/sda1"
        assert labels["mountpoint"] == "/"
        assert value == pytest.approx(53687091200.0)

    def test_multiple_entries_same_metric(self) -> None:
        text = (
            'node_cpu_seconds_total{cpu="0",mode="idle"} 12345.67\n'
            'node_cpu_seconds_total{cpu="0",mode="user"} 456.78\n'
            'node_cpu_seconds_total{cpu="1",mode="idle"} 11111.0\n'
        )
        parsed = _parse_prom_text(text)
        entries = parsed["node_cpu_seconds_total"]
        assert len(entries) == 3
        modes = [lbl["mode"] for lbl, _ in entries]
        assert modes.count("idle") == 2
        assert modes.count("user") == 1

    def test_skips_help_and_type_comments(self) -> None:
        text = (
            "# HELP node_memory_MemTotal_bytes Total memory\n"
            "# TYPE node_memory_MemTotal_bytes gauge\n"
            "node_memory_MemTotal_bytes 1024\n"
        )
        parsed = _parse_prom_text(text)
        assert list(parsed.keys()) == ["node_memory_MemTotal_bytes"]

    def test_skips_empty_lines(self) -> None:
        text = "\n\nnode_memory_MemTotal_bytes 1024\n\n"
        parsed = _parse_prom_text(text)
        assert "node_memory_MemTotal_bytes" in parsed

    def test_scientific_notation_value(self) -> None:
        text = "node_memory_MemTotal_bytes 8.589934592e+09\n"
        parsed = _parse_prom_text(text)
        val = parsed["node_memory_MemTotal_bytes"][0][1]
        assert val == pytest.approx(8.589934592e9)

    def test_malformed_lines_silently_skipped(self) -> None:
        text = (
            "not_a_metric\n"
            "node_memory_MemTotal_bytes 1024\n"
            "broken{labels=no_value\n"
        )
        parsed = _parse_prom_text(text)
        # Only the valid line should be parsed
        assert "node_memory_MemTotal_bytes" in parsed
        assert len(parsed) == 1

    def test_negative_value(self) -> None:
        text = "some_metric -1.5\n"
        parsed = _parse_prom_text(text)
        assert parsed["some_metric"][0][1] == pytest.approx(-1.5)


# ---------------------------------------------------------------------------
# MetricsCollector._extract_ne_metrics
# ---------------------------------------------------------------------------

class TestExtractNeMetrics:
    def _collector(self) -> MetricsCollector:
        return MetricsCollector()

    def _mem_parsed(
        self,
        total_bytes: float = 8_000_000_000.0,
        avail_bytes: float = 4_000_000_000.0,
    ) -> dict:
        return {
            "node_memory_MemTotal_bytes": [({}, total_bytes)],
            "node_memory_MemAvailable_bytes": [({}, avail_bytes)],
        }

    def test_mem_computed_correctly(self) -> None:
        c = self._collector()
        parsed = self._mem_parsed(total_bytes=8e9, avail_bytes=2e9)
        metrics = c._extract_ne_metrics("vm1", parsed, time.time())
        # used = 6e9, total = 8e9 → 75%
        assert metrics["mem"] == pytest.approx(75.0, abs=0.1)

    def test_mem_100_percent_when_avail_zero(self) -> None:
        c = self._collector()
        parsed = self._mem_parsed(total_bytes=8e9, avail_bytes=0.0)
        metrics = c._extract_ne_metrics("vm1", parsed, time.time())
        assert metrics["mem"] == pytest.approx(100.0, abs=0.1)

    def test_mem_absent_when_no_data(self) -> None:
        c = self._collector()
        metrics = c._extract_ne_metrics("vm1", {}, time.time())
        assert "mem" not in metrics

    def test_disk_real_filesystem_included(self) -> None:
        c = self._collector()
        parsed = {
            "node_filesystem_size_bytes": [
                ({"mountpoint": "/", "fstype": "ext4"}, 50_000_000_000.0),
            ],
            "node_filesystem_avail_bytes": [
                ({"mountpoint": "/"}, 25_000_000_000.0),
            ],
        }
        metrics = c._extract_ne_metrics("vm1", parsed, time.time())
        assert "disk_/" in metrics
        assert metrics["disk_/"] == pytest.approx(50.0, abs=0.1)

    def test_disk_noisy_fstype_excluded(self) -> None:
        c = self._collector()
        for fstype in ["tmpfs", "overlay", "devtmpfs", "squashfs"]:
            parsed = {
                "node_filesystem_size_bytes": [
                    ({"mountpoint": "/run", "fstype": fstype}, 1_000_000_000.0),
                ],
                "node_filesystem_avail_bytes": [
                    ({"mountpoint": "/run"}, 500_000_000.0),
                ],
            }
            metrics = c._extract_ne_metrics("vm1", parsed, time.time())
            assert "disk_/run" not in metrics, f"fstype {fstype!r} should be excluded"

    def test_disk_noisy_mountpoint_excluded(self) -> None:
        c = self._collector()
        for mount in ["/proc", "/sys", "/run/user", "/snap/foo", "/dev/shm"]:
            parsed = {
                "node_filesystem_size_bytes": [
                    ({"mountpoint": mount, "fstype": "ext4"}, 1_000_000_000.0),
                ],
                "node_filesystem_avail_bytes": [
                    ({"mountpoint": mount}, 500_000_000.0),
                ],
            }
            metrics = c._extract_ne_metrics("vm1", parsed, time.time())
            assert f"disk_{mount}" not in metrics, f"mount {mount!r} should be excluded"

    def test_cpu_first_scrape_no_cpu_metric(self) -> None:
        c = self._collector()
        parsed = {
            "node_cpu_seconds_total": [
                ({"cpu": "0", "mode": "idle"}, 1000.0),
                ({"cpu": "0", "mode": "user"}, 200.0),
                ({"cpu": "0", "mode": "system"}, 100.0),
            ],
        }
        metrics = c._extract_ne_metrics("vm1", parsed, time.time())
        # First scrape stores baseline — no cpu yet
        assert "cpu" not in metrics

    def test_cpu_second_scrape_computes_delta(self) -> None:
        c = self._collector()
        now = time.time()
        # First scrape: 1000s idle, 300s non-idle → total = 1300s
        parsed1 = {
            "node_cpu_seconds_total": [
                ({"cpu": "0", "mode": "idle"}, 1000.0),
                ({"cpu": "0", "mode": "user"}, 200.0),
                ({"cpu": "0", "mode": "system"}, 100.0),
            ],
        }
        c._extract_ne_metrics("vm1", parsed1, now)

        # Second scrape: 60s later — 50s idle, 10s non-idle → 50/60 idle → 16.7% CPU
        parsed2 = {
            "node_cpu_seconds_total": [
                ({"cpu": "0", "mode": "idle"}, 1050.0),    # +50s idle
                ({"cpu": "0", "mode": "user"}, 207.0),     # +7s user
                ({"cpu": "0", "mode": "system"}, 103.0),   # +3s system
            ],
        }
        metrics = c._extract_ne_metrics("vm1", parsed2, now + 60)
        assert "cpu" in metrics
        # total_delta = 60, idle_delta = 50 → cpu = (1 - 50/60)*100 ≈ 16.7%
        assert metrics["cpu"] == pytest.approx(16.7, abs=0.2)

    def test_cpu_clamped_at_100(self) -> None:
        c = self._collector()
        now = time.time()
        # Impossible data (counter went backwards on one mode) that yields >100%
        parsed1 = {"node_cpu_seconds_total": [({"cpu": "0", "mode": "idle"}, 1000.0)]}
        c._extract_ne_metrics("vm1", parsed1, now)
        # Second scrape: zero idle increase but huge total increase
        parsed2 = {"node_cpu_seconds_total": [({"cpu": "0", "mode": "idle"}, 1000.0),
                                               ({"cpu": "0", "mode": "user"}, 999.0)]}
        metrics = c._extract_ne_metrics("vm1", parsed2, now + 60)
        assert metrics.get("cpu", 100.0) <= 100.0

    def test_cpu_clamped_at_0_on_counter_reset(self) -> None:
        c = self._collector()
        now = time.time()
        # First scrape: large values
        parsed1 = {
            "node_cpu_seconds_total": [
                ({"cpu": "0", "mode": "idle"}, 99999.0),
                ({"cpu": "0", "mode": "user"}, 9999.0),
            ],
        }
        c._extract_ne_metrics("vm1", parsed1, now)
        # Counter reset (reboot) — values start from 0
        parsed2 = {
            "node_cpu_seconds_total": [
                ({"cpu": "0", "mode": "idle"}, 1.0),
                ({"cpu": "0", "mode": "user"}, 0.0),
            ],
        }
        metrics = c._extract_ne_metrics("vm1", parsed2, now + 60)
        # total_delta negative → clamped to 0 (or no cpu key if total_delta <= 0)
        assert metrics.get("cpu", 0.0) >= 0.0


# ---------------------------------------------------------------------------
# parse_probe_output (SSH probe parser)
# ---------------------------------------------------------------------------

class TestParseProbeOutput:
    def test_basic_cpu_mem_disk(self) -> None:
        output = "CPU=42\nMEM=78\nDISK=/=38\nDISK=/var=52\n"
        metrics = parse_probe_output(output)
        assert metrics["cpu"] == pytest.approx(42.0)
        assert metrics["mem"] == pytest.approx(78.0)
        assert metrics["disk_/"] == pytest.approx(38.0)
        assert metrics["disk_/var"] == pytest.approx(52.0)

    def test_skips_out_of_range_values(self) -> None:
        output = "CPU=150\nMEM=-5\nDISK=/=200\n"
        metrics = parse_probe_output(output)
        assert "cpu" not in metrics
        assert "mem" not in metrics
        assert "disk_/" not in metrics

    def test_skips_malformed_lines(self) -> None:
        output = "CPU=42\nnot_valid\nMEM=78\n"
        metrics = parse_probe_output(output)
        assert "cpu" in metrics
        assert "mem" in metrics
        assert len(metrics) == 2

    def test_empty_output_returns_empty(self) -> None:
        assert parse_probe_output("") == {}

    def test_disk_path_with_subpath(self) -> None:
        output = "DISK=/home/ubuntu=15\n"
        metrics = parse_probe_output(output)
        assert "disk_/home/ubuntu" in metrics
        assert metrics["disk_/home/ubuntu"] == pytest.approx(15.0)

    def test_boundary_values_accepted(self) -> None:
        output = "CPU=0\nMEM=100\nDISK=/=0\n"
        metrics = parse_probe_output(output)
        assert metrics["cpu"] == pytest.approx(0.0)
        assert metrics["mem"] == pytest.approx(100.0)
        assert metrics["disk_/"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# MetricsCollector.discover
# ---------------------------------------------------------------------------

class TestMetricsCollectorDiscover:
    """discover() is now flag-driven (target.node_exporter).

    node_exporter=True  → verify :9100, use NE if running, SSH probe if not.
    node_exporter=False → SSH probe always, no HTTP check.
    """

    def _make_session(self, status: int = 200, side_effect: Exception | None = None) -> MagicMock:
        mock_resp = AsyncMock()
        mock_resp.status = status
        if side_effect:
            mock_resp.__aenter__ = AsyncMock(side_effect=side_effect)
        else:
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        return mock_session

    async def test_flag_true_and_ne_running(self) -> None:
        c = MetricsCollector()
        target = _fake_target("vm1", "10.0.0.1", node_exporter=True)
        c._http_session = self._make_session(status=200)
        await c.discover([target])
        assert c.source_map["vm1"] == "node_exporter"
        await c.close()

    async def test_flag_true_but_ne_not_running_falls_back(self) -> None:
        c = MetricsCollector()
        target = _fake_target("vm2", "10.0.0.2", node_exporter=True)
        c._http_session = self._make_session(side_effect=OSError("refused"))
        await c.discover([target])
        # Flag is true but :9100 is down → SSH probe fallback
        assert c.source_map["vm2"] == "ssh_probe"
        await c.close()

    async def test_flag_true_but_ne_non_200_falls_back(self) -> None:
        c = MetricsCollector()
        target = _fake_target("vm3", "10.0.0.3", node_exporter=True)
        c._http_session = self._make_session(status=503)
        await c.discover([target])
        assert c.source_map["vm3"] == "ssh_probe"
        await c.close()

    async def test_flag_false_uses_ssh_probe_without_http_check(self) -> None:
        c = MetricsCollector()
        target = _fake_target("vm4", "10.0.0.4", node_exporter=False)
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        c._http_session = mock_session

        await c.discover([target])

        assert c.source_map["vm4"] == "ssh_probe"
        # No HTTP request made when flag is false
        mock_session.get.assert_not_called()
        await c.close()

    async def test_mixed_fleet(self) -> None:
        c = MetricsCollector()
        ne_target = _fake_target("ne-vm", "10.0.0.10", node_exporter=True)
        ssh_target = _fake_target("ssh-vm", "10.0.0.11", node_exporter=False)

        # ne-vm will trigger an HTTP check; ssh-vm will not
        def _fake_get(url: str, **kwargs: object) -> AsyncMock:
            resp = AsyncMock()
            resp.status = 200
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            return resp

        mock_session = MagicMock()
        mock_session.get = _fake_get
        mock_session.closed = False
        mock_session.close = AsyncMock()
        c._http_session = mock_session

        await c.discover([ne_target, ssh_target])
        assert c.source_map["ne-vm"] == "node_exporter"
        assert c.source_map["ssh-vm"] == "ssh_probe"
        await c.close()


# ---------------------------------------------------------------------------
# MetricsCollector.collect_all — integration with in-memory DB
# ---------------------------------------------------------------------------

class TestCollectAll:
    async def test_writes_rows_to_db(self) -> None:
        db = await _mem_db()
        c = MetricsCollector()
        target = _fake_target("prod-api-01", "10.1.1.10")
        c._source["prod-api-01"] = "node_exporter"

        # Mock _probe_node_exporter to return fixture metrics
        async def _fake_probe(t: object) -> dict[str, float]:
            return {"cpu": 42.0, "mem": 78.0, "disk_/": 38.0}

        c._probe_node_exporter = _fake_probe  # type: ignore[method-assign]

        await c.collect_all(db, [target])

        cursor = await db.execute(
            "SELECT metric, value_pct FROM vm_metrics WHERE hostname=?",
            ("10.1.1.10",),
        )
        rows = {str(r[0]): float(str(r[1])) for r in await cursor.fetchall()}
        assert rows["cpu"] == pytest.approx(42.0)
        assert rows["mem"] == pytest.approx(78.0)
        assert rows["disk_/"] == pytest.approx(38.0)
        await db.close()

    async def test_skips_unreachable_vm(self) -> None:
        db = await _mem_db()
        c = MetricsCollector()
        target = _fake_target("unreachable", "10.1.1.99")
        c._source["unreachable"] = "ssh_probe"

        # Mock _probe_ssh to return None (unreachable)
        async def _fail(t: object, **kw: object) -> None:
            return None

        c._probe_ssh = _fail  # type: ignore[method-assign]

        await c.collect_all(db, [target])

        cursor = await db.execute("SELECT COUNT(*) FROM vm_metrics")
        row = await cursor.fetchone()
        assert int(str(row[0])) == 0  # nothing written
        await db.close()

    async def test_multiple_vms_independent(self) -> None:
        db = await _mem_db()
        c = MetricsCollector()
        targets = [
            _fake_target("vm-a", "10.0.0.1"),
            _fake_target("vm-b", "10.0.0.2"),
        ]
        c._source["vm-a"] = "node_exporter"
        c._source["vm-b"] = "node_exporter"

        call_log: list[str] = []

        async def _fake(t: MagicMock) -> dict[str, float]:
            call_log.append(t.vm_id)
            return {"cpu": 50.0, "mem": 60.0}

        c._probe_node_exporter = _fake  # type: ignore[method-assign]

        await c.collect_all(db, targets)

        assert sorted(call_log) == ["vm-a", "vm-b"]
        cursor = await db.execute("SELECT COUNT(DISTINCT hostname) FROM vm_metrics")
        row = await cursor.fetchone()
        assert int(str(row[0])) == 2
        await db.close()


# ---------------------------------------------------------------------------
# query_metrics — spot check (full DB path tested in test_migrations.py)
# ---------------------------------------------------------------------------

class TestQueryMetrics:
    async def test_returns_bucketed_rows(self) -> None:
        db = await _mem_db()
        now = int(time.time())
        rows = [
            ("host1", "cpu", 42.0, now - 120),
            ("host1", "cpu", 55.0, now - 60),
            ("host1", "cpu", 61.0, now),
            ("host1", "mem", 78.0, now),
            ("host1", "disk_/", 38.0, now),
        ]
        await db.executemany(
            "INSERT INTO vm_metrics (hostname, metric, value_pct, ts) VALUES (?,?,?,?)",
            rows,
        )
        await db.commit()

        result = await query_metrics(db, "host1", "15m")
        assert len(result["cpu"]) >= 1
        assert len(result["mem"]) == 1
        assert "/" in result["disk"]
        await db.close()

    async def test_empty_result_for_unknown_host(self) -> None:
        db = await _mem_db()
        result = await query_metrics(db, "ghost-host", "24h")
        assert result == {"cpu": [], "mem": [], "disk": {}}
        await db.close()

    async def test_invalid_window_defaults_to_24h(self) -> None:
        db = await _mem_db()
        # Should not raise — falls back to 24h bucket
        result = await query_metrics(db, "host1", "bogus")
        assert isinstance(result, dict)
        await db.close()
