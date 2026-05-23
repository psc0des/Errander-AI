"""Tests for ContextBudgeter — Phase 3 context budget caps."""

from __future__ import annotations

from datetime import UTC, datetime

from errander.models.analysis import FleetContext, VMSignalSummary
from errander.safety.context_budget import BudgetStats, ContextBudgeter
from errander.safety.vm_facts import ActionOutcomeFact


def _vm(vm_id: str = "dev/web-01", **kwargs: object) -> VMSignalSummary:
    defaults: dict[str, object] = {"vm_id": vm_id, "hostname": "10.0.0.1"}
    defaults.update(kwargs)
    return VMSignalSummary(**defaults)  # type: ignore[arg-type]


def _context(vms: list[VMSignalSummary] | None = None, **kwargs: object) -> FleetContext:
    return FleetContext(
        env_name="dev",
        vm_summaries=vms or [_vm()],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=0,
        **kwargs,  # type: ignore[arg-type]
    )


def _fact(
    vm_id: str = "dev/web-01",
    action_type: str = "patching",
    last_failure_reason: str | None = None,
) -> ActionOutcomeFact:
    return ActionOutcomeFact(
        vm_id=vm_id,
        action_type=action_type,
        success_rate=1.0,
        sample_size=5,
        last_failure_reason=last_failure_reason,
        last_success_at=datetime.now(tz=UTC),
    )


class TestContextBudgeter:
    # -----------------------------------------------------------------------
    # VM capping
    # -----------------------------------------------------------------------

    def test_vms_within_limit_all_kept(self) -> None:
        b = ContextBudgeter(max_vms=5)
        ctx = _context(vms=[_vm(f"vm-{i}") for i in range(3)])
        capped, stats = b.apply(ctx)
        assert len(capped.vm_summaries) == 3
        assert stats.vms_dropped == 0
        assert stats.vms_included == 3

    def test_vms_over_limit_excess_dropped(self) -> None:
        b = ContextBudgeter(max_vms=3)
        vms = [_vm(f"vm-{i}") for i in range(5)]
        ctx = _context(vms=vms)
        capped, stats = b.apply(ctx)
        assert len(capped.vm_summaries) == 3
        assert stats.vms_dropped == 2
        # First 3 VMs are retained in order
        assert [v.vm_id for v in capped.vm_summaries] == ["vm-0", "vm-1", "vm-2"]

    def test_vms_at_exact_limit_none_dropped(self) -> None:
        b = ContextBudgeter(max_vms=4)
        ctx = _context(vms=[_vm(f"vm-{i}") for i in range(4)])
        _, stats = b.apply(ctx)
        assert stats.vms_dropped == 0

    # -----------------------------------------------------------------------
    # Log entry capping
    # -----------------------------------------------------------------------

    def test_elk_errors_capped(self) -> None:
        b = ContextBudgeter(max_log_entries_per_vm=3)
        vm = _vm(elk_errors=["e1", "e2", "e3", "e4", "e5"])
        ctx = _context(vms=[vm])
        capped, stats = b.apply(ctx)
        assert capped.vm_summaries[0].elk_errors == ["e1", "e2", "e3"]
        assert stats.entries_truncated == 2

    def test_journal_errors_capped(self) -> None:
        b = ContextBudgeter(max_log_entries_per_vm=2)
        vm = _vm(journal_errors=["j1", "j2", "j3"])
        ctx = _context(vms=[vm])
        capped, stats = b.apply(ctx)
        assert len(capped.vm_summaries[0].journal_errors) == 2
        assert stats.entries_truncated == 1

    def test_multiple_list_fields_capped(self) -> None:
        b = ContextBudgeter(max_log_entries_per_vm=1)
        vm = _vm(
            elk_errors=["e1", "e2"],
            journal_errors=["j1", "j2"],
            prometheus_metrics=["m1", "m2"],
        )
        ctx = _context(vms=[vm])
        _, stats = b.apply(ctx)
        assert stats.entries_truncated == 3  # 1 dropped from each of the 3 fields

    def test_entries_within_limit_not_truncated(self) -> None:
        b = ContextBudgeter(max_log_entries_per_vm=5)
        vm = _vm(elk_errors=["e1", "e2"])
        ctx = _context(vms=[vm])
        capped, stats = b.apply(ctx)
        assert capped.vm_summaries[0].elk_errors == ["e1", "e2"]
        assert stats.entries_truncated == 0

    # -----------------------------------------------------------------------
    # Field length capping (action outcome last_failure_reason)
    # -----------------------------------------------------------------------

    def test_long_last_failure_reason_truncated(self) -> None:
        b = ContextBudgeter(max_chars_per_field=20)
        fact = _fact(last_failure_reason="x" * 50)
        ctx = _context(action_outcomes=[fact])
        capped, stats = b.apply(ctx)
        reason = capped.action_outcomes[0].last_failure_reason
        assert reason is not None
        assert len(reason) <= 22  # 20 chars + "…" (1 char, but could be multi-byte)
        assert reason.endswith("…")
        assert stats.fields_truncated == 1

    def test_short_last_failure_reason_not_truncated(self) -> None:
        b = ContextBudgeter(max_chars_per_field=200)
        fact = _fact(last_failure_reason="dpkg lock held")
        ctx = _context(action_outcomes=[fact])
        capped, stats = b.apply(ctx)
        assert capped.action_outcomes[0].last_failure_reason == "dpkg lock held"
        assert stats.fields_truncated == 0

    def test_none_last_failure_reason_unchanged(self) -> None:
        b = ContextBudgeter(max_chars_per_field=10)
        fact = _fact(last_failure_reason=None)
        ctx = _context(action_outcomes=[fact])
        capped, _ = b.apply(ctx)
        assert capped.action_outcomes[0].last_failure_reason is None

    # -----------------------------------------------------------------------
    # Immutability — original context must not be mutated
    # -----------------------------------------------------------------------

    def test_original_context_unchanged(self) -> None:
        b = ContextBudgeter(max_vms=1, max_log_entries_per_vm=1)
        vms = [_vm(f"vm-{i}", elk_errors=["e1", "e2", "e3"]) for i in range(3)]
        ctx = _context(vms=vms)
        original_vm_count = len(ctx.vm_summaries)
        original_elk_count = len(ctx.vm_summaries[0].elk_errors)

        b.apply(ctx)  # must not mutate ctx

        assert len(ctx.vm_summaries) == original_vm_count
        assert len(ctx.vm_summaries[0].elk_errors) == original_elk_count

    # -----------------------------------------------------------------------
    # Stats accuracy
    # -----------------------------------------------------------------------

    def test_stats_dataclass_fields(self) -> None:
        stats = BudgetStats(vms_included=3, vms_dropped=2, fields_truncated=1, entries_truncated=4)
        assert stats.vms_included == 3
        assert stats.vms_dropped == 2
        assert stats.fields_truncated == 1
        assert stats.entries_truncated == 4

    def test_empty_context_produces_zero_stats(self) -> None:
        b = ContextBudgeter()
        ctx = _context(vms=[])
        _, stats = b.apply(ctx)
        assert stats.vms_dropped == 0
        assert stats.entries_truncated == 0
        assert stats.fields_truncated == 0
