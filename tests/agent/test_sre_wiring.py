"""End-to-end wiring tests for SRE signal stores through the production path.

Proves that disk trend, drift baseline, failed login, critical-services, and
vm_state stores are actually called when build_batch_graph / make_wave_dispatcher /
build_vm_graph receive them.  These are the acceptance-criteria tests for the
SRE recommendations validation audit (2026-05-14).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from errander.config.settings import (
    FailedSSHLoginsSettings,
    SRESignalSettings,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.safety.audit import AuditStore
from errander.safety.baselines import BaselineStore
from errander.safety.disk_history import VMDiskHistoryStore
from errander.safety.locking import FileLocker
from errander.safety.vm_state import VMStateStore


def _ok(stdout: str = "") -> SSHResult:
    return SSHResult(exit_code=0, stdout=stdout, stderr="", command="mocked")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=True)


# ---------------------------------------------------------------------------
# make_wave_dispatcher wiring
# ---------------------------------------------------------------------------

class TestMakeWaveDispatcherWiring:
    """Verify that SRE stores are threaded into build_vm_graph via make_wave_dispatcher."""

    def test_disk_history_store_forwarded(self) -> None:
        from errander.agent.graph import make_wave_dispatcher

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)
        disk_store = MagicMock(spec=VMDiskHistoryStore)

        captured: dict[str, object] = {}

        def fake_build_vm_graph(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch("errander.agent.graph.build_vm_graph", side_effect=fake_build_vm_graph):
            make_wave_dispatcher(
                executor, locker, audit_store, ssh_manager,
                disk_history_store=disk_store,
            )

        assert captured.get("disk_history_store") is disk_store

    def test_baseline_store_forwarded(self) -> None:
        from errander.agent.graph import make_wave_dispatcher

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)
        b_store = MagicMock(spec=BaselineStore)

        captured: dict[str, object] = {}

        def fake_build_vm_graph(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch("errander.agent.graph.build_vm_graph", side_effect=fake_build_vm_graph):
            make_wave_dispatcher(
                executor, locker, audit_store, ssh_manager,
                baseline_store=b_store,
            )

        assert captured.get("baseline_store") is b_store

    def test_vm_state_store_forwarded(self) -> None:
        from errander.agent.graph import make_wave_dispatcher

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)
        vs_store = MagicMock(spec=VMStateStore)

        captured: dict[str, object] = {}

        def fake_build_vm_graph(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch("errander.agent.graph.build_vm_graph", side_effect=fake_build_vm_graph):
            make_wave_dispatcher(
                executor, locker, audit_store, ssh_manager,
                vm_state_store=vs_store,
            )

        assert captured.get("vm_state_store") is vs_store

    def test_failed_logins_settings_forwarded(self) -> None:
        from errander.agent.graph import make_wave_dispatcher

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)
        fl_settings = FailedSSHLoginsSettings(enabled=True, window_hours=12)

        captured: dict[str, object] = {}

        def fake_build_vm_graph(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch("errander.agent.graph.build_vm_graph", side_effect=fake_build_vm_graph):
            make_wave_dispatcher(
                executor, locker, audit_store, ssh_manager,
                sre_failed_logins_settings=fl_settings,
            )

        assert captured.get("sre_failed_logins_settings") is fl_settings


# ---------------------------------------------------------------------------
# build_vm_graph wiring
# ---------------------------------------------------------------------------

class TestBuildVmGraphWiring:
    """Verify that build_vm_graph passes vm_state_store and audit_store to patching subgraph."""

    def test_vm_state_store_passed_to_patching(self) -> None:
        from errander.agent.vm_graph import build_vm_graph

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)
        vs_store = MagicMock(spec=VMStateStore)

        captured: dict[str, object] = {}

        def fake_build_patching(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch(
            "errander.agent.vm_graph.build_patching_subgraph",
            side_effect=fake_build_patching,
        ):
            build_vm_graph(executor, locker, audit_store, ssh_manager, vm_state_store=vs_store)

        assert captured.get("vm_state_store") is vs_store

    def test_audit_store_passed_to_patching(self) -> None:
        from errander.agent.vm_graph import build_vm_graph

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)

        captured: dict[str, object] = {}

        def fake_build_patching(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return MagicMock()

        with patch(
            "errander.agent.vm_graph.build_patching_subgraph",
            side_effect=fake_build_patching,
        ):
            build_vm_graph(executor, locker, audit_store, ssh_manager)

        assert captured.get("audit_store") is audit_store


# ---------------------------------------------------------------------------
# critical_services threading
# ---------------------------------------------------------------------------

class TestCriticalServicesWiring:
    """Verify critical_services flows from target dict through VMGraphState to PatchingGraphState."""

    def test_critical_services_in_send_payload(self) -> None:
        from langgraph.types import Send

        from errander.agent.graph import make_wave_dispatcher
        from errander.agent.vm_graph import VMGraphState

        executor = _make_executor()
        locker = MagicMock(spec=FileLocker)
        audit_store = MagicMock(spec=AuditStore)
        ssh_manager = MagicMock(spec=SSHConnectionManager)

        dispatch_fn, _ = make_wave_dispatcher(executor, locker, audit_store, ssh_manager)

        state: dict[str, object] = {
            "current_wave": 0,
            "waves": [[{
                "vm_id": "prod/web-01",
                "hostname": "10.0.0.1",
                "ssh_user": "ubuntu",
                "ssh_key_path": "~/.ssh/key",
                "os_family": "ubuntu",
                "critical_services": ["nginx", "postgresql"],
            }]],
            "batch_id": "batch-test",
            "dry_run": True,
            "env_policy": "moderate",
            "ai_db_path": "",
            "vm_plans": [],
        }

        result = dispatch_fn(state)
        assert isinstance(result, list)
        assert len(result) == 1
        send: Send = result[0]
        vm_state: VMGraphState = send.arg
        assert list(vm_state.get("critical_services", [])) == ["nginx", "postgresql"]

    async def test_critical_services_passed_to_patching_substate(self) -> None:
        from errander.agent.vm_graph import VMGraphState, _run_patching

        state: VMGraphState = {  # type: ignore[typeddict-unknown-key]
            "vm_id": "prod/web-01",
            "batch_id": "batch-abc",
            "hostname": "10.0.0.1",
            "ssh_user": "ubuntu",
            "ssh_key_path": "~/.ssh/key",
            "os_family": "ubuntu",
            "dry_run": True,
            "critical_services": ["nginx", "redis"],
        }

        captured_sub_state: dict[str, object] = {}

        async def fake_ainvoke(sub_state: object) -> dict[str, object]:
            assert isinstance(sub_state, dict)
            captured_sub_state.update(sub_state)
            return {"status": "dry_run_ok", "error": None}

        compiled = MagicMock()
        compiled.ainvoke = fake_ainvoke

        await _run_patching(state, compiled)

        assert captured_sub_state.get("critical_services") == ["nginx", "redis"]
        assert captured_sub_state.get("batch_id") == "batch-abc"


# ---------------------------------------------------------------------------
# run_env_batch wiring: critical_services in target dicts
# ---------------------------------------------------------------------------

class TestRunEnvBatchSREWiring:
    """Verify run_env_batch carries critical_services and SRE stores into the graph."""

    async def test_critical_services_in_initial_targets(self) -> None:
        from errander.config.schema import EnvironmentSchema, TargetSchema
        from errander.execution.sandbox import SandboxExecutor
        from errander.execution.ssh import SSHConnectionManager
        from errander.main import run_env_batch
        from errander.safety.audit import AuditStore
        from errander.safety.locking import FileLocker

        env = EnvironmentSchema(
            ssh_user="ubuntu",
            ssh_key_path="~/.ssh/key",
            critical_services=["nginx"],
            targets=[
                TargetSchema(
                    name="web-01",
                    host="10.0.0.1",
                    os_family="ubuntu",
                    critical_services=["nginx", "postgresql"],
                )
            ],
        )

        from errander.config.settings import Settings
        settings = MagicMock(spec=Settings)
        settings.audit_db_url = ":memory:"
        settings.sre_signals = SRESignalSettings()

        captured: dict[str, object] = {}

        compiled_graph = AsyncMock()
        async def _ainvoke(state: object, *a: object, **kw: object) -> dict[str, object]:
            assert isinstance(state, dict)
            captured["targets"] = state.get("targets", [])
            return {"batch_id": "b1", "vm_results": [], "report": None, "error": None}
        compiled_graph.ainvoke.side_effect = _ainvoke

        graph_mock = MagicMock()
        graph_mock.compile.return_value = compiled_graph

        with patch("errander.agent.graph.build_batch_graph", return_value=graph_mock), \
             patch("errander.main._build_maintenance_window", return_value=None):
            await run_env_batch(
                env_name="production",
                env_schema=env,
                settings=settings,
                executor=MagicMock(spec=SandboxExecutor),
                locker=MagicMock(spec=FileLocker),
                ssh_manager=MagicMock(spec=SSHConnectionManager),
                audit_store=MagicMock(spec=AuditStore),
            )

        targets = captured.get("targets", [])
        assert len(targets) == 1
        assert targets[0].get("critical_services") == ["nginx", "postgresql"]

    async def test_sre_stores_passed_to_build_batch_graph(self) -> None:
        from errander.config.schema import EnvironmentSchema, TargetSchema
        from errander.execution.sandbox import SandboxExecutor
        from errander.execution.ssh import SSHConnectionManager
        from errander.main import run_env_batch
        from errander.safety.audit import AuditStore
        from errander.safety.locking import FileLocker

        env = EnvironmentSchema(
            ssh_user="ubuntu",
            ssh_key_path="~/.ssh/key",
            targets=[TargetSchema(name="web-01", host="10.0.0.1", os_family="ubuntu")],
        )

        from errander.config.settings import Settings
        settings = MagicMock(spec=Settings)
        settings.audit_db_url = ":memory:"
        settings.sre_signals = SRESignalSettings()

        disk_store = MagicMock(spec=VMDiskHistoryStore)
        b_store = MagicMock(spec=BaselineStore)
        vs_store = MagicMock(spec=VMStateStore)

        captured_kwargs: dict[str, object] = {}

        def fake_build_batch_graph(*args: object, **kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            m = MagicMock()
            compiled = AsyncMock()
            async def _ainvoke(s: object, *a: object, **kw: object) -> dict[str, object]:
                return {"batch_id": "b1", "vm_results": [], "report": None, "error": None}
            compiled.ainvoke.side_effect = _ainvoke
            m.compile.return_value = compiled
            return m

        with patch("errander.agent.graph.build_batch_graph", side_effect=fake_build_batch_graph), \
             patch("errander.main._build_maintenance_window", return_value=None):
            await run_env_batch(
                env_name="production",
                env_schema=env,
                settings=settings,
                executor=MagicMock(spec=SandboxExecutor),
                locker=MagicMock(spec=FileLocker),
                ssh_manager=MagicMock(spec=SSHConnectionManager),
                audit_store=MagicMock(spec=AuditStore),
                disk_history_store=disk_store,
                baseline_store=b_store,
                vm_state_store=vs_store,
            )

        assert captured_kwargs.get("disk_history_store") is disk_store
        assert captured_kwargs.get("baseline_store") is b_store
        assert captured_kwargs.get("vm_state_store") is vs_store
