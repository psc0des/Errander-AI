"""Tests for OperatorAssistant fact integration (Phase B2).

Verifies that:
- VMFactsStore facts are included in FleetContext when the store is provided
- Facts appear in the LLM prompt
- The fallback response surfaces low-success-rate and frequently-rejected facts
- When vm_facts_store is None, _build_context continues to work normally
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from errander.agent.operator_assistant import (
    OperatorAssistant,
    _fallback_response,
    _format_prompt,
)
from errander.models.analysis import FleetContext, VMSignalSummary
from errander.safety.vm_facts import (
    ActionOutcomeFact,
    ActionRejectionFact,
    VMRebootPatternFact,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inventory(vm_names: list[str] | None = None) -> MagicMock:
    inv = MagicMock()
    names = vm_names or ["vm1"]
    env = MagicMock()
    env.targets = [MagicMock(name=n, host=f"10.0.0.{i}") for i, n in enumerate(names)]
    inv.environments = {"prod": env}
    return inv


def _empty_audit_store() -> MagicMock:
    store = MagicMock()
    store.get_events = AsyncMock(return_value=[])
    store.get_recent_batches = AsyncMock(return_value=[])
    return store


def _empty_disk_store() -> MagicMock:
    store = MagicMock()
    store.get_distinct_mountpoints = AsyncMock(return_value=[])
    return store


def _empty_baseline_store() -> MagicMock:
    return MagicMock()


def _make_vm_facts_store(
    outcomes: list[ActionOutcomeFact] | None = None,
    reboot_pattern: VMRebootPatternFact | None = None,
    rejections: list[ActionRejectionFact] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.action_outcomes = AsyncMock(return_value=outcomes or [])
    store.reboot_pattern = AsyncMock(return_value=reboot_pattern)
    store.rejection_facts = AsyncMock(return_value=rejections or [])
    return store


def _context_with_facts(
    outcomes: list[ActionOutcomeFact] | None = None,
    reboot_patterns: list[VMRebootPatternFact] | None = None,
    rejections: list[ActionRejectionFact] | None = None,
) -> FleetContext:
    return FleetContext(
        env_name="prod",
        vm_summaries=[VMSignalSummary(vm_id="vm1", hostname="10.0.0.1")],
        recent_batch_count=2,
        last_batch_at="2026-05-18T02:00:00",
        total_failures_7d=0,
        action_outcomes=outcomes or [],
        reboot_patterns=reboot_patterns or [],
        frequently_rejected_actions=rejections or [],
    )


# ---------------------------------------------------------------------------
# Tests: _build_context with vm_facts_store
# ---------------------------------------------------------------------------


class TestBuildContextWithFacts:
    async def test_no_facts_store_context_succeeds(self) -> None:
        assistant = OperatorAssistant()
        ctx = await assistant._build_context(
            audit_store=_empty_audit_store(),
            disk_history_store=_empty_disk_store(),
            baseline_store=_empty_baseline_store(),
            inventory=_make_inventory(),
            env_name="prod",
            vm_facts_store=None,
        )
        assert ctx.action_outcomes == []
        assert ctx.reboot_patterns == []
        assert ctx.frequently_rejected_actions == []

    async def test_facts_store_outcomes_included_in_context(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.7,
            sample_size=10,
            last_failure_reason="dpkg lock",
            last_success_at=None,
        )
        store = _make_vm_facts_store(outcomes=[fact])
        assistant = OperatorAssistant()
        ctx = await assistant._build_context(
            audit_store=_empty_audit_store(),
            disk_history_store=_empty_disk_store(),
            baseline_store=_empty_baseline_store(),
            inventory=_make_inventory(["vm1"]),
            env_name="prod",
            vm_facts_store=store,
        )
        assert len(ctx.action_outcomes) == 1
        assert ctx.action_outcomes[0].action_type == "patching"
        assert "vm_facts" in ctx.sources_used

    async def test_reboot_pattern_included_in_context(self) -> None:
        rp = VMRebootPatternFact(vm_id="vm1", reboots_required_after_patching=3, sample_size=5)
        store = _make_vm_facts_store(reboot_pattern=rp)
        assistant = OperatorAssistant()
        ctx = await assistant._build_context(
            audit_store=_empty_audit_store(),
            disk_history_store=_empty_disk_store(),
            baseline_store=_empty_baseline_store(),
            inventory=_make_inventory(["vm1"]),
            env_name="prod",
            vm_facts_store=store,
        )
        assert len(ctx.reboot_patterns) == 1
        assert ctx.reboot_patterns[0].reboots_required_after_patching == 3

    async def test_rejection_facts_included_in_context(self) -> None:
        rf = ActionRejectionFact(
            action_type="patching",
            rejections_last_90d=4,
            rejection_reasons=["maintenance freeze"],
        )
        store = _make_vm_facts_store(rejections=[rf])
        assistant = OperatorAssistant()
        ctx = await assistant._build_context(
            audit_store=_empty_audit_store(),
            disk_history_store=_empty_disk_store(),
            baseline_store=_empty_baseline_store(),
            inventory=_make_inventory(["vm1"]),
            env_name="prod",
            vm_facts_store=store,
        )
        assert len(ctx.frequently_rejected_actions) == 1
        assert ctx.frequently_rejected_actions[0].action_type == "patching"

    async def test_facts_store_failure_does_not_propagate(self) -> None:
        store = MagicMock()
        store.action_outcomes = AsyncMock(side_effect=RuntimeError("db error"))
        assistant = OperatorAssistant()
        ctx = await assistant._build_context(
            audit_store=_empty_audit_store(),
            disk_history_store=_empty_disk_store(),
            baseline_store=_empty_baseline_store(),
            inventory=_make_inventory(["vm1"]),
            env_name="prod",
            vm_facts_store=store,
        )
        assert ctx.action_outcomes == []


# ---------------------------------------------------------------------------
# Tests: _format_prompt includes facts
# ---------------------------------------------------------------------------


class TestFormatPromptWithFacts:
    def test_prompt_includes_action_outcomes(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.6,
            sample_size=10,
            last_failure_reason="dpkg lock held",
            last_success_at=None,
        )
        ctx = _context_with_facts(outcomes=[fact])
        prompt = _format_prompt("Is vm1 safe to patch?", ctx)
        assert "Operational history facts" in prompt
        assert "patching" in prompt
        assert "60%" in prompt
        assert "dpkg lock held" in prompt

    def test_prompt_includes_reboot_patterns(self) -> None:
        rp = VMRebootPatternFact(
            vm_id="vm1", reboots_required_after_patching=2, sample_size=5,
        )
        ctx = _context_with_facts(reboot_patterns=[rp])
        prompt = _format_prompt("Is vm1 safe to patch?", ctx)
        assert "Reboot patterns" in prompt
        assert "vm1" in prompt
        assert "2 reboots" in prompt

    def test_prompt_includes_rejection_facts(self) -> None:
        rf = ActionRejectionFact(
            action_type="patching",
            rejections_last_90d=3,
            rejection_reasons=["maintenance freeze"],
        )
        ctx = _context_with_facts(rejections=[rf])
        prompt = _format_prompt("Why is patching being rejected?", ctx)
        assert "Frequently rejected" in prompt
        assert "patching" in prompt
        assert "3 rejection" in prompt
        assert "maintenance freeze" in prompt

    def test_prompt_without_facts_has_no_history_section(self) -> None:
        ctx = _context_with_facts()
        prompt = _format_prompt("What's happening?", ctx)
        assert "Operational history facts" not in prompt


# ---------------------------------------------------------------------------
# Tests: _fallback_response surfaces facts
# ---------------------------------------------------------------------------


class TestFallbackResponseWithFacts:
    def test_low_success_rate_appears_in_findings(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.5,
            sample_size=10,
            last_failure_reason="dpkg lock",
            last_success_at=None,
        )
        ctx = _context_with_facts(outcomes=[fact])
        resp = _fallback_response("Is this fleet healthy?", ctx)
        assert any("50%" in f or "patching" in f for f in resp.findings)

    def test_high_success_rate_not_flagged(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.95,
            sample_size=10,
            last_failure_reason=None,
            last_success_at=None,
        )
        ctx = _context_with_facts(outcomes=[fact])
        resp = _fallback_response("Is this fleet healthy?", ctx)
        findings_text = " ".join(resp.findings)
        assert "vm1 patching" not in findings_text

    def test_small_sample_not_flagged_even_if_low_rate(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.0,
            sample_size=2,
            last_failure_reason="disk full",
            last_success_at=None,
        )
        ctx = _context_with_facts(outcomes=[fact])
        resp = _fallback_response("Is this fleet healthy?", ctx)
        findings_text = " ".join(resp.findings)
        assert "vm1 patching" not in findings_text

    def test_frequently_rejected_actions_appear_in_findings(self) -> None:
        rf = ActionRejectionFact(
            action_type="patching",
            rejections_last_90d=5,
            rejection_reasons=["too risky"],
        )
        ctx = _context_with_facts(rejections=[rf])
        resp = _fallback_response("Why is patching being rejected?", ctx)
        assert any("patching" in f and "rejected" in f for f in resp.findings)

    def test_risk_elevated_when_facts_flagged(self) -> None:
        fact = ActionOutcomeFact(
            vm_id="vm1",
            action_type="patching",
            success_rate=0.3,
            sample_size=10,
            last_failure_reason="crash",
            last_success_at=None,
        )
        ctx = _context_with_facts(outcomes=[fact])
        resp = _fallback_response("Status?", ctx)
        assert resp.risk_level == "high"
