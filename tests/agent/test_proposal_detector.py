"""Tests for the deterministic proposal detector (fable-plan Phase 1).

The detector is Layer B-adjacent deterministic code: no LLM, pure rules over
probe signals, ACTION proposals only for inventory-enabled actions, and
review-only proposals for signals a human must look at.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from errander.agent.proposal_detector import (
    FAILED_LOGIN_THRESHOLD,
    detect_proposals,
    file_or_suppress_one,
    file_proposals,
)
from errander.models.events import EventType
from errander.models.proposals import AgentProposal, ProposalKind
from errander.models.reports import DigestReport, ProbeVMResult
from errander.safety.proposal_store import ProposalStore
from tests.conftest import make_test_db

_ALL_ENABLED = {"web-01": {"disk_cleanup", "log_rotation", "patching"}}


def _digest(vm_results: list[ProbeVMResult]) -> DigestReport:
    return DigestReport(
        probe_id="probe-test-1",
        env_name="prod",
        generated_at=datetime.now(tz=UTC),
        vm_results=vm_results,
    )


def _disk_alert(mountpoint: str = "/", pct: float = 85.0, delta: float = 6.0) -> dict[str, object]:
    return {"mountpoint": mountpoint, "used_pct_end": pct, "delta_pct": delta}


class TestDiskGrowthRule:
    def test_disk_alert_yields_disk_cleanup_proposal(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.kind == ProposalKind.ACTION
        assert p.action_type == "disk_cleanup"
        assert p.signal_kind == "disk_growth"
        assert p.vm_id == "web-01"
        assert p.probe_id == "probe-test-1"
        assert p.confidence == "medium"
        assert "85%" in p.evidence[0].observation

    def test_var_mountpoint_adds_log_rotation(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert(mountpoint="/var/log")],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        assert {p.action_type for p in proposals} == {"disk_cleanup", "log_rotation"}

    def test_high_confidence_when_nearly_full(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert(pct=92.0)],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        assert proposals[0].confidence == "high"

    def test_disabled_action_not_proposed(self) -> None:
        """The detector never proposes work the inventory forbids."""
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])
        proposals = detect_proposals(
            report, enabled_actions_by_vm={"web-01": {"patching"}},
        )
        assert proposals == []

    def test_unknown_vm_defaults_to_nothing_enabled(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])
        assert detect_proposals(report, enabled_actions_by_vm={}) == []


class TestReviewRules:
    def test_drift_yields_review_only(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            drift_changes=[{
                "kind": "sudoers", "scope_key": "", "unified_diff": "+bob ALL=(ALL)",
            }],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.kind == ProposalKind.REVIEW
        assert p.action_type == ""
        assert p.is_actionable is False
        assert p.signal_kind == "drift"
        assert "sudoers" in p.evidence[0].check

    def test_failed_logins_above_threshold(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            failed_login_summary={
                "total_count": FAILED_LOGIN_THRESHOLD + 5,
                "window_hours": 24,
                "top_source_ips": [["1.2.3.4", 20]],
            },
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        assert len(proposals) == 1
        assert proposals[0].kind == ProposalKind.REVIEW
        assert proposals[0].signal_kind == "failed_logins"

    def test_failed_logins_at_threshold_ignored(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            failed_login_summary={"total_count": FAILED_LOGIN_THRESHOLD},
        )])
        assert detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED) == []


class TestScope:
    def test_unreachable_vm_skipped(self) -> None:
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1", reachable=False,
            disk_growth_alerts=[_disk_alert()],
        )])
        assert detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED) == []

    def test_quiet_probe_yields_nothing(self) -> None:
        report = _digest([ProbeVMResult(vm_id="web-01", hostname="10.0.0.1")])
        assert detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED) == []


class TestFileProposals:
    @pytest.mark.asyncio
    async def test_files_with_audit_and_dedup(self) -> None:
        store = ProposalStore(make_test_db())
        await store.initialize()
        audit = AsyncMock()
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)

        created, refreshed, suppressed, stored = await file_proposals(
            proposals, store=store, audit_store=audit,
        )
        assert (created, refreshed, suppressed) == (1, 0, 0)
        assert len(stored) == 1
        assert stored[0].vm_id == "web-01"
        event = audit.log_event.await_args_list[0].args[0]
        assert event.event_type == EventType.PROPOSAL_CREATED
        assert event.vm_id == "web-01"
        assert event.action_type == "disk_cleanup"

        # Same detection on the next probe → refresh, not duplicate
        created, refreshed, suppressed, stored2 = await file_proposals(
            proposals, store=store, audit_store=audit,
        )
        assert (created, refreshed, suppressed) == (0, 1, 0)
        assert stored2[0].proposal_id == stored[0].proposal_id  # same open row
        event = audit.log_event.await_args_list[1].args[0]
        assert event.event_type == EventType.PROPOSAL_REFRESHED
        assert await store.count_pending() == 1


def _action_candidate() -> AgentProposal:
    return AgentProposal(
        env_name="prod", vm_id="web-01", kind=ProposalKind.ACTION,
        action_type="disk_cleanup", signal_kind="disk_growth",
    )


def _review_candidate() -> AgentProposal:
    return AgentProposal(
        env_name="prod", vm_id="web-01", kind=ProposalKind.REVIEW,
        signal_kind="drift",
    )


class TestSuppression:
    """fable-plan Phase 4 — suppression integration at the filing layer."""

    @pytest.mark.asyncio
    async def test_suppressed_proposal_not_written_and_audited(self) -> None:
        store = ProposalStore(make_test_db())
        await store.initialize()
        audit = AsyncMock()

        # Reject the same pair twice via the normal flow — a fresh
        # AgentProposal each iteration, matching real detector behavior
        # (each probe run builds new candidate objects).
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_action_candidate())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")

        result, outcome = await file_or_suppress_one(
            _action_candidate(), store=store, audit_store=audit,
            suppression_threshold=2, suppression_window_days=14,
        )
        assert (result, outcome) == (None, "suppressed")
        assert await store.count_pending() == 0

        suppressed_event = audit.log_event.await_args_list[-1].args[0]
        assert suppressed_event.event_type == EventType.PROPOSAL_SUPPRESSED
        assert suppressed_event.vm_id == "web-01"
        assert "rejected 2x" in suppressed_event.detail

    @pytest.mark.asyncio
    async def test_review_proposals_never_suppressed(self) -> None:
        """Suppression scope is ACTION-kind only per the settings docstring."""
        store = ProposalStore(make_test_db())
        await store.initialize()
        audit = AsyncMock()

        # Reject the review proposal twice.
        for _ in range(2):
            stored, _ = await store.create_or_refresh(_review_candidate())
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")

        result, outcome = await file_or_suppress_one(
            _review_candidate(), store=store, audit_store=audit,
            suppression_threshold=2, suppression_window_days=14,
        )
        assert outcome == "created"
        assert result is not None

    @pytest.mark.asyncio
    async def test_file_proposals_reports_suppressed_count(self) -> None:
        store = ProposalStore(make_test_db())
        await store.initialize()
        audit = AsyncMock()

        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])

        # Reject the disk_cleanup pair twice before the detector runs again
        # — a fresh detect_proposals() call each time, matching how the real
        # probe path re-derives candidates on every run.
        for _ in range(2):
            stored, _ = await store.create_or_refresh(
                detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)[0],
            )
            await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")

        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)
        created, refreshed, suppressed, stored_list = await file_proposals(
            proposals, store=store, audit_store=audit,
            suppression_threshold=2, suppression_window_days=14,
        )
        assert suppressed >= 1
        assert all(p.action_type != "disk_cleanup" for p in stored_list)

    @pytest.mark.asyncio
    async def test_refresh_still_works_under_default_suppression_args(self) -> None:
        """file_proposals' suppression kwargs default to (2, 14) — a single
        rejection must not block the next detection cycle's refresh."""
        store = ProposalStore(make_test_db())
        await store.initialize()
        audit = AsyncMock()
        report = _digest([ProbeVMResult(
            vm_id="web-01", hostname="10.0.0.1",
            disk_growth_alerts=[_disk_alert()],
        )])
        proposals = detect_proposals(report, enabled_actions_by_vm=_ALL_ENABLED)

        created, _, _, _ = await file_proposals(proposals, store=store, audit_store=audit)
        assert created == 1
