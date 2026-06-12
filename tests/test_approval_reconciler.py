"""Tests for the approval reconciler (_approval_reconciler) — R3 restart recovery.

AC3: a pending approval survives an agent restart; once decided, the approved
batch is executed exactly once via the exact-artifact replay path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.config.schema import EnvironmentSchema, TargetSchema
from errander.config.settings import Settings
from errander.main import _approval_reconciler
from errander.safety.approval_store import ApprovalRequestStore
from tests.conftest import make_test_db

_VM_PLANS: list[dict[str, object]] = [
    {
        "vm_id": "dev/web-01",
        "planned_actions": [{"action_type": "patching", "risk_tier": "medium"}],
    },
]


def _env_schema() -> EnvironmentSchema:
    target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
    return EnvironmentSchema(targets=[target])  # no maintenance window


async def _make_store() -> ApprovalRequestStore:
    store = ApprovalRequestStore(make_test_db())
    await store.initialize()
    return store


async def _run_reconciler(
    store: ApprovalRequestStore,
    *,
    environments: dict[str, EnvironmentSchema] | None = None,
    audit_store: AsyncMock | None = None,
    deferred_store: AsyncMock | None = None,
    slack_client: object = None,
    claim_grace_seconds: int = 0,
) -> AsyncMock:
    """Run one reconciler tick.

    claim_grace_seconds defaults to 0 so tests that decide-then-reconcile in
    the same instant exercise the claim path; the grace-period tests set it
    explicitly.
    """
    audit = audit_store if audit_store is not None else AsyncMock()
    with patch("errander.main._RECONCILER_CLAIM_GRACE_SECONDS", claim_grace_seconds):
        await _approval_reconciler(
            environments=environments if environments is not None else {"dev": _env_schema()},
            settings=Settings(),
            executor=MagicMock(),
            locker=MagicMock(),
            ssh_manager=MagicMock(),
            audit_store=audit,
            approval_store=store,
            deferred_store=deferred_store if deferred_store is not None else AsyncMock(),
            slack_client=slack_client,
            overrides_store=MagicMock(),
        )
    return audit


# ---------------------------------------------------------------------------
# Pass 1 — expire overdue pending requests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expires_overdue_pending_and_audits() -> None:
    store = await _make_store()
    await store.create(
        "batch-stale", env_name="dev", plan_id="p", plan_hash="a" * 64,
        report="r", timeout_seconds=0,
    )

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        audit = await _run_reconciler(store)

    req = await store.get("batch-stale")
    assert req is not None and req.status == "timeout"
    mock_run.assert_not_awaited()
    # One audit event for the expiry
    assert any(
        e.args[0].batch_id == "batch-stale" for e in audit.log_event.await_args_list
    )


# ---------------------------------------------------------------------------
# Pass 3 — execute approved-but-unclaimed requests (AC3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executes_orphaned_approved_via_replay() -> None:
    """Approved before a crash, never claimed → reconciler replays it exactly."""
    store = await _make_store()
    await store.create(
        "batch-orphan", env_name="dev", plan_id="plan-9", plan_hash="b" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-orphan", approved=True, decided_by="ui:alice")

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store)

    mock_run.assert_awaited_once()
    kwargs = mock_run.await_args.kwargs
    assert kwargs["preloaded_batch_id"] == "batch-orphan"
    assert kwargs["preloaded_plan_hash"] == "b" * 64
    assert kwargs["preloaded_plan_id"] == "plan-9"
    assert kwargs["dry_run"] is False
    assert kwargs["preloaded_approved_at"] is not None
    # The request was atomically claimed before execution
    req = await store.get("batch-orphan")
    assert req is not None and req.execution_started_at is not None


@pytest.mark.asyncio
async def test_orphan_executed_only_once_across_ticks() -> None:
    """Second reconciler tick must not re-execute a claimed batch."""
    store = await _make_store()
    await store.create(
        "batch-once", env_name="dev", plan_id="p", plan_hash="c" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-once", approved=True, decided_by="ui:alice")

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store)
        await _run_reconciler(store)

    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_orphan_without_artifact_claimed_not_executed() -> None:
    """No stored plan → fail closed: claim the row, audit, never execute."""
    store = await _make_store()
    await store.create(
        "batch-no-plan", env_name="dev", plan_id="p", plan_hash="d" * 64,
        report="r", vm_plans=None,
    )
    await store.decide("batch-no-plan", approved=True, decided_by="ui:alice")

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store)

    mock_run.assert_not_awaited()
    req = await store.get("batch-no-plan")
    assert req is not None and req.execution_started_at is not None


@pytest.mark.asyncio
async def test_orphan_unknown_environment_skipped() -> None:
    store = await _make_store()
    await store.create(
        "batch-unknown-env", env_name="gone", plan_id="p", plan_hash="e" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-unknown-env", approved=True, decided_by="ui:alice")

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store, environments={"dev": _env_schema()})

    mock_run.assert_not_awaited()
    # Left unclaimed — operator can fix the inventory and the next tick retries
    req = await store.get("batch-unknown-env")
    assert req is not None and req.execution_started_at is None


@pytest.mark.asyncio
async def test_orphan_outside_window_handed_to_deferred_store() -> None:
    store = await _make_store()
    await store.create(
        "batch-window", env_name="dev", plan_id="p", plan_hash="f" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-window", approved=True, decided_by="ui:alice")

    deferred = AsyncMock()
    from datetime import UTC, datetime, timedelta
    window_env = EnvironmentSchema(
        maintenance_window="02:00-06:00",
        maintenance_days=["monday"],
        targets=[TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")],
    )

    with (
        patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run,
        patch("errander.main.check_window_from_config", return_value=False),
        patch(
            "errander.main.next_window_open",
            return_value=datetime.now(tz=UTC) + timedelta(hours=4),
        ),
    ):
        await _run_reconciler(
            store, environments={"dev": window_env}, deferred_store=deferred,
        )

    mock_run.assert_not_awaited()
    deferred.save.assert_awaited_once()
    assert deferred.save.await_args.kwargs["batch_id"] == "batch-window"
    # Claimed — the deferred path owns it now; reconciler won't pick it up again
    req = await store.get("batch-window")
    assert req is not None and req.execution_started_at is not None


@pytest.mark.asyncio
async def test_orphan_with_live_waiter_left_alone() -> None:
    """An in-process gate between decision and claim keeps ownership."""
    store = await _make_store()
    await store.create(
        "batch-live", env_name="dev", plan_id="p", plan_hash="9" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-live", approved=True, decided_by="ui:alice")
    store._waiters["batch-live"] = asyncio.Event()  # simulate live gate waiting

    try:
        with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
            await _run_reconciler(store)
    finally:
        store._waiters.pop("batch-live", None)

    mock_run.assert_not_awaited()
    req = await store.get("batch-live")
    assert req is not None and req.execution_started_at is None


# ---------------------------------------------------------------------------
# R2 — no Slack watchers; orphaned pendings stay decidable from the web UI
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphaned_pending_left_pending_for_web_ui() -> None:
    """No watcher is spawned for orphaned pendings — the web UI decides them."""
    import errander.main as main_mod

    assert not hasattr(main_mod, "_resumed_slack_watchers")

    store = await _make_store()
    await store.create(
        "batch-pending", env_name="dev", plan_id="p", plan_hash="8" * 64,
        report="r", slack_message_ts="1700.42",
    )

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store, slack_client=MagicMock())

    mock_run.assert_not_awaited()
    req = await store.get("batch-pending")
    assert req is not None and req.status == "pending"  # still decidable


# ---------------------------------------------------------------------------
# Claim grace period — freshly decided approvals belong to their executor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freshly_decided_orphan_skipped_within_grace() -> None:
    """A cross-process executor (e.g. --restart-service CLI) sits between the
    decision and its claim — the reconciler must not steal the batch."""
    store = await _make_store()
    await store.create(
        "batch-fresh", env_name="dev", plan_id="p", plan_hash="1" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-fresh", approved=True, decided_by="ui:alice")

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store, claim_grace_seconds=120)

    mock_run.assert_not_awaited()
    req = await store.get("batch-fresh")
    assert req is not None and req.execution_started_at is None


@pytest.mark.asyncio
async def test_orphan_past_grace_is_claimed_and_executed() -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    store = await _make_store()
    await store.create(
        "batch-old", env_name="dev", plan_id="p", plan_hash="2" * 64,
        report="r", vm_plans=_VM_PLANS,
    )
    await store.decide("batch-old", approved=True, decided_by="ui:alice")
    backdated = (datetime.now(tz=UTC) - timedelta(minutes=10)).isoformat()
    async with store._db.begin() as conn:
        await conn.execute(
            text("UPDATE approval_requests SET decided_at = :d WHERE batch_id = 'batch-old'"),
            {"d": backdated},
        )

    with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
        await _run_reconciler(store, claim_grace_seconds=120)

    mock_run.assert_awaited_once()
