"""Disk usage history store for growth trend detection.

Records `df -P -B1` output per (vm_id, mountpoint) per batch run.
The reporting layer queries the trailing window to compute deltas and
surfaces mountpoints that grew beyond the configured threshold.

Schema is created by migration 0003 in errander/safety/migrations.py.
90-day retention is enforced by prune_old_records(), called at end of batch.
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


@dataclass(frozen=True)
class DiskDataPoint:
    """A single disk usage measurement for one mountpoint."""

    vm_id: str
    captured_at: datetime
    mountpoint: str
    used_bytes: int
    total_bytes: int

    @property
    def used_pct(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.used_bytes / self.total_bytes) * 100.0


class VMDiskHistoryStore:
    """Async database-backed store for per-VM disk usage history.

    The caller must have already run migrations so the vm_disk_history table exists.

    Usage::

        db = AsyncDatabase("postgresql://errander:errander@localhost/errander")
        async with VMDiskHistoryStore(db) as store:
            await store.record_batch("prod/web-01", [("/", 10*GB, 50*GB)])
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> VMDiskHistoryStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def record(
        self,
        vm_id: str,
        mountpoint: str,
        used_bytes: int,
        total_bytes: int,
        captured_at: datetime | None = None,
    ) -> None:
        """Record a single mountpoint measurement."""
        ts = (captured_at or datetime.now(tz=UTC)).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO vm_disk_history (vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
                    "VALUES (:vm_id, :captured_at, :mountpoint, :used_bytes, :total_bytes)"
                ),
                {"vm_id": vm_id, "captured_at": ts, "mountpoint": mountpoint,
                 "used_bytes": used_bytes, "total_bytes": total_bytes},
            )

    async def record_batch(
        self,
        vm_id: str,
        datapoints: list[tuple[str, int, int]],
        captured_at: datetime | None = None,
    ) -> None:
        """Record multiple mountpoints in a single transaction."""
        if not datapoints:
            return
        ts = (captured_at or datetime.now(tz=UTC)).isoformat()
        rows = [
            {"vm_id": vm_id, "captured_at": ts, "mountpoint": mp,
             "used_bytes": used, "total_bytes": total}
            for mp, used, total in datapoints
        ]
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO vm_disk_history (vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
                    "VALUES (:vm_id, :captured_at, :mountpoint, :used_bytes, :total_bytes)"
                ),
                rows,
            )

    async def get_window(
        self,
        vm_id: str,
        mountpoint: str,
        window_days: int,
    ) -> list[DiskDataPoint]:
        """Return data points for a mountpoint within the trailing window."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT vm_id, captured_at, mountpoint, used_bytes, total_bytes
                FROM vm_disk_history
                WHERE vm_id = :vm_id AND mountpoint = :mountpoint AND captured_at >= :cutoff
                ORDER BY captured_at ASC
                """),
                {"vm_id": vm_id, "mountpoint": mountpoint, "cutoff": cutoff},
            )
            rows = result.fetchall()
        return [_row_to_datapoint(row) for row in rows]

    async def get_distinct_mountpoints(self, vm_id: str) -> list[str]:
        """Return all mountpoints ever seen for a VM."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT DISTINCT mountpoint FROM vm_disk_history "
                    "WHERE vm_id = :vm_id ORDER BY mountpoint"
                ),
                {"vm_id": vm_id},
            )
            rows = result.fetchall()
        return [str(row[0]) for row in rows]

    async def prune_old_records(self, retention_days: int = 90) -> int:
        """Delete records older than retention_days. Returns number of rows deleted."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=retention_days)).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM vm_disk_history WHERE captured_at < :cutoff"),
                {"cutoff": cutoff},
            )
            count = result.rowcount or 0
        if count:
            logger.info(
                "Pruned %d old vm_disk_history rows (older than %dd)", count, retention_days
            )
        return count


def _row_to_datapoint(row: Any) -> DiskDataPoint:
    return DiskDataPoint(
        vm_id=str(row[0]),
        captured_at=datetime.fromisoformat(str(row[1])),
        mountpoint=str(row[2]),
        used_bytes=int(str(row[3])),
        total_bytes=int(str(row[4])),
    )
