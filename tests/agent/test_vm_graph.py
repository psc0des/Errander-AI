"""Tests for the per-VM maintenance graph."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.vm_graph import (
    VMGraphState,
    acquire_lock_node,
    audit_results_node,
    build_vm_graph,
    discover_node,
    dispatch_action_node,
    drift_check_node,
    plan_actions_node,
    release_lock_node,
    route_after_discover,
    route_after_drift_check,
    route_after_lock,
    route_check_more,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus, ActionType
from errander.models.events import EventType
from errander.models.vm import OSFamily, VMInfo
from errander.safety.audit import AuditStore
from errander.safety.locking import FileLocker


# --- Helpers ---

def _base_state(**overrides: object) -> VMGraphState:
    defaults: VMGraphState = {
        "vm_id": "dev/web-01",
        "batch_id": "batch-test-001",
        "dry_run": True,
        "hostname": "10.0.1.10",
        "ssh_user": "errander-ai",
        "ssh_key_path": "/home/user/.ssh/id_ed25519",
        "os_family": "ubuntu",
        "locked": False,
        "results": [],
        "current_action_index": 0,
        "planned_actions": [],
        "error": None,
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_vm_info(
    os_family: OSFamily = OSFamily.UBUNTU,
    docker_available: bool = True,
    pending_packages: int = 3,
) -> VMInfo:
    return VMInfo(
        os_family=os_family,
        os_version="Ubuntu 22.04.3 LTS",
        disk_usage={"/": 55.0},
        docker_available=docker_available,
        pending_packages=pending_packages,
        uptime_seconds=43200.0,
    )


def _make_locker(tmp_path: Path) -> FileLocker:
    return FileLocker(lock_dir=tmp_path / "locks")


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


def _make_ssh_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    return SSHResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        command="mocked",
        duration_seconds=0.01,
        started_at=now,
        completed_at=now,
    )


# --- Lock tests ---

class TestAcquireLockNode:
    @pytest.mark.asyncio
    async def test_acquires_lock_successfully(self, tmp_path: Path) -> None:
        locker = _make_locker(tmp_path)
        state = _base_state()
        result = await acquire_lock_node(state, locker=locker)
        assert result["locked"] is True
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_fails_when_already_locked(self, tmp_path: Path) -> None:
        locker = _make_locker(tmp_path)
        # Pre-acquire with different batch
        await locker.acquire("dev/web-01", "other-batch")
        state = _base_state()
        result = await acquire_lock_node(state, locker=locker)
        assert result["locked"] is False
        assert "error" in result
        assert "already locked" in result["error"]


class TestReleaseLockNode:
    @pytest.mark.asyncio
    async def test_releases_acquired_lock(self, tmp_path: Path) -> None:
        locker = _make_locker(tmp_path)
        await locker.acquire("dev/web-01", "batch-test-001")
        state = _base_state(locked=True)
        result = await release_lock_node(state, locker=locker)
        assert result.get("locked") is False
        assert not await locker.is_locked("dev/web-01")

    @pytest.mark.asyncio
    async def test_noop_when_not_locked(self, tmp_path: Path) -> None:
        locker = _make_locker(tmp_path)
        state = _base_state(locked=False)
        result = await release_lock_node(state, locker=locker)
        assert result == {}


# --- Routing tests ---

class TestRouting:
    def test_route_after_lock_locked(self) -> None:
        assert route_after_lock(_base_state(locked=True)) == "discover"

    def test_route_after_lock_not_locked(self) -> None:
        assert route_after_lock(_base_state(locked=False)) == "audit_results"

    def test_route_after_discover_success(self) -> None:
        state = _base_state(vm_info={"os_family": "ubuntu"})
        assert route_after_discover(state) == "drift_check"

    def test_route_after_discover_error(self) -> None:
        state = _base_state(error="SSH failed")
        assert route_after_discover(state) == "audit_results"

    def test_route_check_more_has_actions(self) -> None:
        state = _base_state(
            planned_actions=[{"action_type": "disk_cleanup"}],
            current_action_index=0,
        )
        assert route_check_more(state) == "dispatch_action"

    def test_route_check_more_exhausted(self) -> None:
        state = _base_state(
            planned_actions=[{"action_type": "disk_cleanup"}],
            current_action_index=1,
        )
        assert route_check_more(state) == "audit_results"

    def test_route_check_more_empty(self) -> None:
        assert route_check_more(_base_state()) == "audit_results"


# --- Discovery tests ---

class TestDiscoverNode:
    @pytest.mark.asyncio
    async def test_populates_vm_info(self) -> None:
        ssh_manager = SSHConnectionManager()
        vm_info = _make_vm_info()

        with patch(
            "errander.agent.vm_graph.detect_os",
            AsyncMock(return_value=vm_info),
        ):
            result = await discover_node(_base_state(), ssh_manager=ssh_manager)

        assert result["vm_info"]["os_family"] == "ubuntu"
        assert result["vm_info"]["docker_available"] is True
        assert result["os_family"] == "ubuntu"

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self) -> None:
        ssh_manager = SSHConnectionManager()

        with patch(
            "errander.agent.vm_graph.detect_os",
            AsyncMock(side_effect=ValueError("unsupported OS")),
        ):
            result = await discover_node(_base_state(), ssh_manager=ssh_manager)

        assert "error" in result
        assert "Discovery failed" in result["error"]


# --- Planning tests ---

class TestPlanActionsNode:
    @pytest.mark.asyncio
    async def test_plans_actions_with_docker(self) -> None:
        state = _base_state(
            vm_info={
                "os_family": "ubuntu",
                "os_version": "Ubuntu 22.04",
                "disk_usage": {"/": 50.0},
                "docker_available": True,
                "pending_packages": 5,
                "uptime_seconds": 86400.0,
            }
        )
        result = await plan_actions_node(state)
        action_types = [a["action_type"] for a in result["planned_actions"]]
        assert ActionType.DISK_CLEANUP.value in action_types
        assert ActionType.DOCKER_PRUNE.value in action_types
        assert ActionType.PATCHING.value in action_types
        assert result["current_action_index"] == 0

    @pytest.mark.asyncio
    async def test_skips_docker_when_unavailable(self) -> None:
        state = _base_state(
            vm_info={
                "os_family": "ubuntu",
                "os_version": "Ubuntu 22.04",
                "disk_usage": {"/": 50.0},
                "docker_available": False,
                "pending_packages": 0,
                "uptime_seconds": 86400.0,
            }
        )
        result = await plan_actions_node(state)
        action_types = [a["action_type"] for a in result["planned_actions"]]
        assert ActionType.DOCKER_PRUNE.value not in action_types
        assert ActionType.PATCHING.value not in action_types

    @pytest.mark.asyncio
    async def test_priority_order(self) -> None:
        state = _base_state(
            vm_info={
                "os_family": "ubuntu",
                "os_version": "Ubuntu 22.04",
                "disk_usage": {"/": 50.0},
                "docker_available": True,
                "pending_packages": 3,
                "uptime_seconds": 86400.0,
            }
        )
        result = await plan_actions_node(state)
        action_types = [a["action_type"] for a in result["planned_actions"]]
        # disk_cleanup must come before patching
        assert action_types.index(ActionType.DISK_CLEANUP.value) < action_types.index(
            ActionType.PATCHING.value
        )


# --- Dispatch tests ---

class TestDispatchActionNode:
    @pytest.mark.asyncio
    async def test_dispatches_disk_cleanup(self) -> None:
        executor = _make_executor(dry_run=True)
        ssh = executor._ssh

        os_release = "ID=ubuntu\nVERSION_ID=22.04\nPRETTY_NAME=Ubuntu 22.04\n"
        df_output = "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 20G 10G 10G 50% /\n"

        with patch.object(
            ssh, "execute",
            AsyncMock(side_effect=[
                _make_ssh_result(df_output),       # assess: df -h
                _make_ssh_result("1.0M\ttotal"),    # assess: /tmp
                _make_ssh_result("500K"),            # assess: apt-cache
                _make_ssh_result("200M"),            # assess: journal
                _make_ssh_result("0 packages"),     # assess: orphaned-deps
                _make_ssh_result("[DRY-RUN]"),       # execute: /tmp
                _make_ssh_result("[DRY-RUN]"),       # execute: apt-cache
                _make_ssh_result("[DRY-RUN]"),       # execute: journal
                _make_ssh_result("[DRY-RUN]"),       # execute: orphaned-deps
            ]),
        ):
            state = _base_state(
                planned_actions=[{"action_type": "disk_cleanup", "risk_tier": "low", "params": {}}],
                current_action_index=0,
            )
            from errander.agent.subgraphs.disk_cleanup import build_disk_cleanup_subgraph
            disk_compiled = build_disk_cleanup_subgraph(executor).compile()
            result = await dispatch_action_node(
                state, executor=executor, disk_cleanup_compiled=disk_compiled,
            )

        assert len(result["results"]) == 1
        assert result["results"][0]["action_type"] == "disk_cleanup"
        assert result["current_action_index"] == 1

    @pytest.mark.asyncio
    async def test_skips_unknown_action_type(self) -> None:
        executor = _make_executor()
        state = _base_state(
            planned_actions=[
                {"action_type": "unknown_action", "risk_tier": "medium", "params": {}}
            ],
            current_action_index=0,
        )
        result = await dispatch_action_node(state, executor=executor)
        assert result["results"][0]["status"] == ActionStatus.SKIPPED.value
        assert result["current_action_index"] == 1

    @pytest.mark.asyncio
    async def test_noop_when_index_exhausted(self) -> None:
        executor = _make_executor()
        state = _base_state(
            planned_actions=[{"action_type": "disk_cleanup", "risk_tier": "low", "params": {}}],
            current_action_index=1,
        )
        result = await dispatch_action_node(state, executor=executor)
        assert result == {}


# --- Audit tests ---

class TestAuditResultsNode:
    @pytest.mark.asyncio
    async def test_logs_successful_results(self) -> None:
        async with AuditStore(":memory:") as store:
            state = _base_state(
                results=[{
                    "action_type": "disk_cleanup",
                    "status": ActionStatus.DRY_RUN_OK.value,
                    "detail": "all good",
                    "error": None,
                }],
            )
            await audit_results_node(state, audit_store=store)
            events = await store.get_events(batch_id="batch-test-001")
            assert len(events) == 1
            assert events[0].event_type == EventType.ACTION_COMPLETED

    @pytest.mark.asyncio
    async def test_logs_failed_results(self) -> None:
        async with AuditStore(":memory:") as store:
            state = _base_state(
                results=[{
                    "action_type": "disk_cleanup",
                    "status": ActionStatus.FAILED.value,
                    "detail": "",
                    "error": "permission denied",
                }],
            )
            await audit_results_node(state, audit_store=store)
            events = await store.get_events(batch_id="batch-test-001")
            assert events[0].event_type == EventType.ACTION_FAILED

    @pytest.mark.asyncio
    async def test_logs_vm_level_error(self) -> None:
        async with AuditStore(":memory:") as store:
            state = _base_state(error="Discovery failed: SSH refused")
            await audit_results_node(state, audit_store=store)
            events = await store.get_events(vm_id="dev/web-01")
            assert len(events) == 1
            assert "Discovery failed" in events[0].detail

    @pytest.mark.asyncio
    async def test_noop_on_empty_results(self) -> None:
        async with AuditStore(":memory:") as store:
            state = _base_state()
            await audit_results_node(state, audit_store=store)
            events = await store.get_events(batch_id="batch-test-001")
            assert events == []


# --- Graph construction tests ---

class TestBuildVMGraph:
    def test_graph_builds(self, tmp_path: Path) -> None:
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        ssh = SSHConnectionManager()

        async def noop_audit(*a: object, **kw: object) -> None:
            pass

        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        graph = build_vm_graph(executor, locker, audit_store, ssh)
        assert graph is not None

    def test_graph_compiles(self, tmp_path: Path) -> None:
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        ssh = SSHConnectionManager()
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        compiled = build_vm_graph(executor, locker, audit_store, ssh).compile()
        assert compiled is not None

    @pytest.mark.asyncio
    async def test_lock_failure_goes_to_audit_and_unlock(self, tmp_path: Path) -> None:
        """When lock fails, graph must still reach audit and release."""
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        # Pre-lock to force failure
        await locker.acquire("dev/web-01", "other-batch")

        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()

        compiled = build_vm_graph(executor, locker, audit_store, ssh).compile()
        final = await compiled.ainvoke(_base_state())

        # Should have error, not locked, audit was called
        assert final.get("locked") is False
        assert final.get("error") is not None
        audit_store.log_event.assert_called()

    @pytest.mark.asyncio
    async def test_discovery_failure_skips_to_audit(self, tmp_path: Path) -> None:
        """Discovery failure must audit the error and still release the lock."""
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()

        with patch(
            "errander.agent.vm_graph.detect_os",
            AsyncMock(side_effect=ConnectionError("refused")),
        ):
            compiled = build_vm_graph(executor, locker, audit_store, ssh).compile()
            final = await compiled.ainvoke(_base_state())

        assert final.get("locked") is False
        assert "Discovery failed" in final.get("error", "")

    @pytest.mark.asyncio
    async def test_full_dry_run_disk_cleanup(self, tmp_path: Path) -> None:
        """Full happy-path: lock → discover → plan → dispatch(disk_cleanup) → audit → unlock."""
        executor = _make_executor(dry_run=True)
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)

        df_output = "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 20G 10G 10G 50% /\n"
        ssh_responses = [
            # assess: df, /tmp, apt-cache, journal, orphaned-deps
            _make_ssh_result(df_output),
            _make_ssh_result("1.0M\ttotal"),
            _make_ssh_result("500K"),
            _make_ssh_result("200M"),
            _make_ssh_result("0 packages"),
            # execute: /tmp, apt-cache, journal, orphaned-deps
            _make_ssh_result("done"),
            _make_ssh_result("done"),
            _make_ssh_result("done"),
            _make_ssh_result("done"),
        ]

        with patch(
            "errander.agent.vm_graph.detect_os",
            AsyncMock(return_value=vm_info),
        ):
            with patch.object(
                executor._ssh, "execute",
                AsyncMock(side_effect=ssh_responses),
            ):
                compiled = build_vm_graph(executor, locker, audit_store, ssh).compile()
                final = await compiled.ainvoke(_base_state())

        # Lock released
        assert final.get("locked") is False
        assert not await locker.is_locked("dev/web-01")

        # Disk cleanup ran
        results = final.get("results", [])
        assert any(r["action_type"] == "disk_cleanup" for r in results)
        disk_result = next(r for r in results if r["action_type"] == "disk_cleanup")
        assert disk_result["status"] == ActionStatus.DRY_RUN_OK.value

        # Audit was called
        audit_store.log_event.assert_called()


# --- Drift check node tests ---

class TestDriftCheckNode:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self) -> None:
        state = _base_state(drift_detection_enabled=False)
        async with AuditStore(":memory:") as store:
            result = await drift_check_node(state, audit_store=store)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_baseline_first_run(self) -> None:
        state = _base_state(
            drift_detection_enabled=True,
            vm_info={
                "os_version": "Ubuntu 22.04",
                "disk_usage": {"/": 45.0},
                "docker_available": True,
                "uptime_seconds": 86400.0,
                "pending_packages": 3,
            },
        )
        async with AuditStore(":memory:") as store:
            result = await drift_check_node(state, audit_store=store)

        assert result["drift_result"]["has_drift"] is False
        assert result["drift_result"]["baseline_found"] is False
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_no_drift_passes_through(self) -> None:
        from errander.safety.drift import save_baseline

        vm_info: dict[str, object] = {
            "os_version": "Ubuntu 22.04",
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        async with AuditStore(":memory:") as store:
            await save_baseline(store, "dev/web-01", vm_info)
            state = _base_state(
                drift_detection_enabled=True,
                vm_info=vm_info,
            )
            result = await drift_check_node(state, audit_store=store)

        assert result["drift_result"]["has_drift"] is False
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_drift_detected_no_abort_continues(self) -> None:
        from errander.safety.drift import save_baseline

        baseline_info: dict[str, object] = {
            "os_version": "Ubuntu 22.04",
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        current_info: dict[str, object] = {
            "os_version": "Ubuntu 24.04",  # drift
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        async with AuditStore(":memory:") as store:
            await save_baseline(store, "dev/web-01", baseline_info)
            state = _base_state(
                drift_detection_enabled=True,
                drift_abort_on_detection=False,
                vm_info=current_info,
            )
            result = await drift_check_node(state, audit_store=store)

        assert result["drift_result"]["has_drift"] is True
        assert "error" not in result  # no abort

    @pytest.mark.asyncio
    async def test_drift_detected_abort_sets_error(self) -> None:
        from errander.safety.drift import save_baseline

        baseline_info: dict[str, object] = {
            "os_version": "Ubuntu 22.04",
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        current_info: dict[str, object] = {
            "os_version": "Ubuntu 24.04",
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        async with AuditStore(":memory:") as store:
            await save_baseline(store, "dev/web-01", baseline_info)
            state = _base_state(
                drift_detection_enabled=True,
                drift_abort_on_detection=True,
                vm_info=current_info,
            )
            result = await drift_check_node(state, audit_store=store)

        assert result["drift_result"]["has_drift"] is True
        assert "error" in result
        assert "Drift detected, aborting" in str(result["error"])

    @pytest.mark.asyncio
    async def test_disabled_no_vm_info_returns_empty(self) -> None:
        state = _base_state(drift_detection_enabled=True)
        # No vm_info in state
        async with AuditStore(":memory:") as store:
            result = await drift_check_node(state, audit_store=store)
        assert result == {}


class TestRoutingDriftCheck:
    def test_route_after_drift_check_no_error_goes_to_plan(self) -> None:
        state = _base_state()
        assert route_after_drift_check(state) == "plan_actions"

    def test_route_after_drift_check_with_error_goes_to_audit(self) -> None:
        state = _base_state(error="Drift detected, aborting")
        assert route_after_drift_check(state) == "audit_results"

    def test_route_after_discover_goes_to_drift_check(self) -> None:
        state = _base_state()
        assert route_after_discover(state) == "drift_check"

    def test_route_after_discover_error_skips_drift_check(self) -> None:
        state = _base_state(error="Discovery failed")
        assert route_after_discover(state) == "audit_results"


class TestAuditSavesBaseline:
    @pytest.mark.asyncio
    async def test_baseline_saved_after_successful_action(self) -> None:
        from errander.models.actions import ActionStatus
        from errander.models.events import EventType

        vm_info: dict[str, object] = {
            "os_version": "Ubuntu 22.04",
            "disk_usage": {"/": 45.0},
            "docker_available": True,
            "uptime_seconds": 86400.0,
            "pending_packages": 3,
        }
        state = _base_state(
            drift_detection_enabled=True,
            vm_info=vm_info,
            results=[
                {
                    "action_type": "disk_cleanup",
                    "status": ActionStatus.DRY_RUN_OK.value,
                    "vm_id": "dev/web-01",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:01:00+00:00",
                    "detail": "",
                    "error": None,
                }
            ],
            error=None,
        )
        async with AuditStore(":memory:") as store:
            await audit_results_node(state, audit_store=store)
            events = await store.get_events(
                vm_id="dev/web-01",
                event_type=EventType.DRIFT_BASELINE_SAVED,
            )

        assert len(events) == 1


# --- Exception safety tests ---

class TestSubgraphExceptionSafety:
    """Step 1: sub-graph exceptions must not escape dispatch; lock must be released."""

    @pytest.mark.asyncio
    async def test_subgraph_exception_returns_failed_result(self) -> None:
        """ConnectionError inside a sub-graph returns FAILED result, not an exception."""
        executor = _make_executor(dry_run=True)
        state = _base_state(
            planned_actions=[{"action_type": "disk_cleanup", "risk_tier": "low", "params": {}}],
            current_action_index=0,
        )
        bad_compiled = MagicMock()
        bad_compiled.ainvoke = AsyncMock(side_effect=ConnectionError("SSH dropped"))

        result = await dispatch_action_node(
            state, executor=executor, disk_cleanup_compiled=bad_compiled,
        )

        assert len(result["results"]) == 1
        r = result["results"][0]
        assert r["status"] == ActionStatus.FAILED.value
        assert r["action_type"] == "disk_cleanup"
        assert "SSH dropped" in str(r.get("error", ""))

    @pytest.mark.asyncio
    async def test_dispatch_action_handles_unexpected_error(self) -> None:
        """RuntimeError inside a sub-graph returns FAILED result (bare Exception guard)."""
        executor = _make_executor(dry_run=True)
        state = _base_state(
            planned_actions=[{"action_type": "log_rotation", "risk_tier": "low", "params": {}}],
            current_action_index=0,
        )
        bad_compiled = MagicMock()
        bad_compiled.ainvoke = AsyncMock(side_effect=RuntimeError("internal failure"))

        result = await dispatch_action_node(
            state, executor=executor, log_rotation_compiled=bad_compiled,
        )

        assert result["results"][0]["status"] == ActionStatus.FAILED.value
        assert result["current_action_index"] == 1

    @pytest.mark.asyncio
    async def test_subgraph_exception_releases_lock(self, tmp_path: Path) -> None:
        """Lock must be released even when the dispatched sub-graph raises."""
        executor = _make_executor(dry_run=True)
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)

        bad_compiled = MagicMock()
        bad_compiled.ainvoke = AsyncMock(side_effect=ConnectionError("SSH reset"))

        with patch(
            "errander.agent.vm_graph.detect_os",
            AsyncMock(return_value=vm_info),
        ):
            with patch(
                "errander.agent.vm_graph.build_disk_cleanup_subgraph",
                return_value=MagicMock(compile=MagicMock(return_value=bad_compiled)),
            ):
                compiled = build_vm_graph(executor, locker, audit_store, ssh).compile()
                final = await compiled.ainvoke(_base_state())

        assert final.get("locked") is False
        assert not await locker.is_locked("dev/web-01")

    @pytest.mark.asyncio
    async def test_baseline_save_failure_does_not_crash_audit(self) -> None:
        """save_baseline raising must not prevent audit_results_node from returning {}."""
        async with AuditStore(":memory:") as store:
            state = _base_state(
                drift_detection_enabled=True,
                vm_info={
                    "os_version": "Ubuntu 22.04",
                    "disk_usage": {"/": 45.0},
                    "docker_available": True,
                    "uptime_seconds": 86400.0,
                    "pending_packages": 3,
                },
                results=[{
                    "action_type": "disk_cleanup",
                    "status": ActionStatus.DRY_RUN_OK.value,
                    "detail": "",
                    "error": None,
                }],
                error=None,
            )
            with patch(
                "errander.agent.vm_graph.save_baseline",
                AsyncMock(side_effect=OSError("disk full")),
            ):
                result = await audit_results_node(state, audit_store=store)

        assert result == {}
