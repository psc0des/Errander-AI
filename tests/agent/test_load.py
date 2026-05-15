"""Load and concurrency tests for wave machinery, canary logic, and file locking.

Exercises the batch orchestrator with realistic fleet sizes to verify:
- Wave partitioning correctness at scale (100+ VMs)
- Wave abort stops the fleet at the correct boundary
- Canary failure prevents fleet-wide execution
- Concurrent lock acquisition is race-safe (50 coroutines, 1 winner)
- No stale locks after normal or aborted batches
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from errander.agent.graph import (
    BatchGraphState,
    _partition_into_waves,
    build_batch_graph,
    prepare_waves_node,
)
from errander.config.settings import Settings
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus
from errander.safety.audit import AuditStore
from errander.safety.locking import FileLocker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(vm_id: str) -> dict[str, object]:
    return {
        "vm_id": vm_id,
        "hostname": f"10.0.{(hash(vm_id) >> 8) % 254 + 1}.{hash(vm_id) % 254 + 1}",
        "ssh_user": "errander-ai",
        "ssh_key_path": "/keys/id_ed25519",
        "os_family": "ubuntu",
    }


def _make_targets(n: int) -> list[dict[str, object]]:
    return [_make_target(f"dev/vm-{i:03d}") for i in range(1, n + 1)]


_OS_RELEASE = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04 LTS"\n'


def _ssh_ok(stdout: str = _OS_RELEASE, exit_code: int = 0) -> SSHResult:
    """Return an SSH success result.

    Default stdout is a valid /etc/os-release payload so validate_targets_node
    can parse it (finding #8 — replaced 'echo ok' with os-release check).
    Pass an explicit stdout override for health-check calls that need "ok".
    """
    now = datetime.now(tz=UTC)
    return SSHResult(
        exit_code=exit_code, stdout=stdout, stderr="",
        command="mocked", duration_seconds=0.01,
        started_at=now, completed_at=now,
    )


def _make_fast_vm_mock(succeed: bool = True) -> MagicMock:
    """build_vm_graph mock that resolves ainvoke() instantly with a valid result."""
    mock_graph = MagicMock()
    mock_compiled = MagicMock()
    mock_graph.compile.return_value = mock_compiled

    async def _ainvoke(state: dict) -> dict:
        vm_id = state.get("vm_id", "unknown")
        status = ActionStatus.SUCCESS.value if succeed else ActionStatus.FAILED.value
        now = datetime.now(tz=UTC).isoformat()
        return {"results": [{
            "action_type": "disk_cleanup",
            "status": status, "vm_id": vm_id,
            "started_at": now, "completed_at": now,
            "detail": "mocked", "error": None,
        }]}

    mock_compiled.ainvoke = _ainvoke
    return mock_graph


def _make_deps(
    tmp_path: Path,
) -> tuple[SandboxExecutor, FileLocker, AuditStore, SSHConnectionManager]:
    executor = SandboxExecutor(SSHConnectionManager(), dry_run=True)
    locker = FileLocker(tmp_path / "locks")
    audit_store = MagicMock(spec=AuditStore)
    audit_store.log_event = AsyncMock()
    ssh = SSHConnectionManager()
    return executor, locker, audit_store, ssh


def _initial(targets: list[dict]) -> dict:
    return {
        "dry_run": True,
        "force": True,          # bypass window check in all load tests
        "force_reason": "load test",
        "targets": targets,
    }


# ---------------------------------------------------------------------------
# Large fleet partitioning — pure unit tests, no async
# ---------------------------------------------------------------------------

class TestLargeFleetPartitioning:

    def test_100_vms_10_percent_creates_10_equal_waves(self) -> None:
        waves = _partition_into_waves(_make_targets(100), 10)
        assert len(waves) == 10
        assert all(len(w) == 10 for w in waves)

    def test_100_vms_25_percent_creates_4_waves(self) -> None:
        waves = _partition_into_waves(_make_targets(100), 25)
        assert len(waves) == 4
        assert all(len(w) == 25 for w in waves)

    def test_all_vms_covered_for_odd_fleet_and_percentage(self) -> None:
        # 47 VMs at 30% — waves of ceil(14.1)=15, 15, 17  → all 47 covered
        targets = _make_targets(47)
        waves = _partition_into_waves(targets, 30)
        assert sum(len(w) for w in waves) == 47

    def test_no_duplicate_vms_across_waves(self) -> None:
        targets = _make_targets(50)
        waves = _partition_into_waves(targets, 20)
        all_ids = [t["vm_id"] for w in waves for t in w]
        assert len(all_ids) == len(set(all_ids))

    def test_1_percent_each_vm_in_own_wave(self) -> None:
        # ceil(10 * 0.01) = 1  →  10 waves of 1
        waves = _partition_into_waves(_make_targets(10), 1)
        assert len(waves) == 10
        assert all(len(w) == 1 for w in waves)

    def test_large_fleet_wave_count_matches_math(self) -> None:
        import math
        n, pct = 200, 15
        wave_size = math.ceil(n * pct / 100)
        expected_waves = math.ceil(n / wave_size)
        waves = _partition_into_waves(_make_targets(n), pct)
        assert len(waves) == expected_waves

    @pytest.mark.asyncio
    async def test_prepare_waves_20_vms_25_pct(self) -> None:
        state: BatchGraphState = {
            "batch_id": "batch-load",
            "dry_run": True,
            "force": False,
            "force_reason": "",
            "targets": [],
            "healthy_targets": _make_targets(20),
            "failed_targets": [],
            "vm_results": [],
            "report": "",
            "error": None,
            "rolling_update_percentage": 25,
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
        result = await prepare_waves_node(state)
        assert result["total_waves"] == 4
        assert sum(len(w) for w in result["waves"]) == 20


# ---------------------------------------------------------------------------
# Fleet batch graph integration tests (mocked vm_compiled for speed)
# ---------------------------------------------------------------------------

class TestFleetBatchGraph:

    @pytest.mark.asyncio
    async def test_10_vm_fleet_all_results_collected(self, tmp_path: Path) -> None:
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(rolling_update_percentage=100, canary_enabled=False)

        with (
            patch.object(ssh, "execute", AsyncMock(return_value=_ssh_ok())),
            patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(10)))

        assert len(final.get("vm_results", [])) == 10
        assert all(r["status"] == ActionStatus.SUCCESS.value for r in final["vm_results"])

    @pytest.mark.asyncio
    async def test_25_pct_rolling_4_waves_all_processed(self, tmp_path: Path) -> None:
        """8 VMs at 25% → 4 waves; all health checks pass → all 8 results collected."""
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(rolling_update_percentage=25, canary_enabled=False)

        with (
            patch.object(ssh, "execute", AsyncMock(return_value=_ssh_ok())),
            patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(8)))

        assert len(final.get("vm_results", [])) == 8

    @pytest.mark.asyncio
    async def test_wave_abort_stops_fleet_at_boundary(self, tmp_path: Path) -> None:
        """12 VMs at 25% → 4 waves of 3. Wave 1 health check fails → only 6 VMs processed."""
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        # wave_failure_threshold=0.5: >50% failure triggers abort
        settings = Settings(
            rolling_update_percentage=25,
            wave_failure_threshold=0.5,
            canary_enabled=False,
        )

        call_count = 0

        async def _ssh(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            # 12 validate_targets + 12×5 plan_vm + 12×2 enrich_plan (disk_cleanup preview) + 3 wave-0 health = 99 succeed
            # wave-1 health check calls (100-102) fail
            return _ssh_ok() if call_count <= 99 else _ssh_ok("", 1)

        with (
            patch.object(ssh, "execute", side_effect=_ssh),
            patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(12)))

        # Waves 0 and 1 dispatched (3 VMs each) → 6 results, then abort
        assert len(final.get("vm_results", [])) == 6
        assert final.get("wave_aborted") is True

    @pytest.mark.asyncio
    async def test_canary_abort_skips_entire_fleet(self, tmp_path: Path) -> None:
        """10 VMs with canary. Canary health check fails → fleet (9 VMs) skipped."""
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(
            rolling_update_percentage=100,
            canary_enabled=True,
            wave_failure_threshold=0.0,  # any failure aborts
        )

        call_count = 0

        async def _ssh(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            # 10 validate (os-release) + 10×5 plan_vm (detect_os) = 60 succeed
            # canary health check (call 61) fails → canary wave aborts, fleet skipped
            return _ssh_ok() if call_count <= 60 else _ssh_ok("", 1)

        with (
            patch.object(ssh, "execute", side_effect=_ssh),
            patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(10)))

        # Only the canary VM was dispatched
        assert len(final.get("vm_results", [])) == 1
        assert final.get("wave_aborted") is True
        assert final.get("canary_passed") is False

    @pytest.mark.asyncio
    async def test_report_generated_for_large_fleet(self, tmp_path: Path) -> None:
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(rolling_update_percentage=100, canary_enabled=False)

        with (
            patch.object(ssh, "execute", AsyncMock(return_value=_ssh_ok())),
            patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(15)))

        assert final.get("report", "") != ""
        assert final.get("batch_id", "").startswith("batch-")

    @pytest.mark.asyncio
    async def test_all_unhealthy_fleet_still_produces_report(self, tmp_path: Path) -> None:
        """If all VMs fail validate_targets, batch still generates a report."""
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(rolling_update_percentage=100, canary_enabled=False)

        with patch.object(ssh, "execute", AsyncMock(side_effect=ConnectionError("refused"))):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(10)))

        assert len(final.get("healthy_targets", [])) == 0
        assert final.get("report", "") != ""

    @pytest.mark.asyncio
    async def test_vm_crash_in_fleet_does_not_abort_other_vms(self, tmp_path: Path) -> None:
        """One VM graph crashes with RuntimeError — other VMs still produce results."""
        executor, locker, audit_store, ssh = _make_deps(tmp_path)
        settings = Settings(rolling_update_percentage=100, canary_enabled=False)

        # First vm_compiled call raises, rest succeed
        call_count = 0
        mock_compiled = MagicMock()
        mock_graph = MagicMock()
        mock_graph.compile.return_value = mock_compiled

        async def _ainvoke(state: dict) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("vm graph exploded")
            vm_id = state.get("vm_id", "unknown")
            now = datetime.now(tz=UTC).isoformat()
            return {"results": [{
                "action_type": "disk_cleanup",
                "status": ActionStatus.SUCCESS.value, "vm_id": vm_id,
                "started_at": now, "completed_at": now,
                "detail": "ok", "error": None,
            }]}

        mock_compiled.ainvoke = _ainvoke

        with (
            patch.object(ssh, "execute", AsyncMock(return_value=_ssh_ok())),
            patch("errander.agent.graph.build_vm_graph", return_value=mock_graph),
        ):
            final = await build_batch_graph(
                executor, locker, audit_store, ssh, settings=settings,
            ).compile().ainvoke(_initial(_make_targets(5)))

        results = final.get("vm_results", [])
        assert len(results) == 5
        # First result is FAILED (caught crash), rest are COMPLETED
        failed = [r for r in results if r["status"] == ActionStatus.FAILED.value]
        completed = [r for r in results if r["status"] == ActionStatus.SUCCESS.value]
        assert len(failed) == 1
        assert len(completed) == 4


# ---------------------------------------------------------------------------
# Concurrent lock operations
# ---------------------------------------------------------------------------

class TestConcurrentLockOperations:

    @pytest.mark.asyncio
    async def test_50_concurrent_acquires_exactly_one_wins(self, tmp_path: Path) -> None:
        """50 coroutines race for the same VM lock — exactly 1 must succeed."""
        locker = FileLocker(tmp_path / "locks")
        results = await asyncio.gather(
            *[locker.acquire("vm-shared", f"batch-{i:03d}") for i in range(50)],
        )
        assert sum(results) == 1

    @pytest.mark.asyncio
    async def test_20_vm_concurrent_lifecycle_leaves_no_locks(self, tmp_path: Path) -> None:
        """20 VMs each acquire → work → release concurrently; no locks remain."""
        locker = FileLocker(tmp_path / "locks")

        async def _lifecycle(vm_id: str) -> None:
            await locker.acquire(vm_id, "batch-1")
            await asyncio.sleep(0.005)
            await locker.release(vm_id, "batch-1")

        await asyncio.gather(*[_lifecycle(f"vm-{i:02d}") for i in range(20)])
        assert await locker.list_locks() == []

    @pytest.mark.asyncio
    async def test_20_different_vms_concurrent_acquire_all_succeed(self, tmp_path: Path) -> None:
        """20 coroutines each lock a DIFFERENT VM — all 20 should succeed."""
        locker = FileLocker(tmp_path / "locks")
        results = await asyncio.gather(
            *[locker.acquire(f"vm-{i:02d}", "batch-1") for i in range(20)],
        )
        assert all(results)
        assert len(await locker.list_locks()) == 20

    @pytest.mark.asyncio
    async def test_stale_lock_unblocks_next_batch(self, tmp_path: Path) -> None:
        """Expired lock from a crashed batch does not block the next batch."""
        locker = FileLocker(tmp_path / "locks")
        lock_path = locker._lock_path("vm-stale")

        past = datetime.now(tz=UTC) - timedelta(hours=10)
        lock_path.write_text(json.dumps({
            "vm_id": "vm-stale", "batch_id": "crashed-batch",
            "acquired_at": past.isoformat(), "ttl_seconds": 3600,
        }))

        assert await locker.acquire("vm-stale", "recovery-batch")
        info = await locker.get_lock_info("vm-stale")
        assert info is not None and info.batch_id == "recovery-batch"

    @pytest.mark.asyncio
    async def test_force_release_unblocks_stuck_vm(self, tmp_path: Path) -> None:
        """force_release simulates operator recovery after a crashed VM graph."""
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-stuck", "crashed-batch")

        assert await locker.force_release("vm-stuck")
        assert not await locker.is_locked("vm-stuck")
        assert await locker.acquire("vm-stuck", "next-batch")

    @pytest.mark.asyncio
    async def test_wave_serial_batches_never_double_lock(self, tmp_path: Path) -> None:
        """Simulates back-to-back waves for the same VM — no double-lock."""
        locker = FileLocker(tmp_path / "locks")
        vm_id = "prod/db-01"

        for wave in range(5):
            batch_id = f"batch-wave-{wave}"
            acquired = await locker.acquire(vm_id, batch_id)
            assert acquired, f"Wave {wave} failed to acquire lock"
            await locker.release(vm_id, batch_id)

        assert not await locker.is_locked(vm_id)
