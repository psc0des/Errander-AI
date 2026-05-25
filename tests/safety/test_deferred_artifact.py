"""P0-2 Commit 1 tests: plan artifact persistence in DeferredExecutionStore."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.safety.deferred import DeferredExecution, DeferredExecutionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_window() -> datetime:
    return datetime.now(tz=UTC) + timedelta(days=1)


async def _make_store(tmp_path: Path) -> DeferredExecutionStore:
    store = DeferredExecutionStore(str(tmp_path / "test.sqlite"))
    await store.initialize()
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_with_artifact(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    try:
        plan_json = json.dumps({"plan_id": "p1", "vm_plans": [{"vm_id": "vm1"}]})
        plan_hash = "a" * 64
        window = _future_window()
        await store.save("batch-1", "dev", "alice", window, plan_json=plan_json, plan_hash=plan_hash)

        pending = await store.get_pending("dev")
        assert len(pending) == 1
        rec = pending[0]
        assert rec.plan_json == plan_json
        assert rec.plan_hash == plan_hash
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_save_without_artifact(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    try:
        window = _future_window()
        await store.save("batch-2", "dev", "bob", window)

        pending = await store.get_pending("dev")
        assert len(pending) == 1
        rec = pending[0]
        assert rec.plan_json is None
        assert rec.plan_hash is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_round_trip_vm_plans(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    try:
        vm_plans = [
            {
                "vm_id": "prod/web-01",
                "os_family": "ubuntu",
                "planned_actions": [
                    {
                        "action_type": "patching",
                        "preview": {
                            "packages": [
                                {"name": "nginx", "current": "1.18.0", "target": "1.24.0"}
                            ],
                            "package_count": 1,
                        },
                    }
                ],
            }
        ]
        plan_json = json.dumps({"plan_id": "pid-abc", "vm_plans": vm_plans})
        plan_hash = "b" * 64
        window = _future_window()
        await store.save("batch-3", "prod", "carol", window, plan_json=plan_json, plan_hash=plan_hash)

        pending = await store.get_pending("prod")
        rec = pending[0]
        assert rec.plan_json is not None
        artifact = json.loads(rec.plan_json)
        assert artifact["plan_id"] == "pid-abc"
        assert artifact["vm_plans"][0]["vm_id"] == "prod/web-01"
        pkgs = artifact["vm_plans"][0]["planned_actions"][0]["preview"]["packages"]
        assert pkgs[0]["name"] == "nginx"
        assert pkgs[0]["current"] == "1.18.0"
        assert pkgs[0]["target"] == "1.24.0"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_migration_adds_columns(tmp_path: Path) -> None:
    """initialize() must add plan_json/plan_hash to tables that predate P0-2."""
    import aiosqlite

    db_path = str(tmp_path / "legacy.sqlite")
    # Create legacy table without the new columns
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE deferred_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                env_name TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                approved_by TEXT,
                window_start TEXT NOT NULL,
                expiry_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                executed_at TEXT
            )
        """)
        await db.commit()

    # Now initialise store over the legacy DB — migration should succeed
    store = DeferredExecutionStore(db_path)
    await store.initialize()
    try:
        # Insert using the new save() — must not fail due to missing columns
        window = _future_window()
        await store.save("batch-mig", "dev", None, window, plan_json='{"v":1}', plan_hash="c" * 64)
        pending = await store.get_pending("dev")
        assert pending[0].plan_json == '{"v":1}'
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_approval_gate_defers_with_artifact(tmp_path: Path) -> None:
    """approval_gate_node must call deferred_store.save() with plan_json when deferring."""

    from errander.agent.graph import approval_gate_node
    from errander.scheduling.windows import MaintenanceWindow

    deferred_store = AsyncMock()

    # Build a minimal state that triggers deferral:
    # approved live run but outside window → deferred_store.save() called
    window = MagicMock(spec=MaintenanceWindow)

    from errander.safety.approval import ApprovalManager

    approval_manager = AsyncMock(spec=ApprovalManager)
    approval_manager.await_approval = AsyncMock(return_value=(True, "alice", None))

    vm_plans = [{"vm_id": "vm1", "planned_actions": [{"action_type": "patching", "risk_tier": "medium"}]}]

    state: dict = {
        "batch_id": "b1",
        "env_name": "prod",
        "env_policy": "strict",
        "plan_id": "pid-1",
        "plan_hash": "d" * 64,
        "dry_run": False,
        "vm_plans": vm_plans,
        "is_deferred_reapproval": False,
        "is_deferred_replay": False,
    }

    # Patch window functions so the run appears to be outside window
    with (
        patch("errander.agent.graph.check_window_from_config", return_value=False),
        patch(
            "errander.agent.graph.next_window_open",
            return_value=datetime.now(tz=UTC) + timedelta(hours=2),
        ),
        patch("errander.agent.graph.await_dual_approval", new=AsyncMock(return_value=(True, "alice", None))),
    ):
        await approval_gate_node(
            state,
            approval_manager=approval_manager,
            deferred_store=deferred_store,
            window=window,
            require_live_approval=True,
        )

    deferred_store.save.assert_awaited_once()
    call_kwargs = deferred_store.save.call_args
    assert call_kwargs.kwargs.get("plan_json") is not None or (
        len(call_kwargs.args) > 4 and call_kwargs.args[4] is not None
    )


@pytest.mark.asyncio
async def test_approval_gate_deferred_audit_event(tmp_path: Path) -> None:
    """EXECUTION_DEFERRED audit event should include plan_hash and artifact_saved=True."""

    from errander.agent.graph import approval_gate_node
    from errander.models.events import AuditEvent, EventType
    from errander.safety.approval import ApprovalManager
    from errander.safety.audit import AuditStore
    from errander.scheduling.windows import MaintenanceWindow

    logged_events: list[AuditEvent] = []

    audit_store = AsyncMock(spec=AuditStore)
    audit_store.log_event = AsyncMock(side_effect=logged_events.append)

    deferred_store = AsyncMock()
    approval_manager = AsyncMock(spec=ApprovalManager)
    window = MagicMock(spec=MaintenanceWindow)

    state: dict = {
        "batch_id": "b2",
        "env_name": "dev",
        "env_policy": "strict",
        "plan_id": "pid-2",
        "plan_hash": "e" * 64,
        "dry_run": False,
        "vm_plans": [{"vm_id": "vm1", "planned_actions": [{"action_type": "patching", "risk_tier": "medium"}]}],
        "is_deferred_reapproval": False,
        "is_deferred_replay": False,
    }

    with (
        patch("errander.agent.graph.check_window_from_config", return_value=False),
        patch(
            "errander.agent.graph.next_window_open",
            return_value=datetime.now(tz=UTC) + timedelta(hours=2),
        ),
        patch("errander.agent.graph.await_dual_approval", new=AsyncMock(return_value=(True, "bob", None))),
    ):
        await approval_gate_node(
            state,
            approval_manager=approval_manager,
            deferred_store=deferred_store,
            audit_store=audit_store,
            window=window,
            require_live_approval=True,
        )

    deferred_events = [e for e in logged_events if e.event_type == EventType.EXECUTION_DEFERRED]
    assert deferred_events, "EXECUTION_DEFERRED event not logged"
    evt = deferred_events[0]
    assert evt.metadata.get("plan_hash") is not None
    assert evt.metadata.get("artifact_saved") is True


def test_deferred_execution_dataclass_fields() -> None:
    """DeferredExecution dataclass must have plan_json and plan_hash fields."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(DeferredExecution)}
    assert "plan_json" in field_names
    assert "plan_hash" in field_names


@pytest.mark.asyncio
async def test_save_upsert_replaces_artifact(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    try:
        window = _future_window()
        await store.save("batch-u", "dev", "alice", window, plan_json='{"v":1}', plan_hash="a" * 64)
        await store.save("batch-u", "dev", "alice", window, plan_json='{"v":2}', plan_hash="b" * 64)

        pending = await store.get_pending("dev")
        assert len(pending) == 1
        assert json.loads(pending[0].plan_json or "")["v"] == 2  # type: ignore[arg-type]
        assert pending[0].plan_hash == "b" * 64
    finally:
        await store.close()
