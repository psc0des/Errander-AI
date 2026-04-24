"""Tests for rolling update wave logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    _partition_into_waves,
    check_wave_health_node,
    prepare_waves_node,
    route_after_prepare_waves,
    route_after_wave_health,
)
from errander.execution.ssh import SSHConnectionManager, SSHResult


# --- Helpers ---

def _make_target(vm_id: str = "dev/web-01") -> dict[str, object]:
    return {
        "vm_id": vm_id,
        "hostname": "10.0.1.10",
        "ssh_user": "errander-ai",
        "ssh_key_path": "/keys/id_ed25519",
        "os_family": "ubuntu",
    }


def _make_targets(n: int) -> list[dict[str, object]]:
    return [_make_target(vm_id=f"dev/web-{i:02d}") for i in range(1, n + 1)]


def _make_ssh_result(success: bool = True) -> SSHResult:
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    return SSHResult(
        exit_code=0 if success else 1,
        stdout="ok" if success else "",
        stderr="",
        command="echo ok",
        duration_seconds=0.01,
        started_at=now,
        completed_at=now,
    )


def _base_batch_state(**overrides: object) -> BatchGraphState:
    defaults: BatchGraphState = {
        "batch_id": "batch-test-001",
        "dry_run": True,
        "force": False,
        "force_reason": "",
        "targets": [],
        "healthy_targets": [],
        "failed_targets": [],
        "vm_results": [],
        "report": "",
        "error": None,
        "rolling_update_percentage": 100,
        "wave_failure_threshold": 0.5,
        "health_check_command": "echo ok",
        "current_wave": 0,
        "total_waves": 0,
        "waves": [],
        "wave_aborted": False,
        "canary_enabled": False,
        "canary_health_check_command": "systemctl is-system-running",
        "canary_passed": None,
        "drift_detection_enabled": False,
        "drift_abort_on_detection": False,
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


# --- _partition_into_waves tests ---

class TestPartitionIntoWaves:
    def test_100_percent_returns_single_wave(self) -> None:
        targets = _make_targets(4)
        waves = _partition_into_waves(targets, 100)
        assert len(waves) == 1
        assert len(waves[0]) == 4

    def test_25_percent_4_targets_returns_4_waves(self) -> None:
        targets = _make_targets(4)
        waves = _partition_into_waves(targets, 25)
        assert len(waves) == 4
        assert all(len(w) == 1 for w in waves)

    def test_50_percent_4_targets_returns_2_waves(self) -> None:
        targets = _make_targets(4)
        waves = _partition_into_waves(targets, 50)
        assert len(waves) == 2
        assert all(len(w) == 2 for w in waves)

    def test_50_percent_3_targets_rounds_up(self) -> None:
        # 3 * 0.5 = 1.5 → ceil(1.5) = 2
        targets = _make_targets(3)
        waves = _partition_into_waves(targets, 50)
        assert len(waves) == 2
        assert len(waves[0]) == 2
        assert len(waves[1]) == 1

    def test_empty_targets_returns_empty(self) -> None:
        waves = _partition_into_waves([], 50)
        assert waves == []

    def test_single_target_always_one_wave(self) -> None:
        targets = _make_targets(1)
        waves = _partition_into_waves(targets, 25)
        assert len(waves) == 1
        assert len(waves[0]) == 1

    def test_all_targets_covered(self) -> None:
        targets = _make_targets(7)
        waves = _partition_into_waves(targets, 30)
        all_vms = [t for wave in waves for t in wave]
        assert len(all_vms) == 7

    def test_99_percent_acts_like_single_wave_for_small_fleets(self) -> None:
        # 3 targets * 0.99 = 2.97 → ceil = 3 → 1 wave of 3
        targets = _make_targets(3)
        waves = _partition_into_waves(targets, 99)
        assert len(waves) == 1


# --- prepare_waves_node tests ---

class TestPrepareWavesNode:
    @pytest.mark.asyncio
    async def test_100_percent_creates_single_wave(self) -> None:
        state = _base_batch_state(
            healthy_targets=_make_targets(4),
            rolling_update_percentage=100,
        )
        result = await prepare_waves_node(state)
        assert result["total_waves"] == 1
        assert result["current_wave"] == 0
        assert result["wave_aborted"] is False

    @pytest.mark.asyncio
    async def test_25_percent_creates_correct_waves(self) -> None:
        state = _base_batch_state(
            healthy_targets=_make_targets(8),
            rolling_update_percentage=25,
        )
        result = await prepare_waves_node(state)
        assert result["total_waves"] == 4
        assert len(result["waves"]) == 4

    @pytest.mark.asyncio
    async def test_no_targets_creates_no_waves(self) -> None:
        state = _base_batch_state(healthy_targets=[], rolling_update_percentage=100)
        result = await prepare_waves_node(state)
        assert result["waves"] == []
        assert result["total_waves"] == 0

    @pytest.mark.asyncio
    async def test_sets_canary_passed_to_none(self) -> None:
        state = _base_batch_state(healthy_targets=_make_targets(2))
        result = await prepare_waves_node(state)
        assert result["canary_passed"] is None


# --- check_wave_health_node tests ---

class TestCheckWaveHealthNode:
    @pytest.mark.asyncio
    async def test_all_healthy_passes(self, tmp_path: Path) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(2)
        state = _base_batch_state(
            waves=[targets],
            current_wave=0,
            total_waves=1,
            wave_failure_threshold=0.5,
        )
        with patch.object(
            ssh, "execute", AsyncMock(return_value=_make_ssh_result(True))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)

        assert result.get("wave_aborted") is not True
        assert result["current_wave"] == 1

    @pytest.mark.asyncio
    async def test_majority_failure_aborts(self) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(2)
        state = _base_batch_state(
            waves=[targets],
            current_wave=0,
            total_waves=1,
            wave_failure_threshold=0.5,
        )
        with patch.object(
            ssh, "execute", AsyncMock(return_value=_make_ssh_result(False))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)

        assert result["wave_aborted"] is True
        assert result["current_wave"] == 1

    @pytest.mark.asyncio
    async def test_advances_current_wave(self) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_batch_state(
            waves=[targets, targets],
            current_wave=0,
            total_waves=2,
        )
        with patch.object(
            ssh, "execute", AsyncMock(return_value=_make_ssh_result(True))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)
        assert result["current_wave"] == 1

    @pytest.mark.asyncio
    async def test_ssh_connection_error_counts_as_failure(self) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_batch_state(
            waves=[targets],
            current_wave=0,
            total_waves=1,
            wave_failure_threshold=0.0,  # any failure aborts
        )
        with patch.object(
            ssh, "execute", AsyncMock(side_effect=ConnectionError("refused"))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)
        assert result["wave_aborted"] is True

    @pytest.mark.asyncio
    async def test_out_of_bounds_wave_returns_not_aborted(self) -> None:
        ssh = SSHConnectionManager()
        state = _base_batch_state(
            waves=[_make_targets(1)],
            current_wave=5,  # beyond end of waves
            total_waves=1,
        )
        result = await check_wave_health_node(state, ssh_manager=ssh)
        assert result == {"wave_aborted": False}


# --- Routing tests ---

class TestRouteAfterPrepareWaves:
    def test_no_waves_goes_to_report(self) -> None:
        state = _base_batch_state(waves=[])
        assert route_after_prepare_waves(state) == "generate_report"

    def test_has_waves_dispatches(self) -> None:
        state = _base_batch_state(waves=[[_make_target()]])
        assert route_after_prepare_waves(state) == "dispatch_wave"


class TestRouteAfterWaveHealth:
    def test_aborted_goes_to_collect(self) -> None:
        state = _base_batch_state(wave_aborted=True, current_wave=1, total_waves=3)
        assert route_after_wave_health(state) == "collect_results"

    def test_more_waves_continues(self) -> None:
        state = _base_batch_state(wave_aborted=False, current_wave=1, total_waves=3)
        assert route_after_wave_health(state) == "dispatch_wave"

    def test_all_done_collects(self) -> None:
        state = _base_batch_state(wave_aborted=False, current_wave=3, total_waves=3)
        assert route_after_wave_health(state) == "collect_results"

    def test_zero_waves_collects(self) -> None:
        state = _base_batch_state(wave_aborted=False, current_wave=0, total_waves=0)
        assert route_after_wave_health(state) == "collect_results"
