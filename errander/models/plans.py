"""Maintenance plan models.

A plan is the output of the planning phase: it captures what the agent *would*
do, along with a content-addressed hash for integrity verification. Live
execution requires presenting the same plan_hash that was approved (finding #3).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from errander.models.actions import Action, ActionResult


@dataclass
class VMPlan:
    """Dry-run plan for a single VM.

    Attributes:
        vm_id: Target VM identifier.
        planned_actions: Ordered list of actions to perform.
        dry_run_results: Results from simulating each action.
        created_at: When this plan was generated.
    """

    vm_id: str
    planned_actions: list[Action]
    dry_run_results: list[ActionResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class BatchPlan:
    """Dry-run plan for an entire maintenance batch across multiple VMs.

    Attributes:
        batch_id: Unique identifier for this batch run.
        vm_plans: Per-VM plans.
        created_at: When this batch plan was generated.
        approved: Whether human approval was granted.
        approved_by: Slack user ID of the approver.
    """

    batch_id: str
    vm_plans: list[VMPlan] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    approved: bool | None = None
    approved_by: str | None = None


@dataclass
class ImmutablePlan:
    """Content-addressed plan for a maintenance batch.

    The plan_hash is a SHA-256 digest of the canonical JSON of the plan
    contents. Live execution validates that the executing plan_hash matches
    the hash approved by the operator — any drift between planning and
    execution phases is detected (finding #3).

    Attributes:
        plan_id: Unique identifier for this plan run.
        batch_id: Batch run this plan belongs to.
        env_name: Environment name.
        created_at: When the plan was generated.
        vm_plans: Serialised per-VM planned actions (list of dicts).
        plan_hash: SHA-256 hex digest of canonical JSON.
    """

    plan_id: str
    batch_id: str
    env_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    vm_plans: list[dict[str, object]] = field(default_factory=list)
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        canonical = json.dumps(
            {
                "batch_id": self.batch_id,
                "env_name": self.env_name,
                "vm_plans": self.vm_plans,
            },
            sort_keys=True,
            default=str,
        )
        self.plan_hash = hashlib.sha256(canonical.encode()).hexdigest()

    def short_hash(self) -> str:
        """Return first 12 hex chars of plan_hash for display."""
        return self.plan_hash[:12]
