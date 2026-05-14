"""Tests for the batch orchestrator graph."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    build_batch_graph,
    collect_results_node,
    generate_report_node,
    init_batch_node,
    make_fan_out_router,
    route_after_validate,
    route_after_window,
    validate_targets_node,
    validate_window_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus
from errander.safety.approval import ApprovalManager
from errander.safety.audit import AuditStore
from errander.safety.locking import FileLocker
from errander.scheduling.windows import MaintenanceWindow


# --- Helpers ---

def _make_target(
    vm_id: str = "dev/web-01",
    hostname: str = "10.0.1.10",
    ssh_user: str = "errander-ai",
    key_path: str = "/keys/id_ed25519",
    os_family: str = "ubuntu",
) -> dict[str, object]:
    return {
        "vm_id": vm_id,
        "hostname": hostname,
        "ssh_user": ssh_user,
        "ssh_key_path": key_path,
        "os_family": os_family,
    }


def _base_state(**overrides: object) -> BatchGraphState:
    defaults: BatchGraphState = {
        "dry_run": True,
        "force": False,
        "force_reason": "",
        "targets": [_make_target()],
        "healthy_targets": [],
        "failed_targets": [],
        "vm_results": [],
        "report": "",
        "error": None,
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


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


# --- init_batch ---

class TestInitBatchNode:
    @pytest.mark.asyncio
    async def test_generates_batch_id(self) -> None:
        result = await init_batch_node(_base_state())
        assert "batch_id" in result
        assert result["batch_id"].startswith("batch-")

    @pytest.mark.asyncio
    async def test_batch_ids_are_unique(self) -> None:
        r1 = await init_batch_node(_base_state())
        r2 = await init_batch_node(_base_state())
        assert r1["batch_id"] != r2["batch_id"]


# --- validate_window ---

class TestValidateWindowNode:
    @pytest.mark.asyncio
    async def test_passes_with_no_window(self) -> None:
        """No window configured → always passes."""
        result = await validate_window_node(_base_state(), window=None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_passes_with_force(self) -> None:
        """force=True bypasses window check entirely."""
        window = MaintenanceWindow(days=["sunday"], start_hour=0, end_hour=1, timezone="UTC")
        state = _base_state(force=True, force_reason="emergency patch")
        result = await validate_window_node(state, window=window)
        assert result == {}

    @pytest.mark.asyncio
    async def test_passes_inside_window(self) -> None:
        """Current time inside window → no error."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        # Patch datetime.now to a known time: Monday 03:00 UTC
        monday_3am = datetime(2026, 4, 6, 3, 0, 0, tzinfo=timezone.utc)
        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        with patch("errander.agent.graph.datetime") as mock_dt:
            mock_dt.now.return_value = monday_3am
            result = await validate_window_node(_base_state(), window=window)
        assert result == {}

    @pytest.mark.asyncio
    async def test_sets_error_outside_window(self) -> None:
        """Current time outside window → sets error, graph will short-circuit."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        # Monday 10:00 UTC — outside window [02:00, 06:00)
        monday_10am = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc)
        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        with patch("errander.agent.graph.datetime") as mock_dt:
            mock_dt.now.return_value = monday_10am
            result = await validate_window_node(_base_state(), window=window)
        assert "error" in result
        assert "Outside maintenance window" in str(result["error"])

    @pytest.mark.asyncio
    async def test_sets_error_wrong_day(self) -> None:
        """Wrong day of week → sets error."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        # Tuesday 03:00 UTC — window only allows Monday
        tuesday_3am = datetime(2026, 4, 7, 3, 0, 0, tzinfo=timezone.utc)
        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        with patch("errander.agent.graph.datetime") as mock_dt:
            mock_dt.now.return_value = tuesday_3am
            result = await validate_window_node(_base_state(), window=window)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_build_graph_with_window_blocks_outside(self, tmp_path: Path) -> None:
        """build_batch_graph with window wires it into the node."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        monday_10am = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc)

        ssh_mgr = SSHConnectionManager()
        executor = _make_executor()
        locker = FileLocker(lock_dir=tmp_path / "locks")
        async with AuditStore(":memory:") as store:
            graph = build_batch_graph(
                executor, locker, store, ssh_mgr, window=window,
            ).compile()

            with patch("errander.agent.graph.datetime") as mock_dt:
                mock_dt.now.return_value = monday_10am
                final = await graph.ainvoke({
                    "targets": [],
                    "dry_run": True,
                    "force": False,
                })

        assert "Outside maintenance window" in final.get("error", "")


# --- validate_targets ---

class TestValidateTargetsNode:
    @pytest.mark.asyncio
    async def test_healthy_target_on_success(self) -> None:
        ssh = SSHConnectionManager()
        os_release = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n'
        async with AuditStore(":memory:") as store:
            with patch.object(
                ssh, "execute", AsyncMock(return_value=_make_ssh_result(os_release)),
            ):
                result = await validate_targets_node(
                    _base_state(batch_id="b-001"),
                    ssh_manager=ssh,
                    audit_store=store,
                )

        assert len(result["healthy_targets"]) == 1
        assert result["failed_targets"] == []
        # Detected OS stored in target dict
        assert result["healthy_targets"][0]["os_family"] == "ubuntu"

    @pytest.mark.asyncio
    async def test_failed_target_on_ssh_error(self) -> None:
        ssh = SSHConnectionManager()
        async with AuditStore(":memory:") as store:
            with patch.object(
                ssh, "execute",
                AsyncMock(side_effect=ConnectionError("refused")),
            ):
                result = await validate_targets_node(
                    _base_state(batch_id="b-002"),
                    ssh_manager=ssh,
                    audit_store=store,
                )

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1

    @pytest.mark.asyncio
    async def test_failed_target_on_nonzero_exit(self) -> None:
        ssh = SSHConnectionManager()
        async with AuditStore(":memory:") as store:
            with patch.object(
                ssh, "execute",
                AsyncMock(return_value=_make_ssh_result("", exit_code=1)),
            ):
                result = await validate_targets_node(
                    _base_state(batch_id="b-003"),
                    ssh_manager=ssh,
                    audit_store=store,
                )

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1

    @pytest.mark.asyncio
    async def test_partitions_multiple_targets(self) -> None:
        ssh = SSHConnectionManager()
        targets = [
            _make_target(vm_id="dev/web-01", hostname="10.0.1.10"),
            _make_target(vm_id="dev/web-02", hostname="10.0.1.11"),
        ]
        os_release = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n'
        ssh_results = [
            _make_ssh_result(os_release),          # web-01 succeeds
            _make_ssh_result("", exit_code=1),     # web-02 fails
        ]
        async with AuditStore(":memory:") as store:
            with patch.object(
                ssh, "execute", AsyncMock(side_effect=ssh_results),
            ):
                result = await validate_targets_node(
                    _base_state(batch_id="b-004", targets=targets),
                    ssh_manager=ssh,
                    audit_store=store,
                )

        assert len(result["healthy_targets"]) == 1
        assert len(result["failed_targets"]) == 1
        assert result["healthy_targets"][0]["vm_id"] == "dev/web-01"
        assert result["failed_targets"][0]["vm_id"] == "dev/web-02"


# --- Routing ---

class TestRouting:
    def test_route_after_window_no_error(self) -> None:
        assert route_after_window(_base_state()) == "validate_targets"

    def test_route_after_window_with_error(self) -> None:
        assert route_after_window(_base_state(error="outside window")) == "generate_report"

    def test_route_after_validate_healthy(self) -> None:
        state = _base_state(healthy_targets=[_make_target()])
        assert route_after_validate(state) == "fan_out"

    def test_route_after_validate_no_healthy(self) -> None:
        assert route_after_validate(_base_state()) == "generate_report"


# --- fan_out routing ---

class TestFanOutRouter:
    def test_creates_send_per_healthy_target(self, tmp_path: Path) -> None:
        from langgraph.types import Send
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        ssh = SSHConnectionManager()

        route_fn, _ = make_fan_out_router(None, executor, locker, audit_store, ssh)

        targets = [
            _make_target(vm_id="dev/web-01"),
            _make_target(vm_id="dev/web-02"),
        ]
        state = _base_state(batch_id="b-fan-01", healthy_targets=targets)
        result = route_fn(state)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(s, Send) for s in result)

    def test_returns_generate_report_when_no_healthy(self, tmp_path: Path) -> None:
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        ssh = SSHConnectionManager()

        route_fn, _ = make_fan_out_router(None, executor, locker, audit_store, ssh)
        result = route_fn(_base_state(healthy_targets=[]))

        assert result == "generate_report"


# --- collect_results / generate_report ---

class TestCollectResultsNode:
    @pytest.mark.asyncio
    async def test_passthrough(self) -> None:
        state = _base_state(vm_results=[{"action_type": "disk_cleanup"}])
        result = await collect_results_node(state)
        assert result == {}


class TestGenerateReportNode:
    @pytest.mark.asyncio
    async def test_generates_report(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat()
        state = _base_state(
            batch_id="b-report-01",
            vm_results=[{
                "action_type": "disk_cleanup",
                "status": ActionStatus.DRY_RUN_OK.value,
                "vm_id": "dev/web-01",
                "started_at": now,
                "completed_at": now,
                "detail": "",
                "error": None,
            }],
        )
        result = await generate_report_node(state)
        assert "report" in result
        assert "b-report-01" in result["report"]
        assert "dev/web-01" in result["report"]

    @pytest.mark.asyncio
    async def test_report_with_empty_results(self) -> None:
        state = _base_state(batch_id="b-empty")
        result = await generate_report_node(state)
        assert "b-empty" in result["report"]
        assert "report" in result


# --- Full graph ---

class TestBuildBatchGraph:
    def test_graph_builds(self, tmp_path: Path) -> None:
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()

        graph = build_batch_graph(executor, locker, audit_store, ssh)
        assert graph is not None

    def test_graph_compiles(self, tmp_path: Path) -> None:
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()

        compiled = build_batch_graph(executor, locker, audit_store, ssh).compile()
        assert compiled is not None

    @pytest.mark.asyncio
    async def test_no_healthy_targets_produces_report(self, tmp_path: Path) -> None:
        """When all targets fail validation, report is generated with 0 actions."""
        executor = _make_executor()
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh = SSHConnectionManager()

        with patch.object(
            ssh, "execute",
            AsyncMock(side_effect=ConnectionError("refused")),
        ):
            compiled = build_batch_graph(executor, locker, audit_store, ssh).compile()
            final = await compiled.ainvoke(_base_state())

        assert final.get("report", "") != ""
        assert len(final.get("healthy_targets", [])) == 0

    @pytest.mark.asyncio
    async def test_full_dry_run_single_vm(self, tmp_path: Path) -> None:
        """Full flow: init → window → validate → fan_out → run_vm(disk_cleanup) → report."""
        from errander.models.vm import OSFamily, VMInfo

        executor = _make_executor(dry_run=True)
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh_main = SSHConnectionManager()

        vm_info = VMInfo(
            os_family=OSFamily.UBUNTU,
            os_version="Ubuntu 22.04",
            disk_usage={"/": 55.0},
            docker_available=False,
            pending_packages=0,
            uptime_seconds=86400.0,
        )

        df_output = (
            "Filesystem Size Used Avail Use% Mounted on\n"
            "/dev/sda1 20G 10G 10G 50% /\n"
        )

        # ssh_main.execute is used by validate_targets (1 os-release call)
        # executor._ssh.execute is used by discover + disk_cleanup sub-graph
        os_release = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n'
        with patch.object(
            ssh_main, "execute",
            AsyncMock(return_value=_make_ssh_result(os_release)),
        ):
            # patch graph.detect_os so plan_vm_node plans disk_cleanup (disk=55%)
            with patch(
                "errander.agent.graph.detect_os",
                AsyncMock(return_value=vm_info),
            ):
                with patch(
                    "errander.agent.vm_graph.detect_os",
                    AsyncMock(return_value=vm_info),
                ):
                    disk_cleanup_responses = [
                        # assess: df + 5 paths (both apt-cache AND yum-cache, frozenset order varies)
                        _make_ssh_result(df_output),
                        _make_ssh_result("1.0M\ttotal"),
                        _make_ssh_result("500K"),
                        _make_ssh_result("200M"),
                        _make_ssh_result("0 packages"),
                        _make_ssh_result("500K"),    # 6th assess: second cache path
                        # execute: 5 paths × simulate_command (dry_run=True)
                        _make_ssh_result("done"),
                        _make_ssh_result("done"),
                        _make_ssh_result("done"),
                        _make_ssh_result("done"),
                        _make_ssh_result("done"),    # 5th execute: second cache path
                        # log_rotation assess (dry_run=False): no large files → nothing_to_do
                        _make_ssh_result(""),
                    ]
                    with patch.object(
                        executor._ssh, "execute",
                        AsyncMock(side_effect=disk_cleanup_responses),
                    ):
                        compiled = build_batch_graph(
                            executor, locker, audit_store, ssh_main,
                        ).compile()
                        final = await compiled.ainvoke(_base_state())

        assert final.get("batch_id", "").startswith("batch-")
        assert len(final.get("healthy_targets", [])) == 1
        assert final.get("report", "") != ""

        # Disk cleanup result in vm_results
        vm_results = final.get("vm_results", [])
        assert any(r.get("action_type") == "disk_cleanup" for r in vm_results)
        disk_result = next(r for r in vm_results if r.get("action_type") == "disk_cleanup")
        assert disk_result["status"] == ActionStatus.DRY_RUN_OK.value


# --- Exception safety tests for run_vm_node ---

class TestRunVmNodeExceptionSafety:
    """Step 1C: run_vm_node must catch VM graph crashes and return a FAILED entry."""

    @pytest.mark.asyncio
    async def test_run_vm_node_catches_vm_graph_crash(self, tmp_path: Path) -> None:
        """VM graph crash is caught; vm_results contains a FAILED entry."""
        from errander.agent.graph import run_vm_node
        from errander.agent.vm_graph import VMGraphState

        bad_compiled = MagicMock()
        bad_compiled.ainvoke = AsyncMock(side_effect=RuntimeError("graph exploded"))

        state = VMGraphState(
            vm_id="dev/web-01",
            batch_id="batch-test",
            dry_run=True,
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="/key",
            os_family="ubuntu",
            locked=False,
            results=[],
            current_action_index=0,
            planned_actions=[],
            error=None,
        )

        result = await run_vm_node(state, vm_compiled=bad_compiled)

        assert len(result["vm_results"]) == 1
        assert result["vm_results"][0]["status"] == ActionStatus.FAILED.value
        assert result["vm_results"][0]["vm_id"] == "dev/web-01"
        assert "graph exploded" in str(result["vm_results"][0].get("error", ""))

    @pytest.mark.asyncio
    async def test_batch_continues_when_one_vm_crashes(self, tmp_path: Path) -> None:
        """When one VM graph crashes, remaining VMs complete and report is generated."""
        from errander.models.vm import OSFamily, VMInfo

        executor = _make_executor(dry_run=True)
        locker = _make_locker(tmp_path)
        audit_store = MagicMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()
        ssh_main = SSHConnectionManager()

        targets = [
            _make_target(vm_id="vm-01", hostname="10.0.1.1"),
            _make_target(vm_id="vm-02", hostname="10.0.1.2"),
        ]

        # Both targets pass SSH validation
        with patch.object(
            ssh_main, "execute",
            AsyncMock(return_value=_make_ssh_result("ok")),
        ):
            # vm_graph always raises (simulating crash for all VMs)
            with patch(
                "errander.agent.graph.build_vm_graph",
                return_value=MagicMock(
                    compile=MagicMock(
                        return_value=MagicMock(
                            ainvoke=AsyncMock(side_effect=RuntimeError("crash")),
                        )
                    )
                ),
            ):
                compiled = build_batch_graph(
                    executor, locker, audit_store, ssh_main,
                ).compile()
                final = await compiled.ainvoke(_base_state(targets=targets))

        # Report was still generated (graph completed)
        assert final.get("report", "") != ""
        # vm_results has FAILED entries
        vm_results = final.get("vm_results", [])
        assert all(r.get("status") == ActionStatus.FAILED.value for r in vm_results)


# ---------------------------------------------------------------------------
# approval_gate_node — deferred execution
# ---------------------------------------------------------------------------

class TestApprovalGateDeferred:
    """Deferred execution: live runs outside window → defer; dry-run always immediate."""

    def _make_approval_state(
        self,
        dry_run: bool = True,
        env_name: str = "production",
        vm_results: list[dict[str, object]] | None = None,
    ) -> BatchGraphState:
        return _base_state(
            dry_run=dry_run,
            env_name=env_name,
            vm_results=vm_results or [],
            report="Test report",
        )

    @pytest.mark.asyncio
    async def test_dry_run_outside_window_never_deferred(self) -> None:
        """Dry-run is always auto-approved immediately, never deferred regardless of window."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        from errander.agent.graph import approval_gate_node
        from errander.safety.deferred import DeferredExecutionStore

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        # Monday outside window — dry-run should still NOT be deferred
        monday_10am = datetime(2030, 1, 7, 10, 0, 0, tzinfo=timezone.utc)

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        try:
            state = self._make_approval_state(dry_run=True)
            with patch("errander.agent.graph.datetime") as mock_dt:
                mock_dt.now.return_value = monday_10am
                result = await approval_gate_node(
                    state,
                    window=window,
                    deferred_store=deferred_store,
                )

            assert result.get("deferred") is False
            assert result.get("approved") is True
            pending = await deferred_store.get_pending("production")
            assert len(pending) == 0
        finally:
            await deferred_store.close()

    @pytest.mark.asyncio
    async def test_dry_run_inside_window_not_deferred(self) -> None:
        """Dry-run approval while inside window → not deferred."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        from errander.agent.graph import approval_gate_node
        from errander.safety.deferred import DeferredExecutionStore

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        monday_3am = datetime(2026, 4, 6, 3, 0, 0, tzinfo=timezone.utc)

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        try:
            state = self._make_approval_state()
            with patch("errander.agent.graph.datetime") as mock_dt:
                mock_dt.now.return_value = monday_3am
                result = await approval_gate_node(
                    state,
                    window=window,
                    deferred_store=deferred_store,
                )

            assert result.get("deferred") is False
            assert await deferred_store.get_pending("production") == []
        finally:
            await deferred_store.close()

    @pytest.mark.asyncio
    async def test_live_run_deferred_when_outside_window(self) -> None:
        """Live run approved while outside maintenance window is deferred to next window."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        from errander.agent.graph import approval_gate_node
        from errander.safety.deferred import DeferredExecutionStore

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        # 2030-01-07 is a Monday at 10:00 UTC — outside [02:00, 06:00).
        monday_10am = datetime(2030, 1, 7, 10, 0, 0, tzinfo=timezone.utc)

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        try:
            state = self._make_approval_state(dry_run=False)
            mgr = MagicMock(spec=ApprovalManager)
            with (
                patch("errander.agent.graph.datetime") as mock_dt,
                patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval,
            ):
                mock_dt.now.return_value = monday_10am
                mock_approval.return_value = (True, "test-approver")
                result = await approval_gate_node(
                    state,
                    approval_manager=mgr,
                    window=window,
                    deferred_store=deferred_store,
                )

            assert result.get("deferred") is True
            assert result.get("approved") is True
            pending = await deferred_store.get_pending("production")
            assert len(pending) == 1
        finally:
            await deferred_store.close()

    @pytest.mark.asyncio
    async def test_no_window_configured_not_deferred(self) -> None:
        """No window → never deferred."""
        from errander.agent.graph import approval_gate_node
        from errander.safety.deferred import DeferredExecutionStore

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        try:
            state = self._make_approval_state()
            result = await approval_gate_node(
                state,
                window=None,
                deferred_store=deferred_store,
            )
            assert result.get("deferred") is False
        finally:
            await deferred_store.close()

    @pytest.mark.asyncio
    async def test_live_run_outside_window_notifies_slack(self) -> None:
        """When deferring a live run, Slack gets a notification with the window time."""
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, patch

        from errander.agent.graph import approval_gate_node
        from errander.safety.deferred import DeferredExecutionStore

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        monday_10am = datetime(2030, 1, 7, 10, 0, 0, tzinfo=timezone.utc)

        slack_client = MagicMock()
        slack_client.post_alert = AsyncMock()

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        try:
            state = self._make_approval_state(dry_run=False)
            mgr = MagicMock(spec=ApprovalManager)
            with (
                patch("errander.agent.graph.datetime") as mock_dt,
                patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval,
            ):
                mock_dt.now.return_value = monday_10am
                mock_approval.return_value = (True, "test-approver")
                await approval_gate_node(
                    state,
                    approval_manager=mgr,
                    window=window,
                    deferred_store=deferred_store,
                    slack_client=slack_client,
                )

            slack_client.post_alert.assert_awaited_once()
            call_text: str = slack_client.post_alert.call_args[0][0]
            assert "scheduled for" in call_text
        finally:
            await deferred_store.close()

    @pytest.mark.asyncio
    async def test_live_run_outside_window_logs_audit_event(self) -> None:
        """Deferral of a live run logs an EXECUTION_DEFERRED audit event."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        from errander.agent.graph import approval_gate_node
        from errander.models.events import EventType
        from errander.safety.audit import AuditStore
        from errander.safety.deferred import DeferredExecutionStore

        window = MaintenanceWindow(
            days=["monday"], start_hour=2, end_hour=6, timezone="UTC"
        )
        monday_10am = datetime(2030, 1, 7, 10, 0, 0, tzinfo=timezone.utc)

        deferred_store = DeferredExecutionStore(":memory:")
        await deferred_store.initialize()

        async with AuditStore(":memory:") as audit_store:
            try:
                state = self._make_approval_state(dry_run=False)
                mgr = MagicMock(spec=ApprovalManager)
                with (
                    patch("errander.agent.graph.datetime") as mock_dt,
                    patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval,
                ):
                    mock_dt.now.return_value = monday_10am
                    mock_approval.return_value = (True, "test-approver")
                    await approval_gate_node(
                        state,
                        approval_manager=mgr,
                        window=window,
                        deferred_store=deferred_store,
                        audit_store=audit_store,
                    )

                events = await audit_store.get_events(batch_id=state.get("batch_id", "unknown"))
                deferred_events = [e for e in events if e.event_type == EventType.EXECUTION_DEFERRED]
                assert len(deferred_events) == 1
            finally:
                await deferred_store.close()
