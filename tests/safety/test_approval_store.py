"""Tests for the durable ApprovalRequestStore (R3 keystone).

Locks in the race-safety contracts:
- decide() is atomic — concurrent deciders settle on exactly one winner (AC4)
- mark_execution_started() is an atomic claim — no double execution
- wait_for_decision() owns timeouts and survives in-process decisions
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from errander.safety.approval_store import ApprovalRequestStore


@pytest_asyncio.fixture
async def store() -> ApprovalRequestStore:
    from tests.conftest import make_test_db
    s = ApprovalRequestStore(make_test_db())
    await s.initialize()
    return s


_VM_PLANS: list[dict[str, object]] = [
    {
        "vm_id": "prod/web-1",
        "planned_actions": [
            {"action_type": "patching", "preview": {"packages": [
                {"name": "openssl", "current": "3.0.1", "target": "3.0.2"},
            ]}},
        ],
    },
]


async def _create(
    store: ApprovalRequestStore,
    batch_id: str = "batch-r3-001",
    timeout_seconds: int = 1800,
) -> None:
    await store.create(
        batch_id,
        env_name="production",
        plan_id="plan-abc",
        plan_hash="d" * 64,
        report="2 packages on prod/web-1",
        vm_plans=_VM_PLANS,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------

class TestCreateAndGet:
    @pytest.mark.asyncio
    async def test_create_get_round_trip(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        req = await store.get("batch-r3-001")

        assert req is not None
        assert req.batch_id == "batch-r3-001"
        assert req.env_name == "production"
        assert req.plan_id == "plan-abc"
        assert req.plan_hash == "d" * 64
        assert req.status == "pending"
        assert req.approved is None
        assert req.vm_plans == _VM_PLANS
        assert req.execution_started_at is None
        assert req.expires_at > req.posted_at

    @pytest.mark.asyncio
    async def test_get_unknown_returns_none(self, store: ApprovalRequestStore) -> None:
        assert await store.get("no-such-batch") is None

    @pytest.mark.asyncio
    async def test_pending_and_count(self, store: ApprovalRequestStore) -> None:
        await _create(store, "batch-a")
        await _create(store, "batch-b")

        pending = await store.get_pending()
        assert [p.batch_id for p in pending] == ["batch-a", "batch-b"]
        assert await store.count_pending() == 2

    @pytest.mark.asyncio
    async def test_set_slack_ts(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        await store.set_slack_ts("batch-r3-001", "1700000000.000001")

        req = await store.get("batch-r3-001")
        assert req is not None and req.slack_message_ts == "1700000000.000001"

    @pytest.mark.asyncio
    async def test_recreate_pending_row_updates_plan(self, store: ApprovalRequestStore) -> None:
        """Crash-restart re-entering the gate refreshes the pending row."""
        await _create(store)
        await store.create(
            "batch-r3-001",
            env_name="production",
            plan_id="plan-new",
            plan_hash="e" * 64,
            report="updated",
            timeout_seconds=1800,
        )
        req = await store.get("batch-r3-001")
        assert req is not None and req.plan_id == "plan-new"

    @pytest.mark.asyncio
    async def test_recreate_does_not_resurrect_decided_row(
        self, store: ApprovalRequestStore,
    ) -> None:
        await _create(store)
        await store.decide("batch-r3-001", approved=False, decided_by="ui:admin")

        await _create(store)  # ON CONFLICT guarded by status = 'pending'
        req = await store.get("batch-r3-001")
        assert req is not None and req.status == "rejected"


# ---------------------------------------------------------------------------
# decide — atomic, exactly one winner (AC4)
# ---------------------------------------------------------------------------

class TestDecide:
    @pytest.mark.asyncio
    async def test_approve_records_decision(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        won = await store.decide(
            "batch-r3-001", approved=True, decided_by="slack:U123",
            approved_items=[{"vm_id": "prod/web-1", "action_type": "patching",
                             "packages": [{"name": "openssl"}]}],
        )

        assert won is True
        req = await store.get("batch-r3-001")
        assert req is not None
        assert req.status == "approved"
        assert req.approved is True
        assert req.decided_by == "slack:U123"
        assert req.decided_at is not None
        assert req.approved_items is not None
        assert req.approved_items[0]["vm_id"] == "prod/web-1"

    @pytest.mark.asyncio
    async def test_reject_records_decision(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        won = await store.decide("batch-r3-001", approved=False, decided_by="ui:admin")

        assert won is True
        req = await store.get("batch-r3-001")
        assert req is not None and req.status == "rejected" and req.approved is False

    @pytest.mark.asyncio
    async def test_second_decide_loses_and_does_not_overwrite(
        self, store: ApprovalRequestStore,
    ) -> None:
        await _create(store)
        assert await store.decide("batch-r3-001", approved=True, decided_by="slack:U1")
        lost = await store.decide("batch-r3-001", approved=False, decided_by="ui:admin")

        assert lost is False
        req = await store.get("batch-r3-001")
        assert req is not None
        assert req.status == "approved"
        assert req.decided_by == "slack:U1"

    @pytest.mark.asyncio
    async def test_concurrent_decide_race_has_exactly_one_winner(
        self, store: ApprovalRequestStore,
    ) -> None:
        """AC4: Slack watcher and UI click race — exactly one decision lands."""
        await _create(store)

        results = await asyncio.gather(
            store.decide("batch-r3-001", approved=True, decided_by="slack:U1"),
            store.decide("batch-r3-001", approved=False, decided_by="ui:admin"),
        )

        assert sorted(results) == [False, True]  # exactly one winner
        req = await store.get("batch-r3-001")
        assert req is not None
        # The recorded decision matches whichever caller won.
        if req.status == "approved":
            assert req.decided_by == "slack:U1"
        else:
            assert req.status == "rejected"
            assert req.decided_by == "ui:admin"

    @pytest.mark.asyncio
    async def test_decide_unknown_batch_is_noop(self, store: ApprovalRequestStore) -> None:
        assert await store.decide("no-such", approved=True, decided_by="ui:x") is False


# ---------------------------------------------------------------------------
# Timeout + expiry
# ---------------------------------------------------------------------------

class TestTimeout:
    @pytest.mark.asyncio
    async def test_mark_timeout(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        assert await store.mark_timeout("batch-r3-001") is True

        req = await store.get("batch-r3-001")
        assert req is not None and req.status == "timeout" and req.approved is False

    @pytest.mark.asyncio
    async def test_mark_timeout_loses_to_decision(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        await store.decide("batch-r3-001", approved=True, decided_by="ui:admin")

        assert await store.mark_timeout("batch-r3-001") is False
        req = await store.get("batch-r3-001")
        assert req is not None and req.status == "approved"

    @pytest.mark.asyncio
    async def test_expire_overdue(self, store: ApprovalRequestStore) -> None:
        await _create(store, "batch-old", timeout_seconds=0)   # expires immediately
        await _create(store, "batch-new", timeout_seconds=1800)

        expired = await store.expire_overdue()

        assert expired == ["batch-old"]
        old = await store.get("batch-old")
        new = await store.get("batch-new")
        assert old is not None and old.status == "timeout"
        assert new is not None and new.status == "pending"


# ---------------------------------------------------------------------------
# mark_execution_started — atomic claim
# ---------------------------------------------------------------------------

class TestExecutionClaim:
    @pytest.mark.asyncio
    async def test_claim_approved_request(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        await store.decide("batch-r3-001", approved=True, decided_by="ui:admin")

        assert await store.mark_execution_started("batch-r3-001") is True
        req = await store.get("batch-r3-001")
        assert req is not None and req.execution_started_at is not None

    @pytest.mark.asyncio
    async def test_second_claim_refused(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        await store.decide("batch-r3-001", approved=True, decided_by="ui:admin")
        assert await store.mark_execution_started("batch-r3-001") is True

        assert await store.mark_execution_started("batch-r3-001") is False

    @pytest.mark.asyncio
    async def test_cannot_claim_pending_or_rejected(self, store: ApprovalRequestStore) -> None:
        await _create(store, "batch-pend")
        await _create(store, "batch-rej")
        await store.decide("batch-rej", approved=False, decided_by="ui:admin")

        assert await store.mark_execution_started("batch-pend") is False
        assert await store.mark_execution_started("batch-rej") is False

    @pytest.mark.asyncio
    async def test_orphaned_approved_listing(self, store: ApprovalRequestStore) -> None:
        await _create(store, "batch-orphan")
        await _create(store, "batch-claimed")
        await _create(store, "batch-pending")
        await store.decide("batch-orphan", approved=True, decided_by="ui:admin")
        await store.decide("batch-claimed", approved=True, decided_by="ui:admin")
        await store.mark_execution_started("batch-claimed")

        orphans = await store.get_orphaned_approved()
        assert [o.batch_id for o in orphans] == ["batch-orphan"]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class TestHistory:
    @pytest.mark.asyncio
    async def test_history_newest_first_with_limit(self, store: ApprovalRequestStore) -> None:
        for i in range(5):
            await _create(store, f"batch-h{i}")
            await store.decide(f"batch-h{i}", approved=True, decided_by="ui:admin")

        history = await store.get_history(limit=3)
        assert len(history) == 3
        assert history[0].batch_id == "batch-h4"  # newest decision first

    @pytest.mark.asyncio
    async def test_history_excludes_pending(self, store: ApprovalRequestStore) -> None:
        await _create(store, "batch-pend")
        await _create(store, "batch-done")
        await store.decide("batch-done", approved=False, decided_by="ui:admin")

        history = await store.get_history()
        assert [h.batch_id for h in history] == ["batch-done"]


# ---------------------------------------------------------------------------
# wait_for_decision
# ---------------------------------------------------------------------------

class TestWaitForDecision:
    @pytest.mark.asyncio
    async def test_returns_on_in_process_decision(self, store: ApprovalRequestStore) -> None:
        await _create(store)

        async def _decide_soon() -> None:
            await asyncio.sleep(0.05)
            await store.decide("batch-r3-001", approved=True, decided_by="ui:admin")

        task = asyncio.create_task(_decide_soon())
        started = datetime.now(tz=UTC)
        req = await store.wait_for_decision("batch-r3-001", timeout_seconds=30)
        await task

        assert req.status == "approved"
        # In-process event wakeup — must return well before the poll interval
        assert (datetime.now(tz=UTC) - started).total_seconds() < 5

    @pytest.mark.asyncio
    async def test_timeout_marks_row_and_returns(self, store: ApprovalRequestStore) -> None:
        await _create(store)

        req = await store.wait_for_decision("batch-r3-001", timeout_seconds=0)

        assert req.status == "timeout"
        assert req.approved is False

    @pytest.mark.asyncio
    async def test_already_decided_returns_immediately(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        await store.decide("batch-r3-001", approved=False, decided_by="slack:U9")

        req = await store.wait_for_decision("batch-r3-001", timeout_seconds=30)
        assert req.status == "rejected"

    @pytest.mark.asyncio
    async def test_unknown_batch_raises(self, store: ApprovalRequestStore) -> None:
        with pytest.raises(KeyError, match="no-such"):
            await store.wait_for_decision("no-such-batch", timeout_seconds=1)

    @pytest.mark.asyncio
    async def test_has_waiter_reflects_active_wait(self, store: ApprovalRequestStore) -> None:
        await _create(store)
        assert store.has_waiter("batch-r3-001") is False

        wait_task = asyncio.create_task(
            store.wait_for_decision("batch-r3-001", timeout_seconds=30)
        )
        await asyncio.sleep(0.05)
        assert store.has_waiter("batch-r3-001") is True

        await store.decide("batch-r3-001", approved=True, decided_by="ui:admin")
        await wait_task
        assert store.has_waiter("batch-r3-001") is False
