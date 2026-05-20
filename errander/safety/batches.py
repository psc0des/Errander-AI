"""BatchStore — persistent lifecycle tracking for maintenance batch runs.

Wraps the ``batches`` SQLite table (migration #5).  All public methods are
async and use the shared aiosqlite connection owned by AuditStore.

Lifecycle:
  insert(batch_id, env, dry_run, vm_count)   → RUNNING row
  update_status(batch_id, COMPLETED, ...)    → terminal update
  get(batch_id)                              → BatchRecord | None
  list_recent(limit)                         → newest-first list
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from errander.models.batches import BatchRecord, BatchStatus

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class BatchStore:
    """Read/write the ``batches`` table.

    Args:
        db: Open aiosqlite connection.  Caller owns the lifecycle.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert(
        self,
        batch_id: str,
        *,
        env_name: str,
        dry_run: bool,
        vm_count: int,
    ) -> None:
        """Insert a new RUNNING batch row.

        Idempotent — INSERT OR IGNORE so a crash-restart that re-calls
        init_batch_node does not duplicate the row.
        """
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            """
            INSERT OR IGNORE INTO batches
                (id, env_name, status, started_at, finished_at, dry_run, vm_count, error)
            VALUES (?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (batch_id, env_name, BatchStatus.RUNNING.value, now, int(dry_run), vm_count),
        )
        await self._db.commit()
        logger.debug("BatchStore: inserted batch %s as RUNNING", batch_id)

    async def update_status(
        self,
        batch_id: str,
        status: BatchStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Transition a batch to a terminal status.

        Sets finished_at to now.  Only updates if the row is still RUNNING
        so a double-call (e.g. from retry on crash) is safe.
        """
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            """
            UPDATE batches
               SET status = ?, finished_at = ?, error = ?
             WHERE id = ? AND status = ?
            """,
            (status.value, now, error, batch_id, BatchStatus.RUNNING.value),
        )
        await self._db.commit()
        logger.debug("BatchStore: batch %s → %s", batch_id, status.value)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, batch_id: str) -> BatchRecord | None:
        """Return the BatchRecord for *batch_id*, or None if not found."""
        cursor = await self._db.execute(
            "SELECT id, env_name, status, started_at, finished_at, dry_run, vm_count, error "
            "FROM batches WHERE id = ?",
            (batch_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_recent(self, limit: int = 50) -> list[BatchRecord]:
        """Return up to *limit* batches, most-recent first."""
        cursor = await self._db.execute(
            "SELECT id, env_name, status, started_at, finished_at, dry_run, vm_count, error "
            "FROM batches ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_record(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: tuple[object, ...]) -> BatchRecord:
        id_, env_name, status_str, started_at, finished_at, dry_run_int, vm_count, error = row
        return BatchRecord(
            id=str(id_),
            env_name=str(env_name),
            status=BatchStatus(str(status_str)),
            started_at=str(started_at),
            finished_at=str(finished_at) if finished_at is not None else None,
            dry_run=bool(dry_run_int),
            vm_count=int(str(vm_count)),
            error=str(error) if error is not None else None,
        )
