"""Tests for VMDiskHistoryStore — disk usage trend tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from errander.safety.disk_history import VMDiskHistoryStore
from errander.safety.migrations import run_migrations

_GB = 1_000_000_000


async def _make_store() -> VMDiskHistoryStore:
    store = VMDiskHistoryStore(":memory:")
    await store.initialize()
    assert store._db is not None
    await run_migrations(store._db)
    return store


def _ts(days_ago: int = 0) -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=days_ago)


class TestVMDiskHistoryStoreLifecycle:
    async def test_context_manager(self) -> None:
        store = VMDiskHistoryStore(":memory:")
        await store.initialize()
        assert store._db is not None
        await store.close()
        assert store._db is None

    async def test_operations_without_init_raise(self) -> None:
        store = VMDiskHistoryStore(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            await store.get_window("dev/web-01", "/", 7)


class TestVMDiskHistoryRecord:
    async def test_record_single_datapoint(self) -> None:
        store = await _make_store()
        await store.record("dev/web-01", "/", 10 * _GB, 50 * _GB)
        points = await store.get_window("dev/web-01", "/", 7)
        assert len(points) == 1
        assert points[0].mountpoint == "/"
        assert points[0].used_bytes == 10 * _GB
        assert points[0].total_bytes == 50 * _GB
        await store.close()

    async def test_record_batch_multiple_mountpoints(self) -> None:
        store = await _make_store()
        await store.record_batch("dev/web-01", [
            ("/", 10 * _GB, 50 * _GB),
            ("/var", 5 * _GB, 20 * _GB),
        ])
        root = await store.get_window("dev/web-01", "/", 7)
        var = await store.get_window("dev/web-01", "/var", 7)
        assert len(root) == 1
        assert len(var) == 1
        await store.close()

    async def test_record_batch_empty_is_noop(self) -> None:
        store = await _make_store()
        await store.record_batch("dev/web-01", [])
        points = await store.get_window("dev/web-01", "/", 7)
        assert len(points) == 0
        await store.close()

    async def test_used_pct_computed_correctly(self) -> None:
        store = await _make_store()
        await store.record("dev/web-01", "/", 25 * _GB, 100 * _GB)
        points = await store.get_window("dev/web-01", "/", 7)
        assert abs(points[0].used_pct - 25.0) < 0.01
        await store.close()

    async def test_used_pct_zero_total(self) -> None:
        store = await _make_store()
        await store.record("dev/web-01", "/proc", 0, 0)
        points = await store.get_window("dev/web-01", "/proc", 7)
        assert points[0].used_pct == 0.0
        await store.close()


class TestVMDiskHistoryWindow:
    async def test_window_filters_by_age(self) -> None:
        store = await _make_store()
        # Old point (10 days ago) — outside 7-day window
        old_ts = _ts(days_ago=10)
        await store.record("dev/web-01", "/", 10 * _GB, 50 * _GB, captured_at=old_ts)
        # Recent point (1 day ago) — inside window
        recent_ts = _ts(days_ago=1)
        await store.record("dev/web-01", "/", 15 * _GB, 50 * _GB, captured_at=recent_ts)

        points = await store.get_window("dev/web-01", "/", 7)
        assert len(points) == 1
        assert points[0].used_bytes == 15 * _GB
        await store.close()

    async def test_window_returns_oldest_to_newest(self) -> None:
        store = await _make_store()
        # Simulate disk filling up: oldest point has fewest bytes
        pairs = [(5, 10 * _GB), (3, 15 * _GB), (1, 20 * _GB)]  # (days_ago, used_bytes)
        for days_ago, used in pairs:
            await store.record("dev/web-01", "/", used, 50 * _GB, captured_at=_ts(days_ago))
        points = await store.get_window("dev/web-01", "/", 7)
        assert len(points) == 3
        # Oldest first → used_bytes grows across time
        assert points[0].used_bytes < points[-1].used_bytes
        await store.close()

    async def test_window_isolated_by_mountpoint(self) -> None:
        store = await _make_store()
        await store.record("dev/web-01", "/", 10 * _GB, 50 * _GB)
        await store.record("dev/web-01", "/var", 5 * _GB, 20 * _GB)
        root = await store.get_window("dev/web-01", "/", 7)
        var = await store.get_window("dev/web-01", "/var", 7)
        assert len(root) == 1 and root[0].mountpoint == "/"
        assert len(var) == 1 and var[0].mountpoint == "/var"
        await store.close()


class TestVMDiskHistoryPrune:
    async def test_prune_removes_old_records(self) -> None:
        store = await _make_store()
        old_ts = _ts(days_ago=100)
        await store.record("dev/web-01", "/", 10 * _GB, 50 * _GB, captured_at=old_ts)
        recent_ts = _ts(days_ago=1)
        await store.record("dev/web-01", "/", 12 * _GB, 50 * _GB, captured_at=recent_ts)
        deleted = await store.prune_old_records(retention_days=90)
        assert deleted == 1
        points = await store.get_window("dev/web-01", "/", 400)
        assert len(points) == 1
        assert points[0].used_bytes == 12 * _GB
        await store.close()

    async def test_prune_returns_zero_when_nothing_old(self) -> None:
        store = await _make_store()
        await store.record("dev/web-01", "/", 10 * _GB, 50 * _GB)
        deleted = await store.prune_old_records(retention_days=90)
        assert deleted == 0
        await store.close()


class TestVMDiskHistoryMountpoints:
    async def test_get_distinct_mountpoints(self) -> None:
        store = await _make_store()
        await store.record_batch("dev/web-01", [
            ("/", 10 * _GB, 50 * _GB),
            ("/var", 5 * _GB, 20 * _GB),
            ("/tmp", 1 * _GB, 10 * _GB),
        ])
        mounts = await store.get_distinct_mountpoints("dev/web-01")
        assert set(mounts) == {"/", "/var", "/tmp"}
        await store.close()

    async def test_get_distinct_mountpoints_empty_vm(self) -> None:
        store = await _make_store()
        mounts = await store.get_distinct_mountpoints("dev/unknown")
        assert mounts == []
        await store.close()
