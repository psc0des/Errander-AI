"""Maintenance plan models.

A plan is the output of a dry-run: it captures what the agent *would* do,
and is saved for human approval before live execution. Follows the
Terraform plan/apply pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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
