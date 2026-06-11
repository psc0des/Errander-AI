"""Tests for drift detection module."""

from __future__ import annotations

import pytest

from errander.models.events import EventType
from errander.safety.audit import AuditStore
from errander.safety.drift import DriftResult, compare_states, load_baseline, save_baseline
from tests.conftest import make_test_db


def _baseline(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "os_version": "Ubuntu 22.04",
        "disk_usage": {"/": 45.0},
        "docker_available": True,
        "uptime_seconds": 86400.0,
        "pending_packages": 3,
    }
    defaults.update(overrides)
    return defaults


def _current(**overrides: object) -> dict[str, object]:
    return _baseline(**overrides)


class TestCompareStates:
    def test_no_drift_identical(self) -> None:
        b = _baseline()
        result = compare_states(b, _current())
        assert result.has_drift is False
        assert result.drifts == []
        assert result.baseline_found is True

    def test_os_version_drift(self) -> None:
        b = _baseline(os_version="Ubuntu 22.04")
        c = _current(os_version="Ubuntu 24.04")
        result = compare_states(b, c)
        assert result.has_drift is True
        assert any("OS version changed" in d for d in result.drifts)

    def test_disk_drift_above_threshold(self) -> None:
        b = _baseline(disk_usage={"/": 40.0})
        c = _current(disk_usage={"/": 65.0})
        result = compare_states(b, c)
        assert result.has_drift is True
        assert any("Disk usage on /" in d for d in result.drifts)

    def test_disk_no_drift_within_threshold(self) -> None:
        b = _baseline(disk_usage={"/": 40.0})
        c = _current(disk_usage={"/": 55.0})  # delta=15, below 20 threshold
        result = compare_states(b, c, disk_threshold=20.0)
        assert result.has_drift is False

    def test_disk_drift_exact_threshold_not_triggered(self) -> None:
        # delta == threshold: NOT flagged (must be strictly >)
        b = _baseline(disk_usage={"/": 40.0})
        c = _current(disk_usage={"/": 60.0})  # delta=20
        result = compare_states(b, c, disk_threshold=20.0)
        assert result.has_drift is False

    def test_docker_availability_drift(self) -> None:
        b = _baseline(docker_available=True)
        c = _current(docker_available=False)
        result = compare_states(b, c)
        assert result.has_drift is True
        assert any("Docker availability changed" in d for d in result.drifts)

    def test_docker_no_drift_same_value(self) -> None:
        b = _baseline(docker_available=False)
        c = _current(docker_available=False)
        result = compare_states(b, c)
        assert result.has_drift is False

    def test_uptime_reset_rebooted(self) -> None:
        b = _baseline(uptime_seconds=86400.0)
        c = _current(uptime_seconds=300.0)
        result = compare_states(b, c)
        assert result.has_drift is True
        assert any("VM was rebooted" in d for d in result.drifts)

    def test_uptime_no_drift_increasing(self) -> None:
        b = _baseline(uptime_seconds=86400.0)
        c = _current(uptime_seconds=172800.0)
        result = compare_states(b, c)
        assert result.has_drift is False

    def test_package_drift_above_threshold(self) -> None:
        b = _baseline(pending_packages=2)
        c = _current(pending_packages=10)
        result = compare_states(b, c)
        assert result.has_drift is True
        assert any("Pending packages changed" in d for d in result.drifts)

    def test_package_no_drift_small_delta(self) -> None:
        b = _baseline(pending_packages=5)
        c = _current(pending_packages=8)  # delta=3, below 5 threshold
        result = compare_states(b, c)
        assert result.has_drift is False

    def test_package_drift_exact_threshold_not_triggered(self) -> None:
        # delta == 5: NOT flagged (must be strictly >5)
        b = _baseline(pending_packages=0)
        c = _current(pending_packages=5)
        result = compare_states(b, c)
        assert result.has_drift is False

    def test_multiple_drifts_simultaneously(self) -> None:
        b = _baseline(os_version="Ubuntu 22.04", docker_available=True, pending_packages=0)
        c = _current(os_version="Ubuntu 24.04", docker_available=False, pending_packages=20)
        result = compare_states(b, c)
        assert result.has_drift is True
        assert len(result.drifts) >= 3

    def test_drift_result_dataclass_fields(self) -> None:
        r = DriftResult(has_drift=True, drifts=["something changed"], baseline_found=True)
        assert r.has_drift is True
        assert r.drifts == ["something changed"]
        assert r.baseline_found is True


class TestSaveAndLoadBaseline:
    @pytest.mark.asyncio
    async def test_save_baseline_stores_event(self) -> None:
        async with AuditStore(make_test_db()) as store:
            vm_info: dict[str, object] = {"os_version": "Ubuntu 22.04", "disk_usage": {"/": 45.0}}
            await save_baseline(store, "dev/web-01", vm_info)

            events = await store.get_events(
                vm_id="dev/web-01",
                event_type=EventType.DRIFT_BASELINE_SAVED,
            )
        assert len(events) == 1
        assert events[0].event_type == EventType.DRIFT_BASELINE_SAVED
        assert "baseline" in events[0].metadata

    @pytest.mark.asyncio
    async def test_load_baseline_returns_data(self) -> None:
        async with AuditStore(make_test_db()) as store:
            vm_info: dict[str, object] = {"os_version": "Ubuntu 22.04", "disk_usage": {"/": 50.0}}
            await save_baseline(store, "dev/web-01", vm_info)
            loaded = await load_baseline(store, "dev/web-01")

        assert loaded is not None
        assert loaded["os_version"] == "Ubuntu 22.04"
        assert loaded["disk_usage"] == {"/": 50.0}

    @pytest.mark.asyncio
    async def test_load_baseline_no_data_returns_none(self) -> None:
        async with AuditStore(make_test_db()) as store:
            loaded = await load_baseline(store, "dev/unknown-vm")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_load_baseline_returns_most_recent(self) -> None:
        """When multiple baselines exist, the most recent is returned."""
        async with AuditStore(make_test_db()) as store:
            await save_baseline(store, "dev/web-01", {"os_version": "Ubuntu 22.04"})
            await save_baseline(store, "dev/web-01", {"os_version": "Ubuntu 24.04"})
            loaded = await load_baseline(store, "dev/web-01")

        assert loaded is not None
        # Most recent baseline should be the last one saved
        assert loaded["os_version"] == "Ubuntu 24.04"
