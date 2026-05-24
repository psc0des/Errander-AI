"""Layer A analysis models — Operator Assistant output types.

These are read-only data structures produced by the OperatorAssistant.
They are never used to drive execution decisions in Layer B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from errander.safety.vm_facts import (
        ActionOutcomeFact,
        ActionRejectionFact,
        VMRebootPatternFact,
    )


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
