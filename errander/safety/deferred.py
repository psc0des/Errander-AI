"""Deferred execution store.

When a dry-run batch is approved outside the maintenance window, the approval
is persisted here. The window-opener scheduler job picks it up at the next
window start and triggers a live run_env_batch().

Table lives in the same SQLite file as the audit trail (one DB, two tables).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)

#: Days after window_start before a pending record is auto-expired.
_EXPIRY_DAYS = 7

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS deferred_executions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     TEXT    NOT NULL UNIQUE,
    env_name     TEXT    NOT NULL,
    approved_at  TEXT    NOT NULL,
    approved_by  TEXT,
    window_start TEXT    NOT NULL,
    expiry_at    TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    created_at   TEXT    NOT NULL,
    executed_at  TEXT
)
"""

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_deferred_env_status ON deferred_executions (env_name, status)",
    "CREATE INDEX IF NOT EXISTS idx_deferred_window     ON deferred_executions (window_start)",
]

_UPSERT_SQL = """
INSERT INTO deferred_executions
    (batch_id, env_name, approved_at, approved_by, window_start, expiry_at, status, created_at)
VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
ON CONFLICT(batch_id) DO UPDATE SET
    approved_at  = excluded.approved_at,
    approved_by  = excluded.approved_by,
    window_start = excluded.window_start,
    expiry_at    = excluded.expiry_at,
    status       = 'pending',
    executed_at  = NULL
"""


@dataclass
class DeferredExecution:
    """A pending deferred execution record."""

    batch_id: str
    env_name: str
    approved_at: datetime
    approved_by: str | None
    window_start: datetime
    expiry_at: datetime
    status: str
    created_at: datetime
    executed_at: datetime | None


class DeferredExecutionStore:
    """Async SQLite store for deferred execution records.

    Shares the same DB file as AuditStore — initialise with the same db_path.

    Usage::

        store = DeferredExecutionStore("errander.sqlite")
        await store.initialize()
        try:
            await store.save(batch_id, env_name, approved_by, window_start)
            pending = await store.get_pending("production")
        finally:
            await store.close()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the DB connection and create the table if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE_SQL)
        for idx_sql in _CREATE_INDEX_SQL:
            await self._db.execute(idx_sql)
        await self._db.commit()

    async def save(
        self,
        batch_id: str,
        env_name: str,
        approved_by: str | None,
        window_start: datetime,
    ) -> None:
        """Persist a deferred execution approval.

        If a record with the same batch_id already exists it is replaced.
        """
        assert self._db is not None, "Call initialize() first"
        now = datetime.now(tz=timezone.utc)
        expiry_at = window_start + timedelta(days=_EXPIRY_DAYS)
        await self._db.execute(
            _UPSERT_SQL,
            (
                batch_id,
                env_name,
                now.isoformat(),
                approved_by,
                window_start.isoformat(),
                expiry_at.isoformat(),
                now.isoformat(),
            ),
        )
        await self._db.commit()
        logger.info(
            "Deferred execution saved",
            extra={
                "batch_id": batch_id,
                "env_name": env_name,
                "window_start": window_start.isoformat(),
            },
        )

    async def get_pending(self, env_name: str) -> list[DeferredExecution]:
        """Return all non-expired pending records for an environment."""
        assert self._db is not None, "Call initialize() first"
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            SELECT batch_id, env_name, approved_at, approved_by,
                   window_start, expiry_at, status, created_at, executed_at
            FROM   deferred_executions
            WHERE  env_name = ?
              AND  status   = 'pending'
              AND  expiry_at > ?
            ORDER  BY window_start ASC
            """,
            (env_name, now),
        )
        rows = await cursor.fetchall()
        return [_row_to_deferred(row) for row in rows]

    async def mark_executing(self, batch_id: str) -> None:
        """Transition a record from pending → executing."""
        assert self._db is not None, "Call initialize() first"
        await self._db.execute(
            "UPDATE deferred_executions SET status = 'executing' WHERE batch_id = ?",
            (batch_id,),
        )
        await self._db.commit()

    async def mark_done(self, batch_id: str) -> None:
        """Transition a record from executing → done and stamp executed_at."""
        assert self._db is not None, "Call initialize() first"
        now = datetime.now(tz=timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE deferred_executions SET status = 'done', executed_at = ? WHERE batch_id = ?",
            (now, batch_id),
        )
        await self._db.commit()

    async def expire_old(self) -> int:
        """Mark all past-expiry pending records as expired. Returns count expired."""
        assert self._db is not None, "Call initialize() first"
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE deferred_executions SET status = 'expired' WHERE status = 'pending' AND expiry_at <= ?",
            (now,),
        )
        await self._db.commit()
        count: int = cursor.rowcount
        if count:
            logger.info("Expired %d stale deferred execution(s)", count)
        return count

    async def close(self) -> None:
        """Close the DB connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None


def _row_to_deferred(row: aiosqlite.Row) -> DeferredExecution:
    def _dt(s: str | None) -> datetime | None:
        return datetime.fromisoformat(s) if s else None

    return DeferredExecution(
        batch_id=str(row["batch_id"]),
        env_name=str(row["env_name"]),
        approved_at=datetime.fromisoformat(str(row["approved_at"])),
        approved_by=row["approved_by"],
        window_start=datetime.fromisoformat(str(row["window_start"])),
        expiry_at=datetime.fromisoformat(str(row["expiry_at"])),
        status=str(row["status"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        executed_at=_dt(row["executed_at"]),
    )
