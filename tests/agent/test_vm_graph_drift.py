"""Tests for drift_baseline_node and failed_logins_node in vm_graph (PR-1.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from errander.agent.vm_graph import (
    VMGraphState,
    build_vm_graph,
    drift_baseline_node,
    failed_logins_node,
)
from errander.config.settings import DriftSettings, FailedSSHLoginsSettings
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager
from errander.safety.audit import AuditStore
from errander.safety.baselines import BaselineCapture, BaselineComparison, BaselineStore
from errander.safety.locking import FileLocker


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


def _make_state(**overrides: object) -> VMGraphState:
    base: VMGraphState = {
        "vm_id": "dev/web-01",
        "batch_id": "batch-001",
        "dry_run": True,
        "hostname": "10.0.0.1",
        "ssh_user": "admin",
        "ssh_key_path": "/key",
        "vm_info": {
            "hostname": "10.0.0.1",
            "ssh_user": "admin",
            "ssh_key_path": "/key",
        },
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_comparison(
    *,
    is_first_run: bool = False,
    changed: bool = False,
    unified_diff: str = "",
) -> BaselineComparison:
    capture = BaselineCapture(kind="sudoers", scope_key="", content="root ALL=(ALL) ALL")
    return BaselineComparison(
        is_first_run=is_first_run,
        changed=changed,
        previous=None if is_first_run else capture,
        current=capture,
        unified_diff=unified_diff,
    )


# --- drift_baseline_node ---

class TestDriftBaselineNode:
    async def test_wrong_store_type_returns_empty(self) -> None:
        state = _make_state()
        result = await drift_baseline_node(
            state,
            executor=_make_executor(),
            baseline_store=object(),  # wrong type
            audit_store=None,
            settings=DriftSettings(),
        )
        assert result == {"drift_changes": []}

    async def test_wrong_settings_type_returns_empty(self) -> None:
        store = AsyncMock(spec=BaselineStore)
        state = _make_state()
        result = await drift_baseline_node(
            state,
            executor=_make_executor(),
            baseline_store=store,
            audit_store=None,
            settings=object(),  # wrong type
        )
        assert result == {"drift_changes": []}

    async def test_first_run_no_change_recorded(self) -> None:
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(return_value=_make_comparison(is_first_run=True))
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="x")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        # First run → no change → no drift_changes entry
        assert result["drift_changes"] == []

    async def test_changed_content_recorded(self) -> None:
        diff = "@@ -1 +1 @@\n-old\n+new\n"
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(changed=True, unified_diff=diff),
        )
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="new")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        assert len(result["drift_changes"]) == 1
        change = result["drift_changes"][0]
        assert change["kind"] == "sudoers"
        assert "@@ -1 +1 @@" in str(change["unified_diff"])

    async def test_unchanged_content_no_change_recorded(self) -> None:
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(is_first_run=False, changed=False),
        )
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="x")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        assert result["drift_changes"] == []

    async def test_emits_drift_kind_changed_event(self) -> None:
        diff = "@@ -1 +1 @@\n-old\n+new\n"
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(changed=True, unified_diff=diff),
        )
        audit_store = AsyncMock(spec=AuditStore)
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="new")]),
        ):
            await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=audit_store,
                settings=settings,
            )
        audit_store.log_event.assert_called_once()
        event = audit_store.log_event.call_args[0][0]
        from errander.models.events import EventType
        assert event.event_type == EventType.DRIFT_KIND_CHANGED

    async def test_emits_baseline_saved_event_on_first_run(self) -> None:
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(return_value=_make_comparison(is_first_run=True))
        audit_store = AsyncMock(spec=AuditStore)
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="x")]),
        ):
            await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=audit_store,
                settings=settings,
            )
        audit_store.log_event.assert_called_once()
        event = audit_store.log_event.call_args[0][0]
        from errander.models.events import EventType
        assert event.event_type == EventType.DRIFT_KIND_BASELINE_SAVED

    async def test_no_audit_store_no_error(self) -> None:
        diff = "@@ -1 +1 @@\n-old\n+new\n"
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(changed=True, unified_diff=diff),
        )
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="new")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        assert len(result["drift_changes"]) == 1

    async def test_diff_truncated_to_max_lines(self) -> None:
        big_diff = "\n".join([f"line {i}" for i in range(200)])
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(changed=True, unified_diff=big_diff),
        )
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
            diff_max_lines=10,
        )
        state = _make_state(dry_run=False)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="x")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        diff_text = str(result["drift_changes"][0]["unified_diff"])
        assert "truncated" in diff_text
        assert len(diff_text.splitlines()) <= 15  # 10 lines + truncation note

    async def test_all_four_checks_run_when_enabled(self) -> None:
        comparison = _make_comparison(is_first_run=True)
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(return_value=comparison)
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=True,
            listening_ports=True,
            scheduled_jobs=True,
        )
        state = _make_state(dry_run=False)

        capture_calls: list[str] = []

        async def mock_sudoers(*a: object, **k: object) -> list[BaselineCapture]:
            capture_calls.append("sudoers")
            return [BaselineCapture(kind="sudoers", scope_key="", content="")]

        async def mock_auth_keys(*a: object, **k: object) -> list[BaselineCapture]:
            capture_calls.append("authorized_keys")
            return [BaselineCapture(kind="authorized_keys", scope_key="user", content="")]

        async def mock_ports(*a: object, **k: object) -> list[BaselineCapture]:
            capture_calls.append("listening_ports")
            return [BaselineCapture(kind="listening_ports", scope_key="", content="")]

        async def mock_jobs(*a: object, **k: object) -> list[BaselineCapture]:
            capture_calls.append("scheduled_jobs")
            return [BaselineCapture(kind="scheduled_jobs", scope_key="", content="")]

        with (
            patch("errander.safety.drift_checks.capture_sudoers", mock_sudoers),
            patch("errander.safety.drift_checks.capture_authorized_keys", mock_auth_keys),
            patch("errander.safety.drift_checks.capture_listening_ports", mock_ports),
            patch("errander.safety.drift_checks.capture_scheduled_jobs", mock_jobs),
        ):
            await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )

        assert "sudoers" in capture_calls
        assert "authorized_keys" in capture_calls
        assert "listening_ports" in capture_calls
        assert "scheduled_jobs" in capture_calls

    async def test_disabled_checks_not_run(self) -> None:
        comparison = _make_comparison(is_first_run=True)
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(return_value=comparison)
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=False,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=False)

        result = await drift_baseline_node(
            state, executor=executor,
            baseline_store=store,
            audit_store=None,
            settings=settings,
        )
        store.compare_and_save.assert_not_called()
        assert result["drift_changes"] == []

    async def test_dry_run_skips_compare_and_save(self) -> None:
        store = AsyncMock(spec=BaselineStore)
        store.compare_and_save = AsyncMock(
            return_value=_make_comparison(changed=True),
        )
        executor = _make_executor()
        settings = DriftSettings(
            sudoers=True,
            authorized_keys=False,
            listening_ports=False,
            scheduled_jobs=False,
        )
        state = _make_state(dry_run=True)

        with patch(
            "errander.safety.drift_checks.capture_sudoers",
            AsyncMock(return_value=[BaselineCapture(kind="sudoers", scope_key="", content="x")]),
        ):
            result = await drift_baseline_node(
                state, executor=executor,
                baseline_store=store,
                audit_store=None,
                settings=settings,
            )
        store.compare_and_save.assert_not_called()
        assert result["drift_changes"] == []


# --- failed_logins_node ---

class TestFailedLoginsNode:
    async def test_wrong_settings_type_returns_none(self) -> None:
        state = _make_state()
        result = await failed_logins_node(
            state,
            executor=_make_executor(),
            audit_store=None,
            settings=object(),  # wrong type
        )
        assert result == {"failed_login_summary": None}

    async def test_ssh_failure_returns_none(self) -> None:

        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        state = _make_state()

        with patch(
            "errander.execution.failed_logins.detect_failed_logins",
            AsyncMock(return_value=None),
        ):
            result = await failed_logins_node(
                state, executor=executor, audit_store=None, settings=settings,
            )
        assert result == {"failed_login_summary": None}

    async def test_zero_failures_no_event(self) -> None:
        from errander.models.reports import FailedLoginSummary

        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        audit_store = AsyncMock(spec=AuditStore)
        state = _make_state()

        summary = FailedLoginSummary(
            vm_id="dev/web-01",
            window_hours=24,
            total_count=0,
            top_users=(),
            top_source_ips=(),
        )
        with patch(
            "errander.execution.failed_logins.detect_failed_logins",
            AsyncMock(return_value=summary),
        ):
            result = await failed_logins_node(
                state, executor=executor, audit_store=audit_store, settings=settings,
            )
        audit_store.log_event.assert_not_called()
        assert result["failed_login_summary"] is not None
        assert result["failed_login_summary"]["total_count"] == 0  # type: ignore[index]

    async def test_non_zero_failures_emits_event(self) -> None:
        from errander.models.events import EventType
        from errander.models.reports import FailedLoginSummary

        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        audit_store = AsyncMock(spec=AuditStore)
        state = _make_state()

        summary = FailedLoginSummary(
            vm_id="dev/web-01",
            window_hours=24,
            total_count=5,
            top_users=(("root", 5),),
            top_source_ips=(("1.2.3.4", 5),),
        )
        with patch(
            "errander.execution.failed_logins.detect_failed_logins",
            AsyncMock(return_value=summary),
        ):
            await failed_logins_node(
                state, executor=executor, audit_store=audit_store, settings=settings,
            )
        audit_store.log_event.assert_called_once()
        event = audit_store.log_event.call_args[0][0]
        assert event.event_type == EventType.FAILED_SSH_LOGINS_OBSERVED

    async def test_summary_serialised_correctly(self) -> None:
        from errander.models.reports import FailedLoginSummary

        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        state = _make_state()

        summary = FailedLoginSummary(
            vm_id="dev/web-01",
            window_hours=24,
            total_count=3,
            top_users=(("root", 2), ("admin", 1)),
            top_source_ips=(("1.2.3.4", 3),),
        )
        with patch(
            "errander.execution.failed_logins.detect_failed_logins",
            AsyncMock(return_value=summary),
        ):
            result = await failed_logins_node(
                state, executor=executor, audit_store=None, settings=settings,
            )
        s = result["failed_login_summary"]
        assert s is not None
        assert s["total_count"] == 3  # type: ignore[index]
        assert s["window_hours"] == 24  # type: ignore[index]
        assert ["root", 2] in s["top_users"]  # type: ignore[index]

    async def test_no_audit_store_no_error(self) -> None:
        from errander.models.reports import FailedLoginSummary

        executor = _make_executor()
        settings = FailedSSHLoginsSettings()
        state = _make_state()

        summary = FailedLoginSummary(
            vm_id="dev/web-01",
            window_hours=24,
            total_count=10,
            top_users=(("root", 10),),
            top_source_ips=(("1.2.3.4", 10),),
        )
        with patch(
            "errander.execution.failed_logins.detect_failed_logins",
            AsyncMock(return_value=summary),
        ):
            result = await failed_logins_node(
                state, executor=executor, audit_store=None, settings=settings,
            )
        assert result["failed_login_summary"] is not None


# --- build_vm_graph wiring ---

class TestBuildVmGraphWithDriftNodes:
    def _make_locker(self) -> FileLocker:
        locker = MagicMock(spec=FileLocker)
        locker.acquire = AsyncMock(return_value=True)
        locker.release = AsyncMock()
        return locker

    def _make_audit_store(self) -> AuditStore:
        store = MagicMock(spec=AuditStore)
        store.log_event = AsyncMock()
        return store

    def _make_baseline_store(self) -> BaselineStore:
        store = MagicMock(spec=BaselineStore)
        return store

    def test_build_with_baseline_store_compiles(self) -> None:
        from errander.config.settings import DriftSettings

        graph = build_vm_graph(
            _make_executor(),
            self._make_locker(),
            self._make_audit_store(),
            SSHConnectionManager(),
            baseline_store=self._make_baseline_store(),
            sre_drift_settings=DriftSettings(),
        )
        compiled = graph.compile()
        assert compiled is not None

    def test_build_with_failed_logins_compiles(self) -> None:
        graph = build_vm_graph(
            _make_executor(),
            self._make_locker(),
            self._make_audit_store(),
            SSHConnectionManager(),
            sre_failed_logins_settings=FailedSSHLoginsSettings(),
        )
        compiled = graph.compile()
        assert compiled is not None

    def test_build_with_all_sre_nodes_compiles(self) -> None:
        from errander.config.settings import DiskGrowthSettings, DriftSettings
        from errander.safety.disk_history import VMDiskHistoryStore

        disk_store = MagicMock(spec=VMDiskHistoryStore)
        graph = build_vm_graph(
            _make_executor(),
            self._make_locker(),
            self._make_audit_store(),
            SSHConnectionManager(),
            disk_history_store=disk_store,
            sre_disk_settings=DiskGrowthSettings(),
            baseline_store=self._make_baseline_store(),
            sre_drift_settings=DriftSettings(),
            sre_failed_logins_settings=FailedSSHLoginsSettings(),
        )
        compiled = graph.compile()
        assert compiled is not None

    def test_build_without_any_sre_nodes_still_compiles(self) -> None:
        graph = build_vm_graph(
            _make_executor(),
            self._make_locker(),
            self._make_audit_store(),
            SSHConnectionManager(),
        )
        compiled = graph.compile()
        assert compiled is not None
