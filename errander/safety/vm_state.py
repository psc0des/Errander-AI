"""Per-VM mutable state store.

Tracks facts the agent computes per-run that are not append-only audit events.
Currently used for needs_reboot state (set after patching, cleared on next
clean run or operator acknowledgement).

Schema is created by migration 0001 in errander/safety/migrations.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
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
    """Async database-backed store for mutable per-VM state.

    The caller must have already run migrations so the vm_state table exists.

    Usage::

        db = AsyncDatabase("errander.sqlite")
        async with VMStateStore(db) as store:
            await store.set_needs_reboot("prod/web-01", "packages", ("linux-image",))
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> VMStateStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def set_needs_reboot(
        self,
        vm_id: str,
        reason: str,
        pkgs: tuple[str, ...] = (),
    ) -> None:
        """Record that a VM requires a reboot."""
        now = datetime.now(tz=UTC).isoformat()
        pkgs_str = "\n".join(pkgs)
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO vm_state
                    (vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                     needs_reboot_detected_at, updated_at)
                VALUES (:vm_id, 1, :reason, :pkgs, :detected_at, :updated_at)
                ON CONFLICT(vm_id) DO UPDATE SET
                    needs_reboot = 1,
                    needs_reboot_reason = EXCLUDED.needs_reboot_reason,
                    needs_reboot_pkgs = EXCLUDED.needs_reboot_pkgs,
                    needs_reboot_detected_at = EXCLUDED.needs_reboot_detected_at,
                    updated_at = EXCLUDED.updated_at
                """),
                {"vm_id": vm_id, "reason": reason, "pkgs": pkgs_str,
                 "detected_at": now, "updated_at": now},
            )
        logger.debug("VM %s flagged needs_reboot: %s (%d pkgs)", vm_id, reason, len(pkgs))

    async def clear_needs_reboot(self, vm_id: str) -> None:
        """Clear the needs_reboot flag for a VM (e.g. after confirmed reboot)."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO vm_state (vm_id, needs_reboot, updated_at)
                VALUES (:vm_id, 0, :updated_at)
                ON CONFLICT(vm_id) DO UPDATE SET
                    needs_reboot = 0,
                    needs_reboot_reason = NULL,
                    needs_reboot_pkgs = NULL,
                    needs_reboot_detected_at = NULL,
                    updated_at = EXCLUDED.updated_at
                """),
                {"vm_id": vm_id, "updated_at": now},
            )
        logger.debug("VM %s needs_reboot cleared", vm_id)

    async def get(self, vm_id: str) -> VMState | None:
        """Return the current state for a VM, or None if never recorded."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                       needs_reboot_detected_at, last_uptime_seconds, updated_at
                FROM vm_state WHERE vm_id = :vm_id
                """),
                {"vm_id": vm_id},
            )
            row = result.fetchone()
        if row is None:
            return None
        return _row_to_vm_state(row)

    async def list_needs_reboot(self) -> list[VMState]:
        """Return all VMs currently flagged as needing a reboot."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT vm_id, needs_reboot, needs_reboot_reason, needs_reboot_pkgs,
                       needs_reboot_detected_at, last_uptime_seconds, updated_at
                FROM vm_state WHERE needs_reboot = 1
                ORDER BY needs_reboot_detected_at ASC
                """)
            )
            rows = result.fetchall()
        return [_row_to_vm_state(row) for row in rows]


def _row_to_vm_state(row: Any) -> VMState:
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
