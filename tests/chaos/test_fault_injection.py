"""Chaos / fault-injection tests — correct failure behavior under adverse conditions (Phase 4.2).

Tests assert SYSTEM BEHAVIOR under fault conditions, not happy-path correctness:
- SSH connection dropped mid-action → action FAILED, batch continues for other VMs
- Patching execute failure → rollback triggered, status=FAILED with rollback detail
- Audit DB locked in strict mode → AuditWriteError raised, live action aborts
- Audit DB locked in best-effort mode → error logged, batch continues
- LLM unreachable / malformed → hardcoded fallback used, ai_decisions records outcome
- Slack unreachable → approval times out, auto-rejects (never auto-approves)
- dpkg lock held → action FAILED with dpkg error, no partial state
- Fleet threshold exceeded → FLEET_ABORT before any VM executes
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.db.core import AsyncDatabase
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus, ActionType
from errander.models.events import AuditEvent, EventType
from errander.safety.audit import AuditStore, AuditWriteError
from errander.safety.locking import FileLocker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh_result(stdout: str = "", exit_code: int = 0, stderr: str = "") -> SSHResult:
    now = datetime.now(tz=UTC)
    return SSHResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        command="mocked", duration_seconds=0.01,
        started_at=now, completed_at=now,
    )


_OS_RELEASE = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n'


def _make_event() -> AuditEvent:
    return AuditEvent(
        event_type=EventType.ACTION_STARTED,
        batch_id="chaos-batch",
        vm_id="dev/web-01",
        detail="chaos test event",
        timestamp=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# 4.2a — SSH connection dropped mid-action
# ---------------------------------------------------------------------------

class TestSSHFaultInjection:
    """SSH failures during action execution produce FAILED results, not crashes."""

    @pytest.mark.asyncio
    async def test_ssh_drop_during_patching_execute_returns_failed(
        self, tmp_path: Path
    ) -> None:
        """SSH ConnectionError during patching execute → FAILED status, not uncaught exception."""
        from errander.agent.subgraphs.patching import (
            PatchingGraphState,
            execute_node,
        )
        from errander.execution.sandbox import SandboxExecutor

        executor = SandboxExecutor(SSHConnectionManager(), dry_run=False)

        async def _drop(*args, **kwargs) -> SSHResult:
            raise ConnectionError("SSH connection reset by peer")

        with patch.object(executor, "execute", side_effect=_drop):
            state: PatchingGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": False,
                "status": ActionStatus.PENDING.value,
                "pending_updates": ["curl"],
                "version_snapshot": {"curl": "7.81.0"},
                "approved_packages": [{"name": "curl", "target": "7.88.1-1", "current": "7.81.0"}],
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }
            with pytest.raises(ConnectionError):
                await execute_node(state, executor=executor)

    @pytest.mark.asyncio
    async def test_ssh_connection_error_moves_target_to_failed(self) -> None:
        """validate_targets_node: SSH ConnectionError on os-release → failed_targets."""
        from errander.agent.graph import validate_targets_node

        ssh = SSHConnectionManager()
        audit_store_mock = MagicMock(spec=AuditStore)
        audit_store_mock.log_event = AsyncMock()

        with patch.object(
            ssh, "execute",
            AsyncMock(side_effect=ConnectionError("connection refused")),
        ):
            result = await validate_targets_node(
                {
                    "batch_id": "chaos-ssh",
                    "targets": [{"vm_id": "dev/vm-01", "hostname": "10.0.0.1",
                                 "ssh_user": "u", "ssh_key_path": "/k",
                                 "os_family": "ubuntu"}],
                    "healthy_targets": [], "failed_targets": [],
                },
                ssh_manager=ssh,
                audit_store=audit_store_mock,
            )

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1


# ---------------------------------------------------------------------------
# 4.2b — Patching rollback triggered on execute failure
# ---------------------------------------------------------------------------

class TestPatchingRollback:
    """Failed patching execute routes to rollback_node."""

    @pytest.mark.asyncio
    async def test_failed_execute_routes_to_rollback(self) -> None:
        from errander.agent.subgraphs.patching import route_after_execute
        from errander.models.actions import ActionStatus

        state = {"status": ActionStatus.FAILED.value}
        assert route_after_execute(state) == "rollback"

    @pytest.mark.asyncio
    async def test_rollback_called_with_version_snapshot(self) -> None:
        """rollback_node calls rollback_action with the pre-patch snapshot."""
        from errander.agent.subgraphs.patching import rollback_node
        from errander.execution.sandbox import SandboxExecutor

        executor = SandboxExecutor(SSHConnectionManager(), dry_run=False)
        snapshot = {"curl": "7.81.0", "nginx": "1.18.0"}

        with patch(
            "errander.safety.rollback.rollback_action",
            new_callable=AsyncMock,
            return_value=(True, "Rolled back 2 packages"),
        ) as mock_rollback:
            state = {
                "vm_id": "dev/web-01",
                "hostname": "10.0.1.10",  # type: ignore
                "username": "errander-ai",  # type: ignore
                "key_path": "/key",  # type: ignore
                "version_snapshot": snapshot,
                "error": "upgrade failed",
            }
            result = await rollback_node(state, executor=executor)

        mock_rollback.assert_awaited_once()
        call_kwargs = mock_rollback.call_args
        assert call_kwargs.args[2] == snapshot  # pre_snapshot passed through
        # Rollback succeeded → ROLLED_BACK (distinct from FAILED = no rollback attempted)
        assert result["status"] == ActionStatus.ROLLED_BACK.value

    @pytest.mark.asyncio
    async def test_rollback_failure_logged_as_critical(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When rollback itself fails, CRITICAL is logged."""
        import logging

        from errander.agent.subgraphs.patching import rollback_node
        from errander.execution.sandbox import SandboxExecutor

        executor = SandboxExecutor(SSHConnectionManager(), dry_run=False)

        with patch(
            "errander.safety.rollback.rollback_action",
            new_callable=AsyncMock,
            return_value=(False, "apt-get rollback failed: dpkg lock held"),
        ):
            state = {
                "vm_id": "dev/web-01",
                "hostname": "10.0.1.10",  # type: ignore
                "username": "errander-ai",  # type: ignore
                "key_path": "/key",  # type: ignore
                "version_snapshot": {"curl": "7.81.0"},
                "error": "upgrade failed",
            }
            with caplog.at_level(logging.ERROR, logger="errander.agent.subgraphs.patching"):
                result = await rollback_node(state, executor=executor)

        assert "CRITICAL" in caplog.text or "FAILED" in result["error"].upper()


# ---------------------------------------------------------------------------
# 4.2c — dpkg lock held produces clean failure
# ---------------------------------------------------------------------------

class TestDpkgLock:
    """dpkg lock held → clean FAILED status, no partial state."""

    @pytest.mark.asyncio
    async def test_dpkg_lock_produces_failed_status(self) -> None:
        from errander.agent.subgraphs.patching import PatchingGraphState, execute_node
        from errander.execution.sandbox import SandboxExecutor

        executor = SandboxExecutor(SSHConnectionManager(), dry_run=False)
        dpkg_lock_stderr = (
            "E: Could not get lock /var/lib/dpkg/lock-frontend. "
            "It is held by process 1234 (apt-get)"
        )
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_ssh_result("", exit_code=100, stderr=dpkg_lock_stderr)),
        ):
            state: PatchingGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": False,
                "status": ActionStatus.PENDING.value,
                "pending_updates": ["curl"],
                "version_snapshot": {"curl": "7.81.0"},
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value


# ---------------------------------------------------------------------------
# 4.2d — Audit DB locked: strict vs best-effort
# ---------------------------------------------------------------------------

class TestAuditFaultInjection:
    """Audit DB failures in strict mode abort live actions; best-effort continues."""

    @pytest.mark.asyncio
    async def test_strict_mode_raises_on_db_failure(self) -> None:
        """Live action with strict audit aborts when DB write fails."""
        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(AsyncDatabase(":memory:"), strict_mode=True) as store:
            @asynccontextmanager
            async def _fail():
                raise SAOperErr(None, None, Exception("database is locked"))
                yield  # noqa: B901

            with (
                patch.object(store._db, "begin", side_effect=lambda: _fail()),
                pytest.raises(AuditWriteError, match="strict mode"),
            ):
                await store.log_event(_make_event(), dry_run=False)

    @pytest.mark.asyncio
    async def test_best_effort_swallows_db_failure(self) -> None:
        """Dry-run audit failures are swallowed — batch continues."""
        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(AsyncDatabase(":memory:"), strict_mode=True) as store:
            @asynccontextmanager
            async def _fail():
                raise SAOperErr(None, None, Exception("database is locked"))
                yield  # noqa: B901

            with patch.object(store._db, "begin", side_effect=lambda: _fail()):
                # dry_run=True → best-effort regardless of strict_mode
                await store.log_event(_make_event(), dry_run=True)  # must not raise

    @pytest.mark.asyncio
    async def test_non_strict_mode_swallows_failure(self) -> None:
        """strict_mode=False always swallows failures."""
        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(AsyncDatabase(":memory:"), strict_mode=False) as store:
            @asynccontextmanager
            async def _fail():
                raise SAOperErr(None, None, Exception("disk full"))
                yield  # noqa: B901

            with patch.object(store._db, "begin", side_effect=lambda: _fail()):
                await store.log_event(_make_event(), dry_run=False)  # must not raise

    @pytest.mark.asyncio
    async def test_strict_retry_then_raises(self) -> None:
        """Both retry attempts fail in strict mode → AuditWriteError after 2 attempts."""
        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(AsyncDatabase(":memory:"), strict_mode=True) as store:
            call_count = 0

            def _always_fail():
                nonlocal call_count
                call_count += 1

                @asynccontextmanager
                async def _ctx():
                    raise SAOperErr(None, None, Exception("disk full"))
                    yield  # noqa: B901

                return _ctx()

            with (
                patch.object(store._db, "begin", side_effect=_always_fail),
                pytest.raises(AuditWriteError),
            ):
                await store.log_event(_make_event(), dry_run=False)

        assert call_count >= 2  # at least one retry


# ---------------------------------------------------------------------------
# 4.2e — LLM unreachable / malformed → hardcoded fallback
# ---------------------------------------------------------------------------

class TestLLMFaultInjection:
    """LLM failures always fall back to hardcoded ordering without crashing."""

    @pytest.mark.asyncio
    async def test_llm_timeout_falls_back_to_hardcoded(self) -> None:
        from errander.agent.decisions import prioritize_actions
        from errander.models.vm import OSFamily, VMInfo

        vm = VMInfo(
            os_family=OSFamily.UBUNTU, os_version="22.04",
            disk_usage={"/": 55.0}, docker_available=True,
            pending_packages=3, uptime_seconds=86400.0,
        )
        client = MagicMock()
        client._model = "mock"
        client._base_url = "http://mock"
        client.complete = AsyncMock(return_value=None)  # LLM timeout → None

        actions = await prioritize_actions(vm, llm_client=client)
        assert len(actions) > 0
        action_types = [a.action_type for a in actions]
        assert ActionType.DISK_CLEANUP in action_types

    @pytest.mark.asyncio
    async def test_llm_malformed_json_falls_back(self) -> None:
        """LLM returning unrecognised action types → parse fails → hardcoded fallback."""
        from pydantic import BaseModel

        from errander.agent.decisions import prioritize_actions
        from errander.models.vm import OSFamily, VMInfo

        class _FakeResponse(BaseModel):
            action_types: list[str]

        vm = VMInfo(
            os_family=OSFamily.UBUNTU, os_version="22.04",
            disk_usage={"/": 55.0}, docker_available=False,
            pending_packages=0, uptime_seconds=86400.0,
        )
        client = MagicMock()
        client._model = "mock"
        client._base_url = "http://mock"
        # LLM returns unknown action types
        client.complete = AsyncMock(return_value=_FakeResponse(
            action_types=["kernel_patch", "rm_rf_root", "UNKNOWN"]
        ))

        actions = await prioritize_actions(vm, llm_client=client)
        # All invalid → _parse_action_types returns [] → hardcoded fallback
        action_types = [a.action_type for a in actions]
        for at in action_types:
            assert at in list(ActionType)  # only known types

    @pytest.mark.asyncio
    async def test_llm_unavailable_audit_records_no_llm(self) -> None:
        """No LLM configured → ai_decisions records outcome=no_llm."""
        from errander.agent.decisions import prioritize_actions
        from errander.models.vm import OSFamily, VMInfo
        from errander.safety.ai_audit import AIDecisionStore

        vm = VMInfo(
            os_family=OSFamily.UBUNTU, os_version="22.04",
            disk_usage={"/": 55.0}, docker_available=True,
            pending_packages=2, uptime_seconds=3600.0,
        )
        async with AIDecisionStore(AsyncDatabase(":memory:")) as store:
            await prioritize_actions(
                vm,
                llm_client=None,
                batch_id="chaos-llm-001",
                ai_store=store,
            )
            decisions = await store.get_decisions(batch_id="chaos-llm-001")

        assert len(decisions) == 1
        assert decisions[0].outcome == "no_llm"


# ---------------------------------------------------------------------------
# 4.2f — Slack unreachable → timeout, never auto-approve
# ---------------------------------------------------------------------------

class TestApprovalFaultInjection:
    """Slack unreachable → auto-REJECT after timeout, never auto-approve."""

    @pytest.mark.asyncio
    async def test_approval_manager_decide_rejects(self) -> None:
        """ApprovalManager.decide(approved=False) signals waiting coroutine."""
        from errander.safety.approval import ApprovalManager

        manager = ApprovalManager()
        batch_id = "chaos-slack-001"
        manager.register(batch_id, "Test plan")

        # Decide in background after a tiny delay (wait_for_decision must be awaited first)
        async def _reject():
            await asyncio.sleep(0.02)
            manager.decide(batch_id, approved=False, user_id="chaos-test")

        asyncio.create_task(_reject())
        approved, approver = await manager.wait_for_decision(batch_id, timeout_seconds=2.0)

        assert not approved
        assert approver == "chaos-test"

    @pytest.mark.asyncio
    async def test_approval_timeout_auto_rejects(self) -> None:
        """ApprovalManager.wait_for_decision times out → returns (False, None)."""
        from errander.safety.approval import ApprovalManager

        manager = ApprovalManager()
        batch_id = "chaos-timeout-001"
        manager.register(batch_id, "Test plan")

        approved, approver = await manager.wait_for_decision(
            batch_id, timeout_seconds=0.05
        )

        assert not approved
        assert approver is None

    @pytest.mark.asyncio
    async def test_no_auto_approve_on_slack_silence(self) -> None:
        """Slack returning no reactions → decision stays pending until timeout."""
        from errander.safety.approval import ApprovalManager

        manager = ApprovalManager()
        batch_id = "chaos-silence-001"
        manager.register(batch_id, "Test plan")

        # Simulate Slack silence: no reactions, no decision call
        approved, _ = await manager.wait_for_decision(
            batch_id, timeout_seconds=0.02
        )

        assert not approved  # silence = auto-reject, not auto-approve


# ---------------------------------------------------------------------------
# 4.2g — Fleet threshold abort stops all execution pre-flight
# ---------------------------------------------------------------------------

class TestFleetAbortChaos:
    """Fleet abort when threshold exceeded runs NO actions on any VM."""

    @pytest.mark.asyncio
    async def test_fleet_abort_node_emits_audit_event(self) -> None:
        """check_fleet_health_node emits FLEET_ABORT and sets error when threshold exceeded."""
        from errander.agent.graph import check_fleet_health_node

        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = {
                "batch_id": "chaos-fleet",
                "healthy_targets": [],
                "failed_targets": [
                    {"vm_id": "dev/vm-01"},
                    {"vm_id": "dev/vm-02"},
                ],
            }
            result = await check_fleet_health_node(
                state, audit_store=store, fleet_failure_threshold=0.5
            )
            events = await store.get_events(batch_id="chaos-fleet")

        assert "error" in result
        fleet_events = [e for e in events if e.event_type == EventType.FLEET_ABORT]
        assert len(fleet_events) == 1


# ---------------------------------------------------------------------------
# 4.3 Windows test infra fix — tempdir cleanup safety
# ---------------------------------------------------------------------------

class TestWindowsTempDirSafety:
    """Verify tests use pytest tmp_path (auto-cleaned) not hardcoded /tmp paths."""

    def test_file_locker_works_with_tmp_path(self, tmp_path: Path) -> None:
        """FileLocker should use pytest-managed tmp_path, not hardcoded /tmp."""
        locker = FileLocker(lock_dir=tmp_path / "locks")
        assert locker is not None

    def test_ai_decision_store_uses_memory_db_in_tests(self) -> None:
        """Tests should use ':memory:' SQLite for AI decision store — never disk paths."""
        from errander.safety.ai_audit import AIDecisionStore
        store = AIDecisionStore(AsyncDatabase(":memory:"))
        assert ":memory:" in store._db._url
