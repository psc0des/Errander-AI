"""Action types and result models.

Defines the enumeration of maintenance action types, their risk tiers,
and the ActionResult model that captures execution outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class ActionType(StrEnum):
    """Types of maintenance actions the agent can perform."""

    PATCHING = "patching"
    DOCKER_PRUNE = "docker_prune"
    DOCKER_HYGIENE = "docker_hygiene"
    LOG_ROTATION = "log_rotation"
    DISK_CLEANUP = "disk_cleanup"
    BACKUP_VERIFY = "backup_verify"
    SERVICE_RESTART = "service_restart"


class RiskTier(StrEnum):
    """Risk classification for actions. Determines approval requirements.

    Low: automatic execution.
    Medium: log + notify.
    High: human approval required.
    Critical: blocked — never automated.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(StrEnum):
    """Outcome status of an executed action."""

    PENDING = "pending"
    SKIPPED = "skipped"
    DRY_RUN_OK = "dry_run_ok"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    NEEDS_MANUAL = "needs_manual"
    # Pre-flight gate deliberately refused to run — not a failure, not a skip.
    # Operator-visible: audit event ACTION_COMPLETED with status=blocked in metadata.
    BLOCKED = "blocked"


#: Default risk tier mapping per action type.
ACTION_RISK_TIERS: dict[ActionType, RiskTier] = {
    ActionType.DISK_CLEANUP: RiskTier.LOW,
    ActionType.LOG_ROTATION: RiskTier.LOW,
    ActionType.DOCKER_PRUNE: RiskTier.MEDIUM,
    ActionType.DOCKER_HYGIENE: RiskTier.MEDIUM,
    ActionType.PATCHING: RiskTier.MEDIUM,
    ActionType.BACKUP_VERIFY: RiskTier.LOW,
    ActionType.SERVICE_RESTART: RiskTier.HIGH,
}


@dataclass
class Action:
    """A planned maintenance action.

    Attributes:
        action_type: What kind of maintenance to perform.
        risk_tier: Risk classification (determines approval flow).
        params: Action-specific parameters (e.g., packages to patch).
    """

    action_type: ActionType
    risk_tier: RiskTier
    params: dict[str, object] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Result of executing a maintenance action.

    Attributes:
        action_type: What action was attempted.
        status: Outcome status.
        vm_id: Which VM this ran on.
        started_at: When execution began.
        completed_at: When execution finished.
        detail: Human-readable description of what happened.
        error: Error message if failed.
        rollback_detail: Description of rollback if attempted.
    """

    action_type: ActionType
    status: ActionStatus
    vm_id: str
    started_at: datetime
    completed_at: datetime | None = None
    detail: str = ""
    error: str | None = None
    rollback_detail: str | None = None
