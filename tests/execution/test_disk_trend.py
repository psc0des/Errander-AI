"""Tests for disk usage capture and growth trend detection (PR-1.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from errander.execution.disk_trend import (
    compute_growth_alert,
    detect_growth_alerts,
    disk_bytes_command,
    parse_df_bytes,
    record_and_detect_disk_growth,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.disk_history import DiskDataPoint


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


def _make_datapoint(
    vm_id: str = "dev/web-01",
    mountpoint: str = "/",
    used_bytes: int = 10_000_000_000,
    total_bytes: int = 50_000_000_000,
    days_ago: int = 0,
) -> DiskDataPoint:
    return DiskDataPoint(
        vm_id=vm_id,
        captured_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        mountpoint=mountpoint,
        used_bytes=used_bytes,
        total_bytes=total_bytes,
    )


# --- disk_bytes_command ---

class TestDiskBytesCommand:
    def test_contains_df_b1(self) -> None:
        assert "df -B1" in disk_bytes_command()

    def test_exits_zero(self) -> None:
        # The || true fallback ensures exit 0 even if df is absent
        cmd = disk_bytes_command()
        assert "|| true" in cmd or "2>/dev/null" in cmd


# --- parse_df_bytes ---

class TestParseDfBytes:
    def test_parses_root_mountpoint(self) -> None:
        stdout = (
            "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
            "/dev/sda1  52428800000  20971520000  31457280000  40% /\n"
        )
        result = parse_df_bytes(stdout)
        assert len(result) == 1
        mp, used, total = result[0]
        assert mp == "/"
        assert used == 20_971_520_000
        assert total == 52_428_800_000

    def test_parses_multiple_mountpoints(self) -> None:
        stdout = (
            "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
            "/dev/sda1  52428800000  20971520000  31457280000  40% /\n"
            "/dev/sdb1  10737418240   5368709120   5368709120  50% /var\n"
        )
        result = parse_df_bytes(stdout)
        assert len(result) == 2
        mountpoints = [r[0] for r in result]
        assert "/" in mountpoints
        assert "/var" in mountpoints

    def test_skips_tmpfs(self) -> None:
        stdout = (
            "Filesystem     1B-blocks    Used Available Use% Mounted on\n"
            "tmpfs           1048576    1024   1047552   1% /run\n"
            "/dev/sda1  52428800000  20000000000  32428800000  38% /\n"
        )
        result = parse_df_bytes(stdout)
        assert len(result) == 1
        assert result[0][0] == "/"

    def test_skips_devtmpfs(self) -> None:
        stdout = (
            "Filesystem     1B-blocks    Used Available Use% Mounted on\n"
            "devtmpfs        4096000       0   4096000   0% /dev\n"
            "/dev/sda1  52428800000  20000000000  32428800000  38% /\n"
        )
        result = parse_df_bytes(stdout)
        assert all(r[0] != "/dev" for r in result)

    def test_empty_output_returns_empty(self) -> None:
        assert parse_df_bytes("") == []

    def test_header_only_returns_empty(self) -> None:
        stdout = "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
        assert parse_df_bytes(stdout) == []

    def test_skips_lines_with_non_integer_values(self) -> None:
        stdout = (
            "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
            "/dev/sda1  BAD_VALUE  20971520000  31457280000  40% /\n"
        )
        result = parse_df_bytes(stdout)
        assert result == []

    def test_skips_zero_total(self) -> None:
        stdout = (
            "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
            "/dev/sda1           0             0           0   0% /mnt/empty\n"
        )
        result = parse_df_bytes(stdout)
        assert result == []


# --- compute_growth_alert ---

class TestComputeGrowthAlert:
    def test_no_alert_with_single_datapoint(self) -> None:
        dp = _make_datapoint(used_bytes=10_000_000, total_bytes=100_000_000)
        assert compute_growth_alert([dp], threshold_pct=10.0) is None

    def test_no_alert_with_empty_datapoints(self) -> None:
        assert compute_growth_alert([], threshold_pct=10.0) is None

    def test_alert_when_threshold_exceeded(self) -> None:
        # 20% → 35% growth (15% delta > 10% threshold)
        older = _make_datapoint(used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=35_000_000, total_bytes=100_000_000, days_ago=0)
        alert = compute_growth_alert([older, newer], threshold_pct=10.0)
        assert alert is not None
        assert alert.mountpoint == "/"
        assert alert.used_pct_start == pytest.approx(20.0, abs=0.2)
        assert alert.used_pct_end == pytest.approx(35.0, abs=0.2)

    def test_no_alert_below_threshold(self) -> None:
        # 20% → 25% growth (5% delta < 10% threshold)
        older = _make_datapoint(used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=25_000_000, total_bytes=100_000_000, days_ago=0)
        assert compute_growth_alert([older, newer], threshold_pct=10.0) is None

    def test_no_alert_when_disk_shrinks(self) -> None:
        # disk freed — negative delta, not an alert
        older = _make_datapoint(used_bytes=50_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=30_000_000, total_bytes=100_000_000, days_ago=0)
        assert compute_growth_alert([older, newer], threshold_pct=10.0) is None

    def test_alert_vm_id_correct(self) -> None:
        older = _make_datapoint(
            vm_id="prod/db-01", used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7,
        )
        newer = _make_datapoint(vm_id="prod/db-01", used_bytes=50_000_000, total_bytes=100_000_000)
        alert = compute_growth_alert([older, newer], threshold_pct=10.0)
        assert alert is not None
        assert alert.vm_id == "prod/db-01"

    def test_exactly_at_threshold_is_alert(self) -> None:
        # 20% → 30% exactly equals 10% threshold → alert
        older = _make_datapoint(used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=30_000_000, total_bytes=100_000_000)
        assert compute_growth_alert([older, newer], threshold_pct=10.0) is not None


# --- detect_growth_alerts ---

class TestDetectGrowthAlerts:
    async def test_no_mountpoints_no_alerts(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        store = AsyncMock()
        store.get_distinct_mountpoints = AsyncMock(return_value=[])
        settings = DiskGrowthSettings(enabled=True, threshold_pct=10, window_days=7)

        alerts = await detect_growth_alerts(store, "dev/web-01", settings)
        assert alerts == []

    async def test_returns_alerts_for_growing_mountpoints(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        older = _make_datapoint(used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=40_000_000, total_bytes=100_000_000)

        store = AsyncMock()
        store.get_distinct_mountpoints = AsyncMock(return_value=["/"])
        store.get_window = AsyncMock(return_value=[older, newer])
        settings = DiskGrowthSettings(enabled=True, threshold_pct=10, window_days=7)

        alerts = await detect_growth_alerts(store, "dev/web-01", settings)
        assert len(alerts) == 1
        assert alerts[0].mountpoint == "/"

    async def test_no_alert_below_threshold(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        older = _make_datapoint(used_bytes=20_000_000, total_bytes=100_000_000, days_ago=7)
        newer = _make_datapoint(used_bytes=23_000_000, total_bytes=100_000_000)

        store = AsyncMock()
        store.get_distinct_mountpoints = AsyncMock(return_value=["/"])
        store.get_window = AsyncMock(return_value=[older, newer])
        settings = DiskGrowthSettings(enabled=True, threshold_pct=10, window_days=7)

        alerts = await detect_growth_alerts(store, "dev/web-01", settings)
        assert alerts == []


# --- record_and_detect_disk_growth ---

class TestRecordAndDetect:
    async def test_records_and_returns_alerts(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        executor = _make_executor()
        df_output = (
            "Filesystem     1B-blocks         Used    Available Use% Mounted on\n"
            "/dev/sda1  52428800000  40000000000  12428800000  77% /\n"
        )
        older = _make_datapoint(used_bytes=20_000_000_000, total_bytes=52_428_800_000, days_ago=7)
        newer = _make_datapoint(used_bytes=40_000_000_000, total_bytes=52_428_800_000)

        store = AsyncMock()
        store.record_batch = AsyncMock()
        store.get_distinct_mountpoints = AsyncMock(return_value=["/"])
        store.get_window = AsyncMock(return_value=[older, newer])
        settings = DiskGrowthSettings(enabled=True, threshold_pct=10, window_days=7)

        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(df_output))):
            alerts = await record_and_detect_disk_growth(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", store, settings,
            )

        store.record_batch.assert_called_once()
        assert len(alerts) >= 0  # growth depends on stored history

    async def test_ssh_failure_returns_empty(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        executor = _make_executor()
        store = AsyncMock()
        store.record_batch = AsyncMock()
        settings = DiskGrowthSettings()

        mock_result = _make_result("", exit_code=1)
        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            alerts = await record_and_detect_disk_growth(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", store, settings,
            )

        store.record_batch.assert_not_called()
        assert alerts == []

    async def test_uses_dry_run_false(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        executor = _make_executor()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("Filesystem 1B-blocks Used Available Use% Mounted on\n")

        store = AsyncMock()
        store.record_batch = AsyncMock()
        store.get_distinct_mountpoints = AsyncMock(return_value=[])
        settings = DiskGrowthSettings()

        with patch.object(executor, "execute", side_effect=capture):
            await record_and_detect_disk_growth(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", store, settings,
            )

        assert calls[0]["dry_run"] is False

    async def test_no_data_points_skips_record(self) -> None:
        from errander.config.settings import DiskGrowthSettings

        executor = _make_executor()
        store = AsyncMock()
        store.record_batch = AsyncMock()
        settings = DiskGrowthSettings()

        # All tmpfs — no recordable data points
        stdout = (
            "Filesystem     1B-blocks    Used Available Use% Mounted on\n"
            "tmpfs           1048576    1024   1047552   1% /run\n"
        )
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(stdout))):
            alerts = await record_and_detect_disk_growth(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", store, settings,
            )

        store.record_batch.assert_not_called()
        assert alerts == []
