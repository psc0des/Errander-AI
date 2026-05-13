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

import aiosqlite

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
    """Async SQLite-backed store for per-VM disk usage history.

    The caller (AuditStore) must have already run migrations so the
    vm_disk_history table exists before this store is used.

    Usage:
        async with VMDiskHistoryStore("errander.sqlite") as store:
            await store.record_batch("prod/web-01", [("/", 10*GB, 50*GB)])
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database connection."""
        self._db = await aiosqlite.connect(self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> VMDiskHistoryStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = (
                "VMDiskHistoryStore not initialized — call initialize() or use as "
                "async context manager"
            )
            raise RuntimeError(msg)
        return self._db

    async def record(
        self,
        vm_id: str,
        mountpoint: str,
        used_bytes: int,
        total_bytes: int,
        captured_at: datetime | None = None,
    ) -> None:
        """Record a single mountpoint measurement.

        Args:
            vm_id: VM identifier.
            mountpoint: Mount path (e.g. '/', '/var').
            used_bytes: Bytes in use.
            total_bytes: Total filesystem capacity.
            captured_at: Timestamp override (defaults to now UTC).
        """
        db = self._ensure_connected()
        ts = (captured_at or datetime.now(tz=UTC)).isoformat()
        await db.execute(
            "INSERT INTO vm_disk_history (vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (vm_id, ts, mountpoint, used_bytes, total_bytes),
        )
        await db.commit()

    async def record_batch(
        self,
        vm_id: str,
        datapoints: list[tuple[str, int, int]],
        captured_at: datetime | None = None,
    ) -> None:
        """Record multiple mountpoints in a single transaction.

        Args:
            vm_id: VM identifier.
            datapoints: List of (mountpoint, used_bytes, total_bytes) tuples.
            captured_at: Shared timestamp for all datapoints (defaults to now UTC).
        """
        if not datapoints:
            return
        db = self._ensure_connected()
        ts = (captured_at or datetime.now(tz=UTC)).isoformat()
        await db.executemany(
            "INSERT INTO vm_disk_history (vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            [(vm_id, ts, mp, used, total) for mp, used, total in datapoints],
        )
        await db.commit()

    async def get_window(
        self,
        vm_id: str,
        mountpoint: str,
        window_days: int,
    ) -> list[DiskDataPoint]:
        """Return data points for a mountpoint within the trailing window.

        Args:
            vm_id: VM identifier.
            mountpoint: Mount path to query.
            window_days: How many days back to look.

        Returns:
            Data points ordered oldest → newest.
        """
        db = self._ensure_connected()
        cutoff = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()
        rows = await db.execute_fetchall(
            """
            SELECT vm_id, captured_at, mountpoint, used_bytes, total_bytes
            FROM vm_disk_history
            WHERE vm_id = ? AND mountpoint = ? AND captured_at >= ?
            ORDER BY captured_at ASC
            """,
            (vm_id, mountpoint, cutoff),
        )
        return [_row_to_datapoint(row) for row in rows]

    async def get_distinct_mountpoints(self, vm_id: str) -> list[str]:
        """Return all mountpoints ever seen for a VM.

        Args:
            vm_id: VM identifier.
        """
        db = self._ensure_connected()
        rows = await db.execute_fetchall(
            "SELECT DISTINCT mountpoint FROM vm_disk_history WHERE vm_id = ? ORDER BY mountpoint",
            (vm_id,),
        )
        return [str(row[0]) for row in rows]

    async def prune_old_records(self, retention_days: int = 90) -> int:
        """Delete records older than retention_days.

        Args:
            retention_days: Records older than this are deleted.

        Returns:
            Number of rows deleted.
        """
        db = self._ensure_connected()
        cutoff = (datetime.now(tz=UTC) - timedelta(days=retention_days)).isoformat()
        cursor = await db.execute(
            "DELETE FROM vm_disk_history WHERE captured_at < ?",
            (cutoff,),
        )
        await db.commit()
        count = cursor.rowcount or 0
        if count:
            logger.info(
                "Pruned %d old vm_disk_history rows (older than %dd)", count, retention_days
            )
        return count


def _row_to_datapoint(row: aiosqlite.Row) -> DiskDataPoint:
    return DiskDataPoint(
        vm_id=str(row[0]),
        captured_at=datetime.fromisoformat(str(row[1])),
        mountpoint=str(row[2]),
        used_bytes=int(str(row[3])),
        total_bytes=int(str(row[4])),
    )
