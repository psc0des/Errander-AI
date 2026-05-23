"""LLM context budget — caps VM count and field lengths before prompt assembly (Phase 3).

Prevents oversized prompts from exceeding model context windows and keeps
token cost predictable.  Returns a shallow-copy of FleetContext with the
caps applied — never mutates the original.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.models.analysis import FleetContext, VMSignalSummary

_DEFAULT_MAX_VMS = 20
_DEFAULT_MAX_CHARS_PER_FIELD = 500
_DEFAULT_MAX_LOG_ENTRIES_PER_VM = 5

# VMSignalSummary list fields that carry per-VM log/error entries
_VM_LIST_FIELDS = (
    "disk_alerts",
    "drift_kinds",
    "last_action_types",
    "prometheus_metrics",
    "elk_errors",
    "journal_errors",
    "failed_services",
)


@dataclass
class BudgetStats:
    """What the budgeter capped or dropped from the original FleetContext."""

    vms_included: int
    vms_dropped: int
    fields_truncated: int
    entries_truncated: int


class ContextBudgeter:
    """Caps FleetContext dimensions before it is rendered into an LLM prompt.

    Drops excess VMs (keeping the first ``max_vms`` in the existing ordering),
    truncates long list fields per VM, and truncates long text fields on
    ActionOutcomeFacts.  Never modifies the original context.

    Usage::

        budgeter = ContextBudgeter()
        capped_context, stats = budgeter.apply(context)
        if stats.vms_dropped:
            logger.info("Budget: dropped %d VMs from prompt", stats.vms_dropped)
    """

    def __init__(
        self,
        *,
        max_vms: int = _DEFAULT_MAX_VMS,
        max_chars_per_field: int = _DEFAULT_MAX_CHARS_PER_FIELD,
        max_log_entries_per_vm: int = _DEFAULT_MAX_LOG_ENTRIES_PER_VM,
    ) -> None:
        self._max_vms = max_vms
        self._max_chars = max_chars_per_field
        self._max_log = max_log_entries_per_vm

    def apply(self, context: FleetContext) -> tuple[FleetContext, BudgetStats]:
        """Return a budget-capped copy of context and stats on what was dropped."""

        original_count = len(context.vm_summaries)
        raw_vms = context.vm_summaries[:self._max_vms]
        vms_dropped = original_count - len(raw_vms)

        fields_truncated = 0
        entries_truncated = 0

        capped_vms: list[VMSignalSummary] = []
        for vm in raw_vms:
            overrides: dict[str, list[str]] = {}
            for attr in _VM_LIST_FIELDS:
                entries: list[str] = getattr(vm, attr)
                if len(entries) > self._max_log:
                    overrides[attr] = entries[: self._max_log]
                    entries_truncated += len(entries) - self._max_log
            capped_vms.append(replace(vm, **overrides) if overrides else vm)  # type: ignore[arg-type]

        capped_outcomes = []
        for fact in context.action_outcomes:
            if fact.last_failure_reason and len(fact.last_failure_reason) > self._max_chars:
                capped_outcomes.append(
                    fact.model_copy(
                        update={"last_failure_reason": fact.last_failure_reason[: self._max_chars] + "…"}
                    )
                )
                fields_truncated += 1
            else:
                capped_outcomes.append(fact)

        capped_context = replace(
            context,
            vm_summaries=capped_vms,
            action_outcomes=capped_outcomes,
        )
        return capped_context, BudgetStats(
            vms_included=len(capped_vms),
            vms_dropped=vms_dropped,
            fields_truncated=fields_truncated,
            entries_truncated=entries_truncated,
        )
