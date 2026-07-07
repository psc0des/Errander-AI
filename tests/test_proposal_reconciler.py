"""Tests for the proposal reconciler (_proposal_reconciler) — fable-plan D1.

An approved actionable proposal is claimed atomically and executed through
the existing deterministic sub-graph; the drift/config/window gates refuse
or defer rather than silently proceed; agent dry-run mode never claims.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.config.schema import ActionConfig, EnvironmentSchema, TargetSchema
from errander.main import _proposal_reconciler
from errander.models.events import EventType
from errander.models.proposals import AgentProposal, ProposalKind
from errander.safety.proposal_store import ProposalStore
from tests.conftest import make_test_db


def _env_schema(*, disk_cleanup_enabled: bool = True) -> EnvironmentSchema:
    target = TargetSchema(
        host="10.0.1.1", name="web-01", os_family="ubuntu",
        actions={"disk_cleanup": ActionConfig(enabled=disk_cleanup_enabled)},
    )
    return EnvironmentSchema(
        targets=[target], ssh_user="errander", ssh_key_path="/tmp/key",
    )  # no maintenance window


def _proposal(**overrides: object) -> AgentProposal:
    defaults: dict[str, object] = {
        "env_name": "prod",
        "vm_id": "web-01",
        "kind": ProposalKind.ACTION,
        "action_type": "disk_cleanup",
        "signal_kind": "disk_growth",
    }
    defaults.update(overrides)
    return AgentProposal(**defaults)  # type: ignore[arg-type]


async def _make_store() -> ProposalStore:
    store = ProposalStore(make_test_db())
    await store.initialize()
    return store


def _locker(acquire: bool = True) -> MagicMock:
    locker = MagicMock()
    locker.acquire = AsyncMock(return_value=acquire)
    locker.release = AsyncMock()
    return locker


def _subgraph_mock(final: dict[str, Any]) -> MagicMock:
    builder = MagicMock()
    builder.return_value.compile.return_value.ainvoke = AsyncMock(return_value=final)
    return builder


async def _run(
    store: ProposalStore,
    *,
    environments: dict[str, EnvironmentSchema] | None = None,
    audit: AsyncMock | None = None,
    locker: MagicMock | None = None,
    agent_dry_run: bool = False,
) -> AsyncMock:
    audit = audit if audit is not None else AsyncMock()
    await _proposal_reconciler(
        environments=(
            environments if environments is not None else {"prod": _env_schema()}
        ),
        settings=MagicMock(),
        executor=MagicMock(),
        locker=locker if locker is not None else _locker(),
        audit_store=audit,
        proposal_store=store,
        agent_dry_run=agent_dry_run,
    )
    return audit


def _events(audit: AsyncMock) -> list[EventType]:
    return [c.args[0].event_type for c in audit.log_event.await_args_list]


class TestExpiryAndWake:
    @pytest.mark.asyncio
    async def test_expires_overdue_and_audits(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal(), expiry_days=0)
        audit = await _run(store)
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.status.value == "expired"
        assert EventType.PROPOSAL_EXPIRED in _events(audit)


class TestExecutionGates:
    @pytest.mark.asyncio
    async def test_dry_run_agent_never_claims(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        await _run(store, agent_dry_run=True)
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.execution_started_at is None

    @pytest.mark.asyncio
    async def test_unknown_vm_fails_closed(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal(vm_id="ghost-vm"))
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        audit = await _run(store)
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.execution_started_at is not None  # claimed — stops looping
        assert loaded.execution_status == "failed"
        assert EventType.PROPOSAL_EXECUTION_FAILED in _events(audit)

    @pytest.mark.asyncio
    async def test_config_drift_gate_refuses_disabled_action(self) -> None:
        """Action disabled between approval and execution → refuse, audit."""
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        audit = await _run(
            store, environments={"prod": _env_schema(disk_cleanup_enabled=False)},
        )
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.execution_status == "failed"
        failed = [
            c.args[0] for c in audit.log_event.await_args_list
            if c.args[0].event_type == EventType.PROPOSAL_EXECUTION_FAILED
        ]
        assert failed and "no longer enabled" in failed[0].detail

    @pytest.mark.asyncio
    async def test_locked_vm_left_unclaimed_for_retry(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        await _run(store, locker=_locker(acquire=False))
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.execution_started_at is None


class TestExecution:
    @pytest.mark.asyncio
    async def test_happy_path_runs_subgraph_and_audits(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:alice")

        builder = _subgraph_mock({"status": "success"})
        locker = _locker()
        with patch(
            "errander.agent.subgraphs.disk_cleanup.build_disk_cleanup_subgraph",
            builder,
        ):
            audit = await _run(store, locker=locker)

        # The existing deterministic sub-graph was invoked for the target VM
        sub_state = builder.return_value.compile.return_value.ainvoke.await_args.args[0]
        assert sub_state["vm_id"] == "web-01"
        assert sub_state["hostname"] == "10.0.1.1"
        assert sub_state["dry_run"] is False

        loaded = await store.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.execution_started_at is not None
        assert loaded.execution_status == "success"
        events = _events(audit)
        assert EventType.PROPOSAL_EXECUTION_STARTED in events
        assert EventType.PROPOSAL_EXECUTION_COMPLETED in events
        locker.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subgraph_failure_recorded_and_lock_released(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:alice")

        builder = MagicMock()
        builder.return_value.compile.return_value.ainvoke = AsyncMock(
            side_effect=ConnectionError("ssh down"),
        )
        locker = _locker()
        with patch(
            "errander.agent.subgraphs.disk_cleanup.build_disk_cleanup_subgraph",
            builder,
        ):
            audit = await _run(store, locker=locker)

        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.execution_status == "failed"
        assert EventType.PROPOSAL_EXECUTION_FAILED in _events(audit)
        locker.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_executes_exactly_once(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:alice")

        builder = _subgraph_mock({"status": "success"})
        with patch(
            "errander.agent.subgraphs.disk_cleanup.build_disk_cleanup_subgraph",
            builder,
        ):
            await _run(store)
            await _run(store)  # second tick — already claimed

        assert builder.return_value.compile.return_value.ainvoke.await_count == 1
