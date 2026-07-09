"""Tests for VMFactsStore — operational learning memory (Phase B1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from errander.safety.audit import AuditStore
from errander.safety.vm_facts import (
    VMFactsStore,
)
from tests.conftest import make_test_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_stores() -> tuple[AuditStore, VMFactsStore]:
    db = make_test_db()
    audit = AuditStore(db, strict_mode=False)
    await audit.initialize()
    facts = VMFactsStore(db)
    return audit, facts


async def _insert_event(
    audit: AuditStore,
    *,
    event_type: str,
    batch_id: str = "b1",
    vm_id: str | None = None,
    action_type: str | None = None,
    detail: str = "",
    timestamp: datetime | None = None,
) -> None:
    from errander.models.events import AuditEvent, EventType

    # Map plain string to EventType if possible
    try:
        et = EventType(event_type)
    except ValueError:
        pytest.skip(f"EventType '{event_type}' not in enum — skipping")
        return

    event = AuditEvent(
        event_type=et,
        batch_id=batch_id,
        vm_id=vm_id,
        action_type=action_type,
        detail=detail,
        timestamp=timestamp or datetime.now(tz=UTC),
    )
    await audit.log_event(event)


# ---------------------------------------------------------------------------
# ActionOutcomeFact tests
# ---------------------------------------------------------------------------


class TestActionOutcomes:
    async def test_empty_db_returns_empty(self) -> None:
        audit, facts = await _make_stores()
        results = await facts.action_outcomes("prod/vm1")
        assert results == []
        await audit.close()

    async def test_success_rate_all_completed(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(5):
            await _insert_event(
                audit,
                event_type="action_completed",
                vm_id="prod/vm1",
                action_type="patching",
            )

        results = await facts.action_outcomes("prod/vm1")
        assert len(results) == 1
        assert results[0].success_rate == pytest.approx(1.0)
        assert results[0].sample_size == 5
        await audit.close()

    async def test_success_rate_mixed(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(3):
            await _insert_event(
                audit,
                event_type="action_completed",
                vm_id="vm1",
                action_type="disk_cleanup",
            )
        for _ in range(2):
            await _insert_event(
                audit,
                event_type="action_failed",
                vm_id="vm1",
                action_type="disk_cleanup",
                detail="permission denied",
            )

        results = await facts.action_outcomes("vm1", action_type="disk_cleanup")
        assert len(results) == 1
        r = results[0]
        assert r.success_rate == pytest.approx(0.6)
        assert r.sample_size == 5
        await audit.close()

    async def test_last_failure_reason_captured(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit,
            event_type="action_failed",
            vm_id="vm1",
            action_type="patching",
            detail="dpkg lock held",
            timestamp=datetime.now(tz=UTC) - timedelta(seconds=10),
        )
        await _insert_event(
            audit,
            event_type="action_completed",
            vm_id="vm1",
            action_type="patching",
        )

        results = await facts.action_outcomes("vm1")
        assert len(results) == 1
        assert results[0].last_failure_reason == "dpkg lock held"
        await audit.close()

    async def test_last_success_at_captured(self) -> None:
        audit, facts = await _make_stores()

        ts = datetime.now(tz=UTC) - timedelta(hours=2)
        await _insert_event(
            audit,
            event_type="action_completed",
            vm_id="vm1",
            action_type="docker_prune",
            timestamp=ts,
        )

        results = await facts.action_outcomes("vm1")
        assert len(results) == 1
        assert results[0].last_success_at is not None
        await audit.close()

    async def test_sample_size_capped_at_20(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(30):
            await _insert_event(
                audit,
                event_type="action_completed",
                vm_id="vm1",
                action_type="log_rotation",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].sample_size == 20
        await audit.close()

    async def test_filter_by_action_type(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit, event_type="action_completed", vm_id="vm1", action_type="patching",
        )
        await _insert_event(
            audit, event_type="action_completed", vm_id="vm1", action_type="disk_cleanup",
        )

        results = await facts.action_outcomes("vm1", action_type="patching")
        assert len(results) == 1
        assert results[0].action_type == "patching"
        await audit.close()

    async def test_different_vms_isolated(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit, event_type="action_completed", vm_id="vm1", action_type="patching",
        )
        await _insert_event(
            audit, event_type="action_failed", vm_id="vm2", action_type="patching",
        )

        r1 = await facts.action_outcomes("vm1")
        r2 = await facts.action_outcomes("vm2")
        assert r1[0].success_rate == pytest.approx(1.0)
        assert r2[0].success_rate == pytest.approx(0.0)
        await audit.close()


# ---------------------------------------------------------------------------
# VMRebootPatternFact tests
# ---------------------------------------------------------------------------


class TestRebootPattern:
    async def test_no_patching_history_returns_none(self) -> None:
        audit, facts = await _make_stores()
        result = await facts.reboot_pattern("vm1")
        assert result is None
        await audit.close()

    async def test_reboot_count_computed(self) -> None:
        audit, facts = await _make_stores()

        # 3 patching completions
        for _ in range(3):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )
        # 2 reboot detections
        for _ in range(2):
            await _insert_event(
                audit, event_type="reboot_required_detected", vm_id="vm1",
            )

        result = await facts.reboot_pattern("vm1")
        assert result is not None
        assert result.reboots_required_after_patching == 2
        assert result.sample_size == 3
        assert result.vm_id == "vm1"
        await audit.close()


# ---------------------------------------------------------------------------
# ActionRejectionFact tests
# ---------------------------------------------------------------------------


class TestRejectionFacts:
    async def test_empty_db_returns_empty(self) -> None:
        audit, facts = await _make_stores()
        result = await facts.rejection_facts()
        assert result == []
        await audit.close()

    async def test_rejection_counted_and_reason_captured(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit,
            event_type="batch_started",
            batch_id="b-rej",
        )
        await _insert_event(
            audit,
            event_type="action_planned",
            batch_id="b-rej",
            action_type="patching",
        )
        await _insert_event(
            audit,
            event_type="approval_rejected",
            batch_id="b-rej",
            detail="maintenance freeze in effect",
        )

        result = await facts.rejection_facts()
        assert any(r.action_type == "patching" for r in result)
        patching_fact = next(r for r in result if r.action_type == "patching")
        assert patching_fact.rejections_last_90d == 1
        assert "maintenance freeze in effect" in patching_fact.rejection_reasons
        await audit.close()

    async def test_multiple_rejections_for_same_action_type(self) -> None:
        audit, facts = await _make_stores()

        for i in range(3):
            bid = f"rej-{i}"
            await _insert_event(
                audit, event_type="batch_started", batch_id=bid,
            )
            await _insert_event(
                audit, event_type="action_planned", batch_id=bid, action_type="patching",
            )
            await _insert_event(
                audit,
                event_type="approval_rejected",
                batch_id=bid,
                detail=f"reason-{i}",
            )

        result = await facts.rejection_facts()
        patching_fact = next(r for r in result if r.action_type == "patching")
        assert patching_fact.rejections_last_90d == 3
        await audit.close()

    async def test_rejection_outside_90d_window_excluded(self) -> None:
        audit, facts = await _make_stores()

        old_ts = datetime.now(tz=UTC) - timedelta(days=100)
        await _insert_event(
            audit, event_type="batch_started", batch_id="old-rej", timestamp=old_ts,
        )
        await _insert_event(
            audit, event_type="action_planned", batch_id="old-rej", action_type="patching",
        )
        await _insert_event(
            audit,
            event_type="approval_rejected",
            batch_id="old-rej",
            detail="old reason",
            timestamp=old_ts,
        )

        result = await facts.rejection_facts()
        assert all(r.action_type != "patching" for r in result)
        await audit.close()


# ---------------------------------------------------------------------------
# Confidence label tests (Phase 4)
# ---------------------------------------------------------------------------


class TestConfidenceLabels:
    async def test_action_outcome_low_confidence_small_sample(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(3):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].confidence == "low"
        await audit.close()

    async def test_action_outcome_medium_confidence(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(7):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].confidence == "medium"
        await audit.close()

    async def test_action_outcome_high_confidence_large_sample(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(12):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].confidence == "high"
        await audit.close()

    async def test_reboot_pattern_confidence_low(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(2):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )
        await _insert_event(
            audit, event_type="reboot_required_detected", vm_id="vm1",
        )

        result = await facts.reboot_pattern("vm1")
        assert result is not None
        assert result.confidence == "low"
        await audit.close()

    async def test_reboot_pattern_confidence_high(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(15):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="patching",
            )
        for _ in range(5):
            await _insert_event(
                audit, event_type="reboot_required_detected", vm_id="vm1",
            )

        result = await facts.reboot_pattern("vm1")
        assert result is not None
        assert result.confidence == "high"
        await audit.close()

    async def test_rejection_fact_confidence_low_one_rejection(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(audit, event_type="batch_started", batch_id="b-rej1")
        await _insert_event(
            audit, event_type="action_planned", batch_id="b-rej1", action_type="patching",
        )
        await _insert_event(
            audit, event_type="approval_rejected", batch_id="b-rej1", detail="reason",
        )

        result = await facts.rejection_facts()
        patching = next(r for r in result if r.action_type == "patching")
        assert patching.confidence == "low"
        await audit.close()

    async def test_rejection_fact_confidence_medium(self) -> None:
        audit, facts = await _make_stores()

        for i in range(3):
            bid = f"b-med-{i}"
            await _insert_event(audit, event_type="batch_started", batch_id=bid)
            await _insert_event(
                audit, event_type="action_planned", batch_id=bid, action_type="disk_cleanup",
            )
            await _insert_event(
                audit, event_type="approval_rejected", batch_id=bid, detail="reason",
            )

        result = await facts.rejection_facts()
        fact = next(r for r in result if r.action_type == "disk_cleanup")
        assert fact.confidence == "medium"
        await audit.close()

    async def test_rejection_fact_confidence_high(self) -> None:
        audit, facts = await _make_stores()

        for i in range(6):
            bid = f"b-high-{i}"
            await _insert_event(audit, event_type="batch_started", batch_id=bid)
            await _insert_event(
                audit, event_type="action_planned", batch_id=bid, action_type="log_rotation",
            )
            await _insert_event(
                audit, event_type="approval_rejected", batch_id=bid, detail="reason",
            )

        result = await facts.rejection_facts()
        fact = next(r for r in result if r.action_type == "log_rotation")
        assert fact.confidence == "high"
        await audit.close()

    async def test_confidence_boundary_exactly_5_is_medium(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(5):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="backup_verify",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].confidence == "medium"
        await audit.close()

    async def test_confidence_boundary_exactly_10_is_high(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(10):
            await _insert_event(
                audit, event_type="action_completed", vm_id="vm1", action_type="backup_verify",
            )

        results = await facts.action_outcomes("vm1")
        assert results[0].confidence == "high"
        await audit.close()


# ---------------------------------------------------------------------------
# ProposalOutcomeFact tests (fable-plan Phase 4)
# ---------------------------------------------------------------------------


class TestProposalOutcomes:
    async def test_counts_each_lifecycle_event_type(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_approved", vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_execution_completed",
            vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_rejected", vm_id="web-01", action_type="disk_cleanup",
        )

        results = await facts.proposal_outcomes("web-01")
        assert len(results) == 1
        f = results[0]
        assert f.action_type == "disk_cleanup"
        assert f.proposed_count == 2
        assert f.approved_count == 1
        assert f.rejected_count == 1
        assert f.executed_success_count == 1
        assert f.executed_failed_count == 0
        await audit.close()

    async def test_last_decided_at_is_most_recent_decision(self) -> None:
        audit, facts = await _make_stores()
        older = datetime(2026, 1, 1, tzinfo=UTC)
        newer = datetime(2026, 6, 1, tzinfo=UTC)

        await _insert_event(
            audit, event_type="proposal_approved", vm_id="web-01",
            action_type="disk_cleanup", timestamp=older,
        )
        await _insert_event(
            audit, event_type="proposal_rejected", vm_id="web-01",
            action_type="disk_cleanup", timestamp=newer,
        )

        results = await facts.proposal_outcomes("web-01")
        assert results[0].last_decided_at == newer
        await audit.close()

    async def test_separates_by_action_type(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="log_rotation",
        )

        results = await facts.proposal_outcomes("web-01")
        assert {f.action_type for f in results} == {"disk_cleanup", "log_rotation"}
        await audit.close()

    async def test_review_only_events_excluded(self) -> None:
        """REVIEW proposals have no action_type — must not appear in facts."""
        audit, facts = await _make_stores()

        # A review-kind proposal's audit event carries action_type=None (or
        # empty) — mirroring detector.py's `action_type=stored.action_type or None`.
        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type=None,
        )
        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="disk_cleanup",
        )

        results = await facts.proposal_outcomes("web-01")
        assert len(results) == 1
        assert results[0].action_type == "disk_cleanup"
        await audit.close()

    async def test_no_history_returns_empty_list(self) -> None:
        _audit, facts = await _make_stores()
        assert await facts.proposal_outcomes("web-99") == []

    async def test_scoped_to_the_requested_vm(self) -> None:
        audit, facts = await _make_stores()

        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-01", action_type="disk_cleanup",
        )
        await _insert_event(
            audit, event_type="proposal_created", vm_id="web-02", action_type="disk_cleanup",
        )

        results = await facts.proposal_outcomes("web-01")
        assert len(results) == 1
        await audit.close()

    async def test_confidence_derived_from_proposed_count(self) -> None:
        audit, facts = await _make_stores()

        for _ in range(10):
            await _insert_event(
                audit, event_type="proposal_created",
                vm_id="web-01", action_type="disk_cleanup",
            )

        results = await facts.proposal_outcomes("web-01")
        assert results[0].confidence == "high"
        await audit.close()
