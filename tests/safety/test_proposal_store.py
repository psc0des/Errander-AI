"""Tests for ProposalStore (agent_proposals, migration #16).

Locks in the fable-plan Phase 1 store contract: dedup upsert (one open
proposal per vm/action_key), atomic decide, snooze honored verbatim,
expiry, and the execution claim used by the proposal reconciler.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
    ProposalStatus,
)
from errander.safety.proposal_store import ProposalStore
from tests.conftest import make_test_db


def _proposal(**overrides: object) -> AgentProposal:
    defaults: dict[str, object] = {
        "env_name": "prod",
        "vm_id": "web-01",
        "kind": ProposalKind.ACTION,
        "action_type": "disk_cleanup",
        "signal_kind": "disk_growth",
        "probe_id": "probe-1",
        "evidence": [ProposalEvidence(
            source="probe:disk_history", check="trend", observation="/var at 91%",
        )],
    }
    defaults.update(overrides)
    return AgentProposal(**defaults)  # type: ignore[arg-type]


async def _make_store() -> ProposalStore:
    store = ProposalStore(make_test_db())
    await store.initialize()
    return store


class TestCreateOrRefresh:
    @pytest.mark.asyncio
    async def test_create_persists_full_row(self) -> None:
        store = await _make_store()
        stored, created = await store.create_or_refresh(_proposal())
        assert created is True
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.vm_id == "web-01"
        assert loaded.action_type == "disk_cleanup"
        assert loaded.status == ProposalStatus.PENDING
        assert loaded.evidence[0].observation == "/var at 91%"
        assert loaded.expires_at is not None

    @pytest.mark.asyncio
    async def test_dedup_refreshes_open_proposal(self) -> None:
        """One open proposal per (vm_id, action_key) — evidence refreshed."""
        store = await _make_store()
        first, created1 = await store.create_or_refresh(_proposal())
        second, created2 = await store.create_or_refresh(_proposal(
            probe_id="probe-2",
            confidence="high",
            evidence=[ProposalEvidence(
                source="probe:disk_history", check="trend", observation="/var at 95%",
            )],
        ))
        assert created1 is True
        assert created2 is False
        assert second.proposal_id == first.proposal_id
        assert second.confidence == "high"
        assert second.probe_id == "probe-2"
        assert second.evidence[0].observation == "/var at 95%"
        assert await store.count_pending() == 1

    @pytest.mark.asyncio
    async def test_decided_proposal_does_not_block_new_one(self) -> None:
        store = await _make_store()
        first, _ = await store.create_or_refresh(_proposal())
        await store.decide(first.proposal_id, approved=False, decided_by="ui:a")
        second, created = await store.create_or_refresh(_proposal())
        assert created is True
        assert second.proposal_id != first.proposal_id

    @pytest.mark.asyncio
    async def test_different_action_keys_coexist(self) -> None:
        store = await _make_store()
        await store.create_or_refresh(_proposal())
        _, created = await store.create_or_refresh(_proposal(
            action_type="log_rotation",
        ))
        assert created is True
        assert await store.count_pending() == 2


class TestDecide:
    @pytest.mark.asyncio
    async def test_decide_approve_records_identity(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        won = await store.decide(
            stored.proposal_id, approved=True,
            decided_by="ui:alice", decided_by_group="admin",
        )
        assert won is True
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.status == ProposalStatus.APPROVED
        assert loaded.decided_by == "ui:alice"
        assert loaded.decided_by_group == "admin"
        assert loaded.decided_at is not None

    @pytest.mark.asyncio
    async def test_decide_is_atomic_exactly_one_winner(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        first = await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        second = await store.decide(stored.proposal_id, approved=False, decided_by="ui:b")
        assert first is True
        assert second is False
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.APPROVED


class TestSnooze:
    @pytest.mark.asyncio
    async def test_snooze_and_wake(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        won = await store.snooze(
            stored.proposal_id,
            snoozed_until=datetime.now(tz=UTC) - timedelta(seconds=1),
            decided_by="ui:alice",
        )
        assert won is True
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.SNOOZED

        woken = await store.wake_snoozed()
        assert stored.proposal_id in woken
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.PENDING
        assert loaded.snoozed_until is None

    @pytest.mark.asyncio
    async def test_future_snooze_not_woken(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.snooze(
            stored.proposal_id,
            snoozed_until=datetime.now(tz=UTC) + timedelta(days=3),
            decided_by="ui:alice",
        )
        assert await store.wake_snoozed() == []


class TestExpiry:
    @pytest.mark.asyncio
    async def test_expire_overdue(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal(), expiry_days=0)
        expired = await store.expire_overdue()
        assert stored.proposal_id in expired
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_fresh_proposal_not_expired(self) -> None:
        store = await _make_store()
        await store.create_or_refresh(_proposal())
        assert await store.expire_overdue() == []


class TestExecutionClaim:
    @pytest.mark.asyncio
    async def test_claim_requires_approved_actionable(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        # Pending — refuse
        assert await store.mark_execution_started(stored.proposal_id) is False
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        # Approved — claim once, exactly once
        assert await store.mark_execution_started(stored.proposal_id) is True
        assert await store.mark_execution_started(stored.proposal_id) is False

    @pytest.mark.asyncio
    async def test_review_proposal_never_claimable(self) -> None:
        """Approving a review proposal acknowledges — it must never execute."""
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal(
            kind=ProposalKind.REVIEW, action_type="", signal_kind="drift",
        ))
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        assert await store.mark_execution_started(stored.proposal_id) is False
        assert await store.get_approved_unclaimed() == []

    @pytest.mark.asyncio
    async def test_approved_unclaimed_feed_and_status(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=True, decided_by="ui:a")
        feed = await store.get_approved_unclaimed()
        assert [p.proposal_id for p in feed] == [stored.proposal_id]
        await store.mark_execution_started(stored.proposal_id)
        await store.set_execution_status(stored.proposal_id, "success")
        assert await store.get_approved_unclaimed() == []
        loaded = await store.get(stored.proposal_id)
        assert loaded is not None and loaded.execution_status == "success"


class TestReadsAndSuppressionInput:
    @pytest.mark.asyncio
    async def test_history_excludes_pending(self) -> None:
        store = await _make_store()
        a, _ = await store.create_or_refresh(_proposal())
        b, _ = await store.create_or_refresh(_proposal(action_type="log_rotation"))
        await store.decide(a.proposal_id, approved=False, decided_by="ui:a")
        history = await store.get_history()
        assert [p.proposal_id for p in history] == [a.proposal_id]
        pending = await store.get_pending()
        assert [p.proposal_id for p in pending] == [b.proposal_id]

    @pytest.mark.asyncio
    async def test_count_rejections(self) -> None:
        """Phase 4 suppression input: rejections per (vm_id, action_key)."""
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.count_rejections("web-01", "disk_cleanup") == 2
        assert await store.count_rejections("web-01", "log_rotation") == 0


class TestSuppression:
    """fable-plan Phase 4 — re-proposal suppression after repeated rejection."""

    @pytest.mark.asyncio
    async def test_rejection_window_state_count_and_timestamp(self) -> None:
        store = await _make_store()
        count, latest = await store.rejection_window_state("web-01", "disk_cleanup")
        assert (count, latest) == (0, None)

        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        count, latest = await store.rejection_window_state("web-01", "disk_cleanup")
        assert count == 1
        assert latest is not None

    @pytest.mark.asyncio
    async def test_below_threshold_not_suppressed(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.is_suppressed(
            "web-01", "disk_cleanup", threshold=2, window_days=14,
        ) is False

    @pytest.mark.asyncio
    async def test_at_threshold_is_suppressed(self) -> None:
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.is_suppressed(
            "web-01", "disk_cleanup", threshold=2, window_days=14,
        ) is True

    @pytest.mark.asyncio
    async def test_outside_window_not_suppressed(self) -> None:
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        # threshold met, but the cooldown window has already elapsed
        assert await store.is_suppressed(
            "web-01", "disk_cleanup", threshold=2, window_days=0,
        ) is False

    @pytest.mark.asyncio
    async def test_different_action_key_not_suppressed(self) -> None:
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.is_suppressed(
            "web-01", "log_rotation", threshold=2, window_days=14,
        ) is False

    @pytest.mark.asyncio
    async def test_get_open_returns_pending_row(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        found = await store.get_open("web-01", "disk_cleanup")
        assert found is not None and found.proposal_id == stored.proposal_id

    @pytest.mark.asyncio
    async def test_get_open_none_when_no_pending_row(self) -> None:
        store = await _make_store()
        assert await store.get_open("web-01", "disk_cleanup") is None
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.get_open("web-01", "disk_cleanup") is None

    @pytest.mark.asyncio
    async def test_create_blocked_when_suppressed(self) -> None:
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")

        result, created = await store.create_or_refresh_unless_suppressed(
            _proposal(), suppression_threshold=2, suppression_window_days=14,
        )
        assert (result, created) == (None, False)
        assert await store.count_pending() == 0  # nothing was written

    @pytest.mark.asyncio
    async def test_create_allowed_below_threshold(self) -> None:
        store = await _make_store()
        stored, _ = await store.create_or_refresh(_proposal())
        await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")

        result, created = await store.create_or_refresh_unless_suppressed(
            _proposal(), suppression_threshold=2, suppression_window_days=14,
        )
        assert result is not None
        assert created is True

    @pytest.mark.asyncio
    async def test_refresh_of_open_row_never_suppressed(self) -> None:
        """Suppression only blocks a fresh CREATE — an already-open pending
        proposal must always be refreshable, even if this exact pair has
        also racked up 2+ historical rejections from an earlier cycle."""
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        # A third proposal is now legitimately open (suppression only blocks
        # the CREATE, and by construction there's no open row yet here) —
        # simulate that by creating one directly below threshold, then
        # driving the count to threshold via a same-cycle extra rejection
        # is not representative; instead verify refresh directly:
        third, created = await store.create_or_refresh_unless_suppressed(
            _proposal(evidence=[]), suppression_threshold=99, suppression_window_days=14,
        )
        assert third is not None and created is True
        # Now refresh that SAME open row while suppression conditions (2
        # historical rejections) are independently true for this pair.
        refreshed, created2 = await store.create_or_refresh_unless_suppressed(
            _proposal(evidence=[ProposalEvidence(
                source="probe:disk_history", check="c", observation="new evidence",
            )]),
            suppression_threshold=2, suppression_window_days=14,
        )
        assert refreshed is not None
        assert created2 is False
        assert refreshed.proposal_id == third.proposal_id

    @pytest.mark.asyncio
    async def test_snooze_unaffected_by_suppression_state(self) -> None:
        """Snooze is an independent code path — works normally regardless
        of whether suppression conditions are also met for the pair."""
        store = await _make_store()
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_proposal())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
        assert await store.is_suppressed(
            "web-01", "disk_cleanup", threshold=2, window_days=14,
        ) is True

        # A fresh open proposal (e.g. filed with a high threshold override)
        # can still be snoozed normally.
        fresh, _ = await store.create_or_refresh_unless_suppressed(
            _proposal(), suppression_threshold=99, suppression_window_days=14,
        )
        assert fresh is not None
        won = await store.snooze(
            fresh.proposal_id,
            snoozed_until=datetime.now(tz=UTC) + timedelta(days=3),
            decided_by="ui:a",
        )
        assert won is True
        loaded = await store.get(fresh.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.SNOOZED
