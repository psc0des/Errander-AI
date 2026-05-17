"""Tests for service_restart sub-graph nodes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.subgraphs.service_restart import (
    execute_node,
    snapshot_node,
    validate_node,
    verify_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus
from errander.models.events import EventType


def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


def _base_state(**overrides: object) -> dict:  # type: ignore[type-arg]
    state = {
        "vm_id": "vm-web-01",
        "batch_id": "batch-001",
        "dry_run": False,
        "hostname": "10.0.0.1",
        "username": "errander",
        "key_path": "/home/errander/.ssh/id_ed25519",
        "unit_name": "nginx",
        "restartable_units": ["nginx", "gunicorn"],
    }
    state.update(overrides)
    return state


_FULL_WRAPPER_OUTPUT = (
    "pre_status_begin\n"
    "● nginx.service - nginx\n"
    "   Active: active (running)\n"
    "pre_status_end\n"
    "pre_journal_begin\n"
    "May 17 10:00:00 host nginx[1]: started\n"
    "pre_journal_end\n"
    "post_active_begin\n"
    "active\n"
    "post_active_end\n"
    "post_status_begin\n"
    "● nginx.service - nginx\n"
    "   Active: active (running)\n"
    "post_status_end\n"
    "post_journal_begin\n"
    "May 17 10:00:05 host nginx[2]: reloaded\n"
    "post_journal_end\n"
)

_SNAPSHOT_OUTPUT = (
    "pre_status_begin\n"
    "● nginx.service\n"
    "   Active: active (running)\n"
    "pre_status_end\n"
    "pre_journal_begin\n"
    "May 17 09:00:00 host nginx[1]: started\n"
    "pre_journal_end\n"
)


# --- validate_node ---

class TestValidateNode:
    @pytest.mark.asyncio
    async def test_passes_when_unit_in_allowlist_and_wrapper_ok(self) -> None:
        executor = _make_executor()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(return_value=_make_result("ok")))
            result = await validate_node(_base_state(), executor=executor)
        assert result["status"] == ActionStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_fails_when_unit_not_in_allowlist(self) -> None:
        executor = _make_executor()
        state = _base_state(unit_name="unknown-service")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(return_value=_make_result("ok")))
            result = await validate_node(state, executor=executor)
        assert result["status"] == ActionStatus.FAILED.value
        assert "unknown-service" in result["error"]

    @pytest.mark.asyncio
    async def test_fails_when_unit_not_in_allowlist_emits_audit_event(self) -> None:
        executor = _make_executor()
        audit_store = MagicMock()
        audit_store.log_event = AsyncMock()
        state = _base_state(unit_name="not-allowed")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(return_value=_make_result("ok")))
            await validate_node(state, executor=executor, audit_store=audit_store, batch_id="b1")

        audit_store.log_event.assert_awaited_once()
        event = audit_store.log_event.call_args[0][0]
        assert event.event_type == EventType.SERVICE_RESTART_UNIT_NOT_ALLOWED
        assert event.batch_id == "b1"

    @pytest.mark.asyncio
    async def test_fails_when_wrapper_check_fails(self) -> None:
        executor = _make_executor()
        state = _base_state()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(
                return_value=_make_result("", exit_code=1)
            ))
            result = await validate_node(state, executor=executor)
        assert result["status"] == ActionStatus.FAILED.value
        assert "Wrapper" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_restartable_units_fails(self) -> None:
        executor = _make_executor()
        state = _base_state(restartable_units=[])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(return_value=_make_result("ok")))
            result = await validate_node(state, executor=executor)
        assert result["status"] == ActionStatus.FAILED.value


# --- snapshot_node ---

class TestSnapshotNode:
    @pytest.mark.asyncio
    async def test_captures_pre_status_and_journal(self) -> None:
        executor = _make_executor()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(return_value=_make_result(_SNAPSHOT_OUTPUT)))
            result = await snapshot_node(_base_state(), executor=executor)
        assert "nginx.service" in result["pre_status"]
        assert "started" in result["pre_journal"]

    @pytest.mark.asyncio
    async def test_returns_empty_strings_on_ssh_failure(self) -> None:
        executor = _make_executor()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(
                return_value=_make_result("", exit_code=1)
            ))
            result = await snapshot_node(_base_state(), executor=executor)
        assert result["pre_status"] == ""
        assert result["pre_journal"] == ""

    @pytest.mark.asyncio
    async def test_uses_snapshot_only_flag(self) -> None:
        executor = _make_executor()
        calls: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(str(kwargs.get("command", "")))
            return _make_result(_SNAPSHOT_OUTPUT)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", capture)
            await snapshot_node(_base_state(), executor=executor)

        assert any("--snapshot-only" in c for c in calls)
        assert any("nginx" in c for c in calls)


# --- execute_node ---

class TestExecuteNode:
    @pytest.mark.asyncio
    async def test_dry_run_returns_dry_run_ok(self) -> None:
        executor = _make_executor()
        state = _base_state(dry_run=True)
        result = await execute_node(state, executor=executor)
        assert result["status"] == ActionStatus.DRY_RUN_OK.value

    @pytest.mark.asyncio
    async def test_dry_run_does_not_ssh(self) -> None:
        executor = _make_executor()
        executor.execute = AsyncMock()  # type: ignore[method-assign]
        state = _base_state(dry_run=True)
        await execute_node(state, executor=executor)
        executor.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_run_parses_full_output(self) -> None:
        executor = _make_executor()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(
                return_value=_make_result(_FULL_WRAPPER_OUTPUT)
            ))
            result = await execute_node(_base_state(), executor=executor)
        assert result["status"] == ActionStatus.SUCCESS.value
        assert "active" in result["post_active"]
        assert "nginx.service" in result["pre_status"]

    @pytest.mark.asyncio
    async def test_wrapper_failure_returns_failed_status(self) -> None:
        executor = _make_executor()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(executor, "execute", AsyncMock(
                return_value=_make_result("", exit_code=4)
            ))
            result = await execute_node(_base_state(), executor=executor)
        assert result["status"] == ActionStatus.FAILED.value


# --- verify_node ---

class TestVerifyNode:
    @pytest.mark.asyncio
    async def test_dry_run_skips_verify(self) -> None:
        executor = _make_executor()
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        result = await verify_node(state, executor=executor)
        assert result == {}

    @pytest.mark.asyncio
    async def test_active_post_state_succeeds(self) -> None:
        executor = _make_executor()
        state = _base_state(
            status=ActionStatus.SUCCESS.value,
            unit_name="nginx",
            post_active="active",
        )
        result = await verify_node(state, executor=executor)
        assert "status" not in result or result.get("status") != ActionStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_inactive_post_state_returns_failed(self) -> None:
        executor = _make_executor()
        state = _base_state(
            status=ActionStatus.SUCCESS.value,
            unit_name="nginx",
            post_active="inactive",
        )
        result = await verify_node(state, executor=executor)
        assert result["status"] == ActionStatus.FAILED.value
        assert "nginx" in result["error"]

    @pytest.mark.asyncio
    async def test_inactive_emits_verify_failed_event(self) -> None:
        executor = _make_executor()
        audit_store = MagicMock()
        audit_store.log_event = AsyncMock()
        state = _base_state(
            status=ActionStatus.SUCCESS.value,
            unit_name="nginx",
            post_active="inactive",
        )
        await verify_node(state, executor=executor, audit_store=audit_store, batch_id="b1")
        audit_store.log_event.assert_awaited_once()
        event = audit_store.log_event.call_args[0][0]
        assert event.event_type == EventType.SERVICE_RESTART_VERIFY_FAILED

    @pytest.mark.asyncio
    async def test_active_emits_verify_ok_event(self) -> None:
        executor = _make_executor()
        audit_store = MagicMock()
        audit_store.log_event = AsyncMock()
        state = _base_state(
            status=ActionStatus.SUCCESS.value,
            unit_name="nginx",
            post_active="active",
        )
        await verify_node(state, executor=executor, audit_store=audit_store, batch_id="b1")
        audit_store.log_event.assert_awaited_once()
        event = audit_store.log_event.call_args[0][0]
        assert event.event_type == EventType.SERVICE_RESTART_VERIFY_OK

    @pytest.mark.asyncio
    async def test_empty_post_active_is_failed(self) -> None:
        executor = _make_executor()
        state = _base_state(
            status=ActionStatus.SUCCESS.value,
            unit_name="redis",
            post_active="",
        )
        result = await verify_node(state, executor=executor)
        assert result["status"] == ActionStatus.FAILED.value
