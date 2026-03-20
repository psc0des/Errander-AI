"""Audit logging for all agent actions.

Every action is logged to the audit trail BEFORE and AFTER execution.
Audit events are immutable — written to SQLite (v1) / PostgreSQL (v2).

The audit trail answers: what happened, when, to which VM, by which batch,
and what was the outcome.

Design note: Schema uses TEXT types and ISO timestamps for PostgreSQL
migration compatibility. The AuditStore class manages connection lifecycle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from automaint.models.events import AuditEvent, EventType

#: SQL to create the audit_events table.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    vm_id TEXT,
    action_type TEXT,
    detail TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_audit_batch ON audit_events (batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_vm ON audit_events (vm_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events (timestamp DESC)",
]

_INSERT_SQL = """
INSERT INTO audit_events (event_type, batch_id, vm_id, action_type, detail, timestamp, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_SQL = """
SELECT event_type, batch_id, vm_id, action_type, detail, timestamp, metadata
FROM audit_events
"""


class AuditStore:
    """Async SQLite-backed audit event store.

    Usage:
        async with AuditStore("audit.sqlite") as store:
            await store.log_event(event)
            events = await store.get_events(batch_id="run-123")

    For testing, use ":memory:" as the database path.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database and create tables if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_TABLE_SQL)
        for index_sql in _CREATE_INDEX_SQL:
            await self._db.execute(index_sql)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> AuditStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "AuditStore not initialized — call initialize() or use as async context manager"
            raise RuntimeError(msg)
        return self._db

    async def log_event(self, event: AuditEvent) -> None:
        """Write an audit event to the persistent store.

        Args:
            event: The audit event to record.
        """
        db = self._ensure_connected()
        metadata_json = json.dumps(
            event.metadata, default=str, ensure_ascii=False,
        )
        timestamp_iso = event.timestamp.isoformat()

        await db.execute(
            _INSERT_SQL,
            (
                event.event_type.value,
                event.batch_id,
                event.vm_id,
                event.action_type,
                event.detail,
                timestamp_iso,
                metadata_json,
            ),
        )
        await db.commit()

    async def get_events(
        self,
        batch_id: str | None = None,
        vm_id: str | None = None,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events with optional filters.

        Args:
            batch_id: Filter by batch run.
            vm_id: Filter by VM.
            event_type: Filter by event type.
            limit: Maximum events to return.

        Returns:
            List of matching audit events, most recent first.
        """
        db = self._ensure_connected()

        clauses: list[str] = []
        params: list[str | int] = []

        if batch_id is not None:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if vm_id is not None:
            clauses.append("vm_id = ?")
            params.append(vm_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)

        query = _SELECT_SQL
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC, id DESC"
        query += " LIMIT ?"
        params.append(limit)

        rows = await db.execute_fetchall(query, params)
        return [_row_to_event(row) for row in rows]

    async def count_events(
        self,
        batch_id: str | None = None,
        vm_id: str | None = None,
    ) -> int:
        """Count audit events matching filters.

        Args:
            batch_id: Filter by batch run.
            vm_id: Filter by VM.

        Returns:
            Number of matching events.
        """
        db = self._ensure_connected()

        clauses: list[str] = []
        params: list[str] = []

        if batch_id is not None:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if vm_id is not None:
            clauses.append("vm_id = ?")
            params.append(vm_id)

        query = "SELECT COUNT(*) FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return row[0] if row else 0  # type: ignore[index]


def _row_to_event(row: tuple[object, ...]) -> AuditEvent:
    """Convert a database row to an AuditEvent."""
    return AuditEvent(
        event_type=EventType(str(row[0])),
        batch_id=str(row[1]),
        vm_id=str(row[2]) if row[2] is not None else None,
        action_type=str(row[3]) if row[3] is not None else None,
        detail=str(row[4]),
        timestamp=datetime.fromisoformat(str(row[5])),
        metadata=json.loads(str(row[6])),
    )
