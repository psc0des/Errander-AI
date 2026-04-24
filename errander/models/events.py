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
