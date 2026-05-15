"""Audit event models.

Every action the agent takes — before and after execution — is logged
as an AuditEvent. These form the immutable audit trail stored in
SQLite (v1) / PostgreSQL (v2).
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
