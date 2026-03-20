"""Audit logging for all agent actions.

Every action is logged to the audit trail BEFORE and AFTER execution.
Audit events are immutable — written to SQLite (v1) / PostgreSQL (v2).

The audit trail answers: what happened, when, to which VM, by which batch,
and what was the outcome.
"""

from __future__ import annotations

from automaint.models.events import AuditEvent


async def log_event(event: AuditEvent) -> None:
    """Write an audit event to the persistent store.

    Args:
        event: The audit event to record.
    """
    raise NotImplementedError("Audit logging not yet implemented")


async def get_events(
    batch_id: str | None = None,
    vm_id: str | None = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Query audit events with optional filters.

    Args:
        batch_id: Filter by batch run.
        vm_id: Filter by VM.
        limit: Maximum events to return.

    Returns:
        List of matching audit events, most recent first.
    """
    raise NotImplementedError("Audit query not yet implemented")
