"""Layer A analysis models — Operator Assistant output types.

These are read-only data structures produced by the OperatorAssistant.
They are never used to drive execution decisions in Layer B.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

from errander.models.proposals import PROPOSABLE_ACTIONS

if TYPE_CHECKING:
    from errander.safety.vm_facts import (
        ActionOutcomeFact,
        ActionRejectionFact,
        VMRebootPatternFact,
    )

_VM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class ProposedWorkItem(BaseModel):
    """An actionable item the investigation agent proposes (fable-plan Phase 2).

    The agent's *only* channel to origination: it may propose that a specific
    LOW-risk action run on a specific VM, with a rationale. Validated here
    (action in the proposable set, vm_id well-formed) and again against live
    inventory when converted to an AgentProposal. Anything else is dropped.
    """

    vm_id: str
    action_type: str
    rationale: str

    @field_validator("vm_id")
    @classmethod
    def _valid_vm(cls, v: str) -> str:
        if not _VM_ID_RE.match(v):
            raise ValueError(f"invalid vm_id: {v!r}")
        return v

    @field_validator("action_type")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in PROPOSABLE_ACTIONS:
            raise ValueError(
                f"action_type {v!r} not in proposable set {sorted(PROPOSABLE_ACTIONS)}"
            )
        return v


class Finding(BaseModel):
    """A single operator-assistant finding with optional source evidence.

    evidence is a list of source IDs (e.g. "audit_store", "vm_facts:prod/vm1:patching").
    An empty evidence list means the finding is uncited.
    """

    text: str
    evidence: list[str] = []

    @property
    def is_cited(self) -> bool:
        """True when the finding cites at least one data source."""
        return bool(self.evidence)


class AssistantResponse(BaseModel):
    """Structured LLM response from the Operator Assistant (Layer A)."""

    summary: str
    findings: list[Finding]
    recommendations: list[str]
    risk_level: str  # "low" | "medium" | "high" | "unknown"
    data_sources: list[str] = []
    #: Actionable proposals the agentic path may emit (Phase 2). Empty for the
    #: deterministic investigate() path. Invalid items are dropped, not raised.
    proposed_work: list[ProposedWorkItem] = []

    @field_validator("findings", mode="before")
    @classmethod
    def _coerce_findings(cls, v: object) -> list[object]:
        """Accept bare strings as well as Finding dicts for backward compatibility."""
        if not isinstance(v, list):
            return v  # type: ignore[return-value]
        return [{"text": item} if isinstance(item, str) else item for item in v]


@dataclass
class VMSignalSummary:
    """Aggregated signal data for one VM, assembled from existing stores."""

    vm_id: str
    hostname: str
    recent_failure_count: int = 0
    disk_alerts: list[str] = field(default_factory=list)
    drift_kinds: list[str] = field(default_factory=list)
    failed_login_count: int = 0
    last_action_types: list[str] = field(default_factory=list)
    prometheus_metrics: list[str] = field(default_factory=list)
    elk_errors: list[str] = field(default_factory=list)
    journal_errors: list[str] = field(default_factory=list)
    failed_services: list[str] = field(default_factory=list)


@dataclass
class FleetContext:
    """Structured context assembled from stores before the LLM call."""

    env_name: str | None
    vm_summaries: list[VMSignalSummary]
    recent_batch_count: int
    last_batch_at: str | None
    total_failures_7d: int
    sources_used: list[str] = field(default_factory=list)
    action_outcomes: list[ActionOutcomeFact] = field(default_factory=list)
    reboot_patterns: list[VMRebootPatternFact] = field(default_factory=list)
    frequently_rejected_actions: list[ActionRejectionFact] = field(default_factory=list)
