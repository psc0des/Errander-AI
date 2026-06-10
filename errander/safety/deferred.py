"""Deferred execution store.

When a dry-run batch is approved outside the maintenance window, the approval
is persisted here. The window-opener scheduler job picks it up at the next
window start and triggers a live run_env_batch().

Table is created by migration 0012 in errander/safety/migrations.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)

#: Days after window_start before a pending record is auto-expired.
_EXPIRY_DAYS = 7

_UPSERT_SQL = """
INSERT INTO deferred_executions
    (batch_id, env_name, approved_at, approved_by, window_start, expiry_at, status, created_at,
     plan_json, plan_hash)
VALUES (:batch_id, :env_name, :approved_at, :approved_by, :window_start, :expiry_at,
        'pending', :created_at, :plan_json, :plan_hash)
ON CONFLICT(batch_id) DO UPDATE SET
    approved_at  = EXCLUDED.approved_at,
    approved_by  = EXCLUDED.approved_by,
    window_start = EXCLUDED.window_start,
    expiry_at    = EXCLUDED.expiry_at,
    status       = 'pending',
    executed_at  = NULL,
    plan_json    = EXCLUDED.plan_json,
    plan_hash    = EXCLUDED.plan_hash
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
    plan_json: str | None = None
    plan_hash: str | None = None


class DeferredExecutionStore:
    """Async database store for deferred execution records.

    Shares the same DB as AuditStore — pass the same AsyncDatabase instance.
    The deferred_executions table is created by migration #12.

    Usage::

        store = DeferredExecutionStore(db)
        await store.initialize()
        await store.save(batch_id, env_name, approved_by, window_start)
        pending = await store.get_pending("production")
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn, self._db.dialect)

    async def close(self) -> None:
        await self._db.close()

    async def save(
        self,
        batch_id: str,
        env_name: str,
        approved_by: str | None,
        window_start: datetime,
        plan_json: str | None = None,
        plan_hash: str | None = None,
    ) -> None:
        """Persist a deferred execution approval.

        If a record with the same batch_id already exists it is replaced.
        """
        now = datetime.now(tz=UTC)
        expiry_at = window_start + timedelta(days=_EXPIRY_DAYS)
        async with self._db.begin() as conn:
            await conn.execute(
                text(_UPSERT_SQL),
                {
                    "batch_id": batch_id,
                    "env_name": env_name,
                    "approved_at": now.isoformat(),
                    "approved_by": approved_by,
                    "window_start": window_start.isoformat(),
                    "expiry_at": expiry_at.isoformat(),
                    "created_at": now.isoformat(),
                    "plan_json": plan_json,
                    "plan_hash": plan_hash,
                },
            )
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
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT batch_id, env_name, approved_at, approved_by,
                       window_start, expiry_at, status, created_at, executed_at,
                       plan_json, plan_hash
                FROM   deferred_executions
                WHERE  env_name = :env_name
                  AND  status   = 'pending'
                  AND  expiry_at > :now
                ORDER  BY window_start ASC
                """),
                {"env_name": env_name, "now": now},
            )
            rows = result.mappings().fetchall()
        return [_row_to_deferred(row) for row in rows]

    async def mark_executing(self, batch_id: str) -> None:
        """Transition a record from pending → executing."""
        async with self._db.begin() as conn:
            await conn.execute(
                text("UPDATE deferred_executions SET status = 'executing' WHERE batch_id = :batch_id"),
                {"batch_id": batch_id},
            )

    async def mark_done(self, batch_id: str) -> None:
        """Transition a record from executing → done and stamp executed_at."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE deferred_executions SET status = 'done', executed_at = :ts "
                    "WHERE batch_id = :batch_id"
                ),
                {"ts": now, "batch_id": batch_id},
            )

    async def expire_old(self) -> int:
        """Mark all past-expiry pending records as expired. Returns count expired."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE deferred_executions SET status = 'expired' "
                    "WHERE status = 'pending' AND expiry_at <= :now"
                ),
                {"now": now},
            )
            count: int = result.rowcount
        if count:
            logger.info("Expired %d stale deferred execution(s)", count)
        return count


def _row_to_deferred(row: Any) -> DeferredExecution:
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
        plan_json=row["plan_json"] if row["plan_json"] is not None else None,
        plan_hash=row["plan_hash"] if row["plan_hash"] is not None else None,
    )
