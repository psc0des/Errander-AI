"""Per-VM mutable state store.

Tracks facts the agent computes per-run that are not append-only audit events.
Currently used for needs_reboot state (set after patching, cleared on next
clean run or operator acknowledgement).

Schema is created by migration 0001 in errander/safety/migrations.py.
The table uses TEXT for timestamps (ISO-8601) for PostgreSQL portability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite
from aiosqlite import Row

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VMState:
    """Mutable per-VM facts tracked between runs."""

    vm_id: str
    needs_reboot: bool
    needs_reboot_reason: str | None
    needs_reboot_pkgs: tuple[str, ...]
    needs_reboot_detected_at: datetime | None
    last_uptime_seconds: float | None
    updated_at: datetime


class VMStateStore:
    """Async SQLite-backed store for mutable per-VM state.

    The caller (AuditStore) must have already run migrations so the
    vm_state table exists before this store is used.

    Usage:
        async with VMStateStore("errander.sqlite") as store:
            await store.set_needs_reboot("prod/web-01", "packages", ("linux-image",))
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

    async def __aenter__(self) -> VMStateStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "VMStateStore not initialized — call initialize() or use as async context manager"
            raise RuntimeError(msg)
        return self._db

    async def set_needs_reboot(
        self,
        vm_id: str,
        reason: str,
        pkgs: tuple[str, ...] = (),
    ) -> None:
        """Record that a VM requires a reboot.

        Args:
            vm_id: VM identifier.
            reason: Human-readable reason (e.g. "packages require reboot").
            pkgs: Package names that triggered the reboot requirement.
        """
        db = self._ensure_connected()
        now = datetime.now(tz=UTC).isoformat()
        pkgs_str = "\n".join(pkgs)
        await db.execute(
            """
            INSERT INTO vm_state
                (vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                 needs_reboot_detected_at, updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(vm_id) DO UPDATE SET
                needs_reboot = 1,
                needs_reboot_reason = excluded.needs_reboot_reason,
                needs_reboot_pkgs = excluded.needs_reboot_pkgs,
                needs_reboot_detected_at = excluded.needs_reboot_detected_at,
                updated_at = excluded.updated_at
            """,
            (vm_id, reason, pkgs_str, now, now),
        )
        await db.commit()
        logger.debug("VM %s flagged needs_reboot: %s (%d pkgs)", vm_id, reason, len(pkgs))

    async def clear_needs_reboot(self, vm_id: str) -> None:
        """Clear the needs_reboot flag for a VM (e.g. after confirmed reboot).

        Args:
            vm_id: VM identifier.
        """
        db = self._ensure_connected()
        now = datetime.now(tz=UTC).isoformat()
        await db.execute(
            """
            INSERT INTO vm_state (vm_id, needs_reboot, updated_at)
            VALUES (?, 0, ?)
            ON CONFLICT(vm_id) DO UPDATE SET
                needs_reboot = 0,
                needs_reboot_reason = NULL,
                needs_reboot_pkgs = NULL,
                needs_reboot_detected_at = NULL,
                updated_at = excluded.updated_at
            """,
            (vm_id, now),
        )
        await db.commit()
        logger.debug("VM %s needs_reboot cleared", vm_id)

    async def get(self, vm_id: str) -> VMState | None:
        """Return the current state for a VM, or None if never recorded.

        Args:
            vm_id: VM identifier.
        """
        db = self._ensure_connected()
        cursor = await db.execute(
            """
            SELECT vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                   needs_reboot_detected_at, last_uptime_seconds, updated_at
            FROM vm_state WHERE vm_id = ?
            """,
            (vm_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_vm_state(row)

    async def list_needs_reboot(self) -> list[VMState]:
        """Return all VMs currently flagged as needing a reboot."""
        db = self._ensure_connected()
        rows = await db.execute_fetchall(
            """
            SELECT vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                   needs_reboot_detected_at, last_uptime_seconds, updated_at
            FROM vm_state WHERE needs_reboot = 1
            ORDER BY needs_reboot_detected_at ASC
            """
        )
        return [_row_to_vm_state(row) for row in rows]


def _row_to_vm_state(row: Row) -> VMState:
    pkgs_str = str(row[3]) if row[3] is not None else ""
    pkgs = tuple(p for p in pkgs_str.split("\n") if p)
    detected_at = datetime.fromisoformat(str(row[4])) if row[4] is not None else None
    return VMState(
        vm_id=str(row[0]),
        needs_reboot=bool(int(str(row[1]))),
        needs_reboot_reason=str(row[2]) if row[2] is not None else None,
        needs_reboot_pkgs=pkgs,
        needs_reboot_detected_at=detected_at,
        last_uptime_seconds=float(str(row[5])) if row[5] is not None else None,
        updated_at=datetime.fromisoformat(str(row[6])),
    )
