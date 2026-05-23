"""P0-2 Commit 2 tests: exact deferred artifact replay in batch graph."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    approval_gate_node,
    load_deferred_artifact_node,
    route_after_fleet_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VM_PLANS = [
    {
        "vm_id": "prod/web-01",
        "os_family": "ubuntu",
        "planned_actions": [
            {"action_type": "patching", "risk_tier": "medium", "preview": {"package_count": 3}},
        ],
    }
]


def _make_hash(batch_id: str, env_name: str, vm_plans: list) -> str:
    canonical = json.dumps(
        {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _make_artifact_state(
    batch_id: str = "b1",
    env_name: str = "prod",
    vm_plans: list | None = None,
    tamper: bool = False,
    approved_at: datetime | None = None,
) -> dict:
    plans = vm_plans or _VM_PLANS
    plan_json = json.dumps({"plan_id": "pid-1", "vm_plans": plans})
    plan_hash = _make_hash(batch_id, env_name, plans)
    if tamper:
        plan_hash = "0" * 64  # wrong hash
    _approved_at = approved_at or (datetime.now(tz=UTC) - timedelta(hours=1))
    return {
        "batch_id": batch_id,
        "env_name": env_name,
        "preloaded_plan_json": plan_json,
        "preloaded_plan_hash": plan_hash,
        "preloaded_plan_id": "pid-1",
        "preloaded_approved_at": _approved_at.isoformat(),
        "is_deferred_replay": True,
    }


# ---------------------------------------------------------------------------
# load_deferred_artifact_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_artifact_node_valid() -> None:
    state = _make_artifact_state()
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert result.get("error") is None
    assert result["is_deferred_replay"] is True
    assert result["plan_hash"] == _make_hash("b1", "prod", _VM_PLANS)
    assert len(result["enriched_vm_plans"]) == 1
    assert result["enriched_vm_plans"][0]["vm_id"] == "prod/web-01"


@pytest.mark.asyncio
async def test_load_artifact_node_hash_mismatch() -> None:
    state = _make_artifact_state(tamper=True)
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "hash mismatch" in result["error"]


@pytest.mark.asyncio
async def test_load_artifact_node_invalid_json() -> None:
    state: dict = {
        "batch_id": "b1",
        "env_name": "prod",
        "preloaded_plan_json": "{not valid json",
        "preloaded_plan_hash": "a" * 64,
        "preloaded_approved_at": (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat(),
        "is_deferred_replay": True,
    }
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "corrupt" in result["error"]


@pytest.mark.asyncio
async def test_load_artifact_node_missing_json() -> None:
    state: dict = {
        "batch_id": "b1",
        "env_name": "prod",
        "preloaded_plan_json": "",
        "preloaded_plan_hash": "a" * 64,
        "preloaded_approved_at": (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat(),
        "is_deferred_replay": True,
    }
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "missing" in result["error"]


@pytest.mark.asyncio
async def test_load_artifact_node_within_age_limit() -> None:
    """Artifact approved 1h ago passes the age check."""
    state = _make_artifact_state()  # defaults to 1h ago
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert result.get("error") is None
    assert result["is_deferred_replay"] is True


@pytest.mark.asyncio
async def test_load_artifact_node_exceeds_age_limit() -> None:
    """Artifact approved beyond _DEFERRED_MAX_ARTIFACT_AGE_HOURS fails closed."""
    from errander.agent.graph import _DEFERRED_MAX_ARTIFACT_AGE_HOURS

    state = _make_artifact_state()
    too_old = datetime.now(tz=UTC) - timedelta(hours=_DEFERRED_MAX_ARTIFACT_AGE_HOURS + 1)
    state["preloaded_approved_at"] = too_old.isoformat()
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "re-approval required" in result["error"]


@pytest.mark.asyncio
async def test_load_artifact_node_missing_approved_at_fails_closed() -> None:
    """Artifact replay without a valid approval timestamp must fail closed."""
    state = _make_artifact_state()
    state["preloaded_approved_at"] = None
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "timestamp" in result["error"] or "approved_at" in result["error"]


@pytest.mark.asyncio
async def test_load_artifact_node_invalid_approved_at_fails_closed() -> None:
    """Unparseable approval timestamp must fail closed."""
    state = _make_artifact_state()
    state["preloaded_approved_at"] = "not-a-timestamp"
    result = await load_deferred_artifact_node(state)  # type: ignore[arg-type]

    assert "error" in result
    assert "re-approval required" in result["error"]


# ---------------------------------------------------------------------------
# approval_gate_node in replay mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_gate_replay_mode_auto_approves() -> None:
    """is_deferred_replay=True → returns approved=True without calling approval_manager."""
    approval_manager = AsyncMock()

    state: dict = {
        "batch_id": "b1",
        "env_name": "prod",
        "env_policy": "strict",
        "plan_id": "pid-1",
        "plan_hash": "a" * 64,
        "dry_run": False,
        "vm_plans": _VM_PLANS,
        "is_deferred_replay": True,
        "is_deferred_reapproval": False,
    }

    result = await approval_gate_node(state, approval_manager=approval_manager)

    assert result["approved"] is True
    approval_manager.await_approval.assert_not_called()


@pytest.mark.asyncio
async def test_approval_gate_replay_mode_logs_audit_event() -> None:
    from errander.models.events import AuditEvent, EventType
    from errander.safety.audit import AuditStore

    logged: list[AuditEvent] = []
    audit_store = AsyncMock(spec=AuditStore)
    audit_store.log_event = AsyncMock(side_effect=logged.append)

    state: dict = {
        "batch_id": "b2",
        "env_name": "dev",
        "env_policy": "strict",
        "plan_id": "pid-2",
        "plan_hash": "b" * 64,
        "dry_run": False,
        "vm_plans": _VM_PLANS,
        "is_deferred_replay": True,
        "is_deferred_reapproval": False,
    }

    await approval_gate_node(state, audit_store=audit_store)

    approval_events = [e for e in logged if e.event_type == EventType.APPROVAL_GRANTED]
    assert approval_events, "APPROVAL_GRANTED not logged in replay mode"
    evt = approval_events[0]
    assert evt.metadata.get("replay_mode") is True
    assert "replay" in evt.detail.lower()


@pytest.mark.asyncio
async def test_approval_gate_replay_mode_no_slack_post() -> None:
    """Replay mode must not post to Slack (no re-approval message)."""
    from errander.integrations.slack import SlackClient

    slack_client = AsyncMock(spec=SlackClient)

    state: dict = {
        "batch_id": "b3",
        "env_name": "dev",
        "env_policy": "strict",
        "plan_id": "pid-3",
        "plan_hash": "c" * 64,
        "dry_run": False,
        "vm_plans": _VM_PLANS,
        "is_deferred_replay": True,
        "is_deferred_reapproval": False,
    }

    await approval_gate_node(state, slack_client=slack_client)

    # In replay mode no Slack message should be sent at approval time
    slack_client.post_message.assert_not_called()


# ---------------------------------------------------------------------------
# route_after_fleet_check
# ---------------------------------------------------------------------------


def test_route_after_fleet_check_normal() -> None:
    state: dict = {
        "healthy_targets": [{"vm_id": "vm1"}],
        "preloaded_plan_json": None,
    }
    assert route_after_fleet_check(state) == "plan_vms"  # type: ignore[arg-type]


def test_route_after_fleet_check_replay() -> None:
    state: dict = {
        "healthy_targets": [{"vm_id": "vm1"}],
        "preloaded_plan_json": '{"plan_id":"p1","vm_plans":[]}',
    }
    assert route_after_fleet_check(state) == "load_deferred_artifact"  # type: ignore[arg-type]


def test_route_after_fleet_check_error() -> None:
    state: dict = {
        "error": "ssh failed",
        "healthy_targets": [],
    }
    assert route_after_fleet_check(state) == "generate_report"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _window_opener integration: uses stored artifact / falls back to re-plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_opener_uses_stored_artifact() -> None:
    """_window_opener must call run_env_batch with preloaded_plan_json when record has artifact."""
    from errander.safety.deferred import DeferredExecution, DeferredExecutionStore

    plan_json = json.dumps({"plan_id": "p1", "vm_plans": _VM_PLANS})
    plan_hash = _make_hash("batch-deferred", "dev", _VM_PLANS)

    record = DeferredExecution(
        batch_id="batch-deferred",
        env_name="dev",
        approved_at=datetime.now(tz=UTC),
        approved_by="alice",
        window_start=datetime.now(tz=UTC),
        expiry_at=datetime.now(tz=UTC) + timedelta(days=7),
        status="pending",
        created_at=datetime.now(tz=UTC),
        executed_at=None,
        plan_json=plan_json,
        plan_hash=plan_hash,
    )

    deferred_store = AsyncMock(spec=DeferredExecutionStore)
    deferred_store.get_pending = AsyncMock(return_value=[record])
    deferred_store.expire_old = AsyncMock()
    deferred_store.mark_executing = AsyncMock()
    deferred_store.mark_done = AsyncMock()

    from errander.models.events import AuditEvent
    from errander.safety.audit import AuditStore

    audit_store = AsyncMock(spec=AuditStore)
    audit_store.log_event = AsyncMock()

    run_batch_calls: list[dict] = []

    async def _mock_run_batch(**kwargs: object) -> None:
        run_batch_calls.append(dict(kwargs))

    with patch("errander.main.run_env_batch", side_effect=_mock_run_batch):
        from errander.main import _window_opener

        await _window_opener(
            env_name="dev",
            env_schema=MagicMock(targets=[]),
            settings=MagicMock(),
            executor=MagicMock(),
            locker=MagicMock(),
            ssh_manager=MagicMock(),
            audit_store=audit_store,
            deferred_store=deferred_store,
            approval_manager=MagicMock(),
            slack_client=None,
            overrides_store=MagicMock(),
        )

    assert len(run_batch_calls) == 1
    call = run_batch_calls[0]
    assert call["preloaded_plan_json"] == plan_json
    assert call["preloaded_plan_hash"] == plan_hash
    # approved_at must be passed so load_deferred_artifact_node can check artifact age
    assert call.get("preloaded_approved_at") is not None
    # No re-approval flag set
    assert not call.get("is_deferred_reapproval", False)


@pytest.mark.asyncio
async def test_window_opener_legacy_fallback() -> None:
    """Records without plan_json must fall back to is_deferred_reapproval=True."""
    from errander.safety.deferred import DeferredExecution, DeferredExecutionStore

    record = DeferredExecution(
        batch_id="batch-legacy",
        env_name="dev",
        approved_at=datetime.now(tz=UTC),
        approved_by="bob",
        window_start=datetime.now(tz=UTC),
        expiry_at=datetime.now(tz=UTC) + timedelta(days=7),
        status="pending",
        created_at=datetime.now(tz=UTC),
        executed_at=None,
        plan_json=None,  # legacy — no artifact
        plan_hash=None,
    )

    deferred_store = AsyncMock(spec=DeferredExecutionStore)
    deferred_store.get_pending = AsyncMock(return_value=[record])
    deferred_store.expire_old = AsyncMock()
    deferred_store.mark_executing = AsyncMock()
    deferred_store.mark_done = AsyncMock()

    from errander.safety.audit import AuditStore

    audit_store = AsyncMock(spec=AuditStore)
    audit_store.log_event = AsyncMock()

    run_batch_calls: list[dict] = []

    async def _mock_run_batch(**kwargs: object) -> None:
        run_batch_calls.append(dict(kwargs))

    with patch("errander.main.run_env_batch", side_effect=_mock_run_batch):
        from errander.main import _window_opener

        await _window_opener(
            env_name="dev",
            env_schema=MagicMock(targets=[]),
            settings=MagicMock(),
            executor=MagicMock(),
            locker=MagicMock(),
            ssh_manager=MagicMock(),
            audit_store=audit_store,
            deferred_store=deferred_store,
            approval_manager=MagicMock(),
            slack_client=None,
            overrides_store=MagicMock(),
        )

    assert len(run_batch_calls) == 1
    call = run_batch_calls[0]
    assert call.get("is_deferred_reapproval") is True
    assert call.get("preloaded_plan_json") is None


@pytest.mark.asyncio
async def test_window_opener_passes_hygiene_manager() -> None:
    """_window_opener must forward hygiene_manager to run_env_batch.

    Without this, deferred docker_hygiene batches see hygiene_manager=None
    and silently skip object-level approval — a silent correctness failure.
    """
    from errander.safety.deferred import DeferredExecution, DeferredExecutionStore
    from errander.safety.hygiene_approval import HygieneApprovalManager

    plan_json = json.dumps({"plan_id": "p2", "vm_plans": _VM_PLANS})
    plan_hash = _make_hash("batch-hygiene", "dev", _VM_PLANS)

    record = DeferredExecution(
        batch_id="batch-hygiene",
        env_name="dev",
        approved_at=datetime.now(tz=UTC),
        approved_by="alice",
        window_start=datetime.now(tz=UTC),
        expiry_at=datetime.now(tz=UTC) + timedelta(days=7),
        status="pending",
        created_at=datetime.now(tz=UTC),
        executed_at=None,
        plan_json=plan_json,
        plan_hash=plan_hash,
    )

    deferred_store = AsyncMock(spec=DeferredExecutionStore)
    deferred_store.get_pending = AsyncMock(return_value=[record])
    deferred_store.expire_old = AsyncMock()
    deferred_store.mark_executing = AsyncMock()
    deferred_store.mark_done = AsyncMock()

    from errander.safety.audit import AuditStore

    audit_store = AsyncMock(spec=AuditStore)
    audit_store.log_event = AsyncMock()

    hygiene_manager = HygieneApprovalManager()
    run_batch_calls: list[dict] = []

    async def _mock_run_batch(**kwargs: object) -> None:
        run_batch_calls.append(dict(kwargs))

    with patch("errander.main.run_env_batch", side_effect=_mock_run_batch):
        from errander.main import _window_opener

        await _window_opener(
            env_name="dev",
            env_schema=MagicMock(targets=[]),
            settings=MagicMock(),
            executor=MagicMock(),
            locker=MagicMock(),
            ssh_manager=MagicMock(),
            audit_store=audit_store,
            deferred_store=deferred_store,
            approval_manager=MagicMock(),
            slack_client=None,
            overrides_store=MagicMock(),
            hygiene_manager=hygiene_manager,
        )

    assert len(run_batch_calls) == 1
    assert run_batch_calls[0]["hygiene_manager"] is hygiene_manager
