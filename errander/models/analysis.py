"""Layer A analysis models — Operator Assistant output types.

These are read-only data structures produced by the OperatorAssistant.
They are never used to drive execution decisions in Layer B.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class AssistantResponse(BaseModel):
    """Structured LLM response from the Operator Assistant (Layer A)."""

    summary: str
    findings: list[str]
    recommendations: list[str]
    risk_level: str  # "low" | "medium" | "high" | "unknown"


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


@dataclass
class FleetContext:
    """Structured context assembled from stores before the LLM call."""

    env_name: str | None
    vm_summaries: list[VMSignalSummary]
    recent_batch_count: int
    last_batch_at: str | None
    total_failures_7d: int
