"""Audit event models.

Every action the agent takes — before and after execution — is logged
as an AuditEvent. These form the immutable audit trail stored in
PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class EventType(StrEnum):
    """Categories of audit events."""

    ACTION_PLANNED = "action_planned"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_COMPLETED = "rollback_completed"
    ROLLBACK_FAILED = "rollback_failed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_TIMEOUT = "approval_timeout"
    BATCH_STARTED = "batch_started"
    BATCH_COMPLETED = "batch_completed"
    DRIFT_BASELINE_SAVED = "drift_baseline_saved"
    DRIFT_DETECTED = "drift_detected"
    SETTINGS_CHANGED = "settings_changed"
    INVENTORY_CHANGED = "inventory_changed"
    EXECUTION_DEFERRED = "execution_deferred"
    DEFERRED_EXECUTION_STARTED = "deferred_execution_started"
    FLEET_ABORT = "fleet_abort"
    OS_MISMATCH = "os_mismatch"
    TARGET_READINESS_BLOCKED = "target_readiness_blocked"
    # SRE signals — Phase 1
    PREFLIGHT_LOCK_DETECTED = "preflight_lock_detected"
    PREFLIGHT_LOCK_CLEAR = "preflight_lock_clear"
    SUDO_PREFLIGHT_FAILED = "sudo_preflight_failed"
    REBOOT_REQUIRED_DETECTED = "reboot_required_detected"
    SERVICE_HEALTH_REGRESSION = "service_health_regression"
    DISK_USAGE_CAPTURED = "disk_usage_captured"
    # SRE signals — Phase 2
    DRIFT_KIND_BASELINE_SAVED = "drift_kind_baseline_saved"
    DRIFT_KIND_CHANGED = "drift_kind_changed"
    FAILED_SSH_LOGINS_OBSERVED = "failed_ssh_logins_observed"
    # Phase B — proactive daily probe
    DAILY_PROBE_STARTED = "daily_probe_started"
    DAILY_PROBE_COMPLETE = "daily_probe_complete"
    DAILY_PROBE_FAILED = "daily_probe_failed"
    # Phase F — LangGraph integration
    DISK_GATE_BLOCKED = "disk_gate_blocked"
    # v1 action opt-in plan
    TARGET_PREFLIGHT_FAILED = "target_preflight_failed"
    # Phase 2 — service_restart module
    SERVICE_RESTART_REQUESTED = "service_restart_requested"
    SERVICE_RESTART_UNIT_NOT_ALLOWED = "service_restart_unit_not_allowed"
    SERVICE_RESTART_APPROVED = "service_restart_approved"
    SERVICE_RESTART_REJECTED = "service_restart_rejected"
    SERVICE_RESTART_EXECUTED = "service_restart_executed"
    SERVICE_RESTART_VERIFY_OK = "service_restart_verify_ok"
    SERVICE_RESTART_VERIFY_FAILED = "service_restart_verify_failed"
    # Project A — workflow durability
    OPERATOR_FORCE_RESUME = "operator_force_resume"
    # v1.1 — docker_hygiene per-object audit (Exact-Object Approval invariant)
    DOCKER_HYGIENE_OBJECT_REMOVED = "docker_hygiene_object_removed"
    DOCKER_HYGIENE_OBJECT_DRIFT_SKIPPED = "docker_hygiene_object_drift_skipped"
    DOCKER_HYGIENE_OBJECT_REMOVE_FAILED = "docker_hygiene_object_remove_failed"
    # R2 — user/group management (web-only approval RBAC)
    USER_CREATED = "user_created"
    USER_DELETED = "user_deleted"
    USER_GROUPS_CHANGED = "user_groups_changed"
    USER_PASSWORD_CHANGED = "user_password_changed"


@dataclass
class AuditEvent:
    """An immutable audit trail entry.

    Attributes:
        event_type: What kind of event occurred.
        batch_id: Batch run this event belongs to.
        vm_id: Target VM (if applicable).
        action_type: Maintenance action (if applicable).
        detail: Human-readable description.
        timestamp: When this event occurred.
        metadata: Additional structured data.
    """

    event_type: EventType
    batch_id: str
    vm_id: str | None = None
    action_type: str | None = None
    detail: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, object] = field(default_factory=dict)
