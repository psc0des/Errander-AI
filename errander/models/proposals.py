"""Agent proposal models (detect-and-propose, fable-plan Phase 1).

An AgentProposal is a *suggestion record*, never an authorization: the
detector (deterministic, no LLM) or the future investigation agent files
one, a named operator decides it in the Web UI, and an approved actionable
proposal is executed by the agent-side proposal reconciler through the
existing deterministic sub-graph path (D1 in tasks/fable-plan.md).

Validation is the guardrail (fable-plan §5.2): identifiers must match a
strict pattern, the action type must come from the fixed proposable set,
and evidence text is length-capped display data that never reaches a shell.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

#: Identifiers (vm_id, env_name, signal kinds) — no shell metacharacters,
#: no path traversal, nothing an attacker-influenced signal could smuggle in.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Actions the deterministic detector may propose in Phase 1. Deliberately a
#: subset of the fixed action set: LOW-risk, whitelist-bounded, categorical-
#: approvable (CLAUDE.md v1 approval coverage decision). Extending this set
#: is a code change with safety review, never a config flag.
PROPOSABLE_ACTIONS: frozenset[str] = frozenset({"disk_cleanup", "log_rotation"})

#: Cap on evidence text — display-only data, bounded to keep rows small.
_EVIDENCE_MAX_CHARS = 500


class ProposalKind(StrEnum):
    """What a decision on this proposal means."""

    #: Approval originates a targeted run of the named action (D1).
    ACTION = "action"
    #: Surfaces evidence for a human to look at — approval acknowledges,
    #: it never executes anything (drift / failed-login signals).
    REVIEW = "review"


class ProposalStatus(StrEnum):
    """Lifecycle states (mirrors the CHECK constraint in migration #16)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SNOOZED = "snoozed"
    EXPIRED = "expired"


class ProposalEvidence(BaseModel):
    """One observation backing a proposal — honest provenance, always."""

    #: Where the observation came from (e.g. "probe:disk_history").
    source: str = Field(max_length=128)
    #: The check or query that produced it (human-readable, not executable).
    check: str = Field(max_length=_EVIDENCE_MAX_CHARS)
    #: What was observed (display-only; HTML-escaped at render time).
    observation: str = Field(max_length=_EVIDENCE_MAX_CHARS)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @field_validator("check", "observation", mode="before")
    @classmethod
    def _truncate(cls, v: object) -> object:
        if isinstance(v, str) and len(v) > _EVIDENCE_MAX_CHARS:
            return v[: _EVIDENCE_MAX_CHARS - 1] + "…"
        return v


class AgentProposal(BaseModel):
    """A durable agent-originated suggestion awaiting a human decision.

    ``action_key`` is the dedup identity: one open proposal per
    (vm_id, action_key) — the detector refreshes evidence on the open row
    instead of duplicating (fable-plan Phase 1 dedup rule).
    """

    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    env_name: str
    vm_id: str
    kind: ProposalKind
    #: For ACTION proposals: the action to run (validated against
    #: PROPOSABLE_ACTIONS). Empty for REVIEW proposals.
    action_type: str = ""
    #: Signal that originated this ("disk_growth" | "drift" | "failed_logins").
    signal_kind: str
    #: Who filed it — "probe_detector" now; "investigation_agent" in Phase 2.
    origin: str = "probe_detector"
    probe_id: str = ""
    evidence: list[ProposalEvidence] = Field(default_factory=list)
    #: "low" | "medium" | "high" — matches the vm_facts confidence vocabulary.
    confidence: str = "medium"
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    expires_at: datetime | None = None
    decided_by: str | None = None
    decided_by_group: str | None = None
    decided_at: datetime | None = None
    snoozed_until: datetime | None = None
    execution_started_at: datetime | None = None
    #: "success" | "failed" | None (not executed / review-only).
    execution_status: str | None = None

    @field_validator("env_name", "vm_id")
    @classmethod
    def _valid_identifier(cls, v: str) -> str:
        if not _IDENTIFIER_RE.match(v):
            raise ValueError(f"invalid identifier: {v!r}")
        return v

    @field_validator("signal_kind", "origin")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if not _IDENTIFIER_RE.match(v):
            raise ValueError(f"invalid kind: {v!r}")
        return v

    @field_validator("action_type")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v and v not in PROPOSABLE_ACTIONS:
            raise ValueError(
                f"action_type {v!r} is not in the proposable action set "
                f"{sorted(PROPOSABLE_ACTIONS)}"
            )
        return v

    @model_validator(mode="after")
    def _kind_action_consistency(self) -> AgentProposal:
        if self.kind == ProposalKind.ACTION and not self.action_type:
            raise ValueError("ACTION proposals require an action_type")
        if self.kind == ProposalKind.REVIEW and self.action_type:
            raise ValueError("REVIEW proposals must not carry an action_type")
        return self

    @property
    def action_key(self) -> str:
        """Dedup identity — one open proposal per (vm_id, action_key)."""
        if self.kind == ProposalKind.ACTION:
            return self.action_type
        return f"review:{self.signal_kind}"

    @property
    def is_actionable(self) -> bool:
        """True when approval originates a targeted execution (D1)."""
        return self.kind == ProposalKind.ACTION and bool(self.action_type)
