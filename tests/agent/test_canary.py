"""Tests for canary logic in rolling updates."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    check_wave_health_node,
    prepare_waves_node,
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
    now = datetime.now(tz=UTC)
    return SSHResult(
        exit_code=0 if success else 1,
        stdout="ok" if success else "failed",
        stderr="",
        command="echo ok",
        duration_seconds=0.01,
        started_at=now,
        completed_at=now,
    )


def _base_state(
    targets: list[dict[str, object]] | None = None,
    canary_enabled: bool = True,
    **overrides: object,
) -> BatchGraphState:
    defaults: BatchGraphState = {
        "batch_id": "batch-canary-001",
        "dry_run": True,
        "force": False,
        "force_reason": "",
        "targets": [],
        "healthy_targets": targets or _make_targets(4),
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
        "canary_enabled": canary_enabled,
        "canary_health_check_command": "systemctl is-system-running",
        "canary_passed": None,
        "drift_detection_enabled": False,
        "drift_abort_on_detection": False,
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


# --- Canary partitioning ---

class TestCanaryPartitioning:
    @pytest.mark.asyncio
    async def test_canary_splits_first_target_alone(self) -> None:
        state = _base_state(targets=_make_targets(4), canary_enabled=True)
        result = await prepare_waves_node(state)
        waves = result["waves"]
        assert len(waves[0]) == 1  # canary wave has exactly 1 VM
        assert waves[0][0]["vm_id"] == "dev/web-01"

    @pytest.mark.asyncio
    async def test_canary_disabled_no_split(self) -> None:
        state = _base_state(
            targets=_make_targets(4),
            canary_enabled=False,
            rolling_update_percentage=100,
        )
        result = await prepare_waves_node(state)
        waves = result["waves"]
        assert len(waves) == 1  # single wave, no canary
        assert len(waves[0]) == 4

    @pytest.mark.asyncio
    async def test_canary_single_target_no_split_needed(self) -> None:
        # Only 1 target: canary mode can't split, just 1 wave
        state = _base_state(targets=_make_targets(1), canary_enabled=True)
        result = await prepare_waves_node(state)
        waves = result["waves"]
        assert len(waves) == 1
        assert len(waves[0]) == 1

    @pytest.mark.asyncio
    async def test_canary_remaining_targets_distributed_correctly(self) -> None:
        # 5 targets with canary: wave 0 = [vm-01], waves 1..n = rest
        state = _base_state(
            targets=_make_targets(5),
            canary_enabled=True,
            rolling_update_percentage=100,
        )
        result = await prepare_waves_node(state)
        waves = result["waves"]
        all_vms = [t for wave in waves for t in wave]
        assert len(all_vms) == 5
        assert waves[0][0]["vm_id"] == "dev/web-01"


# --- Canary health checks ---

class TestCanaryHealthCheck:
    @pytest.mark.asyncio
    async def test_canary_wave_uses_canary_command(self) -> None:
        """Wave 0 with canary enabled uses canary_health_check_command."""
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_state(
            canary_enabled=True,
            waves=[targets],
            current_wave=0,
            total_waves=2,
        )

        captured_cmds: list[str] = []

        async def mock_execute(
            vm_id: str, hostname: str, user: str, key: str, cmd: str
        ) -> SSHResult:
            captured_cmds.append(cmd)
            return _make_ssh_result(True)

        with patch.object(ssh, "execute", side_effect=mock_execute):
            await check_wave_health_node(state, ssh_manager=ssh)

        assert captured_cmds[0] == "systemctl is-system-running"

    @pytest.mark.asyncio
    async def test_canary_failure_sets_wave_aborted(self) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_state(
            canary_enabled=True,
            waves=[targets],
            current_wave=0,
            total_waves=2,
        )
        with patch.object(
            ssh, "execute", AsyncMock(return_value=_make_ssh_result(False))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)

        assert result["wave_aborted"] is True
        assert result["canary_passed"] is False

    @pytest.mark.asyncio
    async def test_canary_success_sets_canary_passed_true(self) -> None:
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_state(
            canary_enabled=True,
            waves=[targets],
            current_wave=0,
            total_waves=2,
        )
        with patch.object(
            ssh, "execute", AsyncMock(return_value=_make_ssh_result(True))
        ):
            result = await check_wave_health_node(state, ssh_manager=ssh)

        assert result.get("wave_aborted") is not True
        assert result["canary_passed"] is True

    @pytest.mark.asyncio
    async def test_non_canary_wave_uses_regular_command(self) -> None:
        """Wave 1 (non-canary) uses health_check_command, not canary command."""
        ssh = SSHConnectionManager()
        targets = _make_targets(1)
        state = _base_state(
            canary_enabled=True,
            waves=[targets, targets],
            current_wave=1,  # NOT canary wave
            total_waves=2,
        )

        captured_cmds: list[str] = []

        async def mock_execute(
            vm_id: str, hostname: str, user: str, key: str, cmd: str
        ) -> SSHResult:
            captured_cmds.append(cmd)
            return _make_ssh_result(True)

        with patch.object(ssh, "execute", side_effect=mock_execute):
            await check_wave_health_node(
                state, ssh_manager=ssh, health_check_command="echo ok"
            )

        assert captured_cmds[0] == "echo ok"
