"""DB-backed docker_hygiene approval store (R3: process separation).

Replaces the in-memory HygieneApprovalManager rendezvous: each pending
docker_hygiene approval is a row in hygiene_approval_requests (migration #15)
so the web process can list and decide approvals without sharing in-process
state with the agent.

Race safety: :meth:`decide` is an atomic ``UPDATE … WHERE status='pending'``
— exactly one caller wins (rowcount == 1).

In-process wakeup: decisions in the same process set an asyncio.Event so
wait_for_decision returns immediately; cross-process decisions are caught by
the 2 s DB poll.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
    from errander.models.docker_hygiene import DockerHygieneAssessment

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_TIMEOUT = "timeout"

_COLUMNS = (
    "id, batch_id, vm_id, assessment_json, signed_token, posted_at, expires_at, "
    "status, decided_by, snapshot_hash, approved_items_json, decided_at"
)


@dataclass
class HygieneApprovalRow:
    """One durable hygiene approval request row."""

    id: int
    batch_id: str
    vm_id: str
    assessment_json: str
    signed_token: str
    posted_at: datetime
    expires_at: datetime
    status: str
    decided_by: str | None = None
    snapshot_hash: str | None = None
    approved_items_json: str | None = None
    decided_at: datetime | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.batch_id, self.vm_id)

    def is_decided(self) -> bool:
        return self.status != STATUS_PENDING

    def assessment(self) -> DockerHygieneAssessment:
        """Deserialize assessment_json back to a DockerHygieneAssessment."""
        from errander.models.docker_hygiene import DockerHygieneAssessment
        return DockerHygieneAssessment.from_json(self.assessment_json)

    def approved_items(self) -> list[dict[str, str]]:
        """Deserialize approved_items_json — list of {resource_class, identity}."""
        if not self.approved_items_json:
            return []
        try:
            parsed = json.loads(self.approved_items_json)
        except json.JSONDecodeError:
            logger.warning("Corrupt approved_items_json in hygiene row %s/%s", self.batch_id, self.vm_id)
            return []
        return parsed if isinstance(parsed, list) else []


class HygieneApprovalStore:
    """Async DB store for docker_hygiene approval requests.

    Shares the same AsyncDatabase as AuditStore. The table is created by
    migration #15.

    Usage::

        store = HygieneApprovalStore(db)
        await store.initialize()

        # Agent side:
        await store.create(batch_id, vm_id, assessment, signed_token)
        row = await store.wait_for_decision(batch_id, vm_id, timeout_seconds=1800)

        # Web handler:
        won = await store.decide(batch_id, vm_id, approved=True, decided_by="ui:admin",
                                 snapshot_hash=..., approved_items=[...])
    """

    _POLL_SECONDS = 2.0

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db
        self._waiters: dict[tuple[str, str], asyncio.Event] = {}

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(
        self,
        batch_id: str,
        vm_id: str,
        assessment: DockerHygieneAssessment,
        signed_token: str,
        *,
        timeout_seconds: int = 1800,
    ) -> HygieneApprovalRow:
        """Persist a new pending hygiene approval (durable-first).

        ON CONFLICT resets a same (batch_id, vm_id) row to pending (crash
        restart re-entering the assessment node).
        """
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(seconds=timeout_seconds)
        assessment_json = assessment.to_json()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO hygiene_approval_requests
                    (batch_id, vm_id, assessment_json, signed_token, posted_at, expires_at, status)
                VALUES (:batch_id, :vm_id, :assessment_json, :signed_token,
                        :posted_at, :expires_at, 'pending')
                ON CONFLICT (batch_id, vm_id) DO UPDATE SET
                    assessment_json = EXCLUDED.assessment_json,
                    signed_token    = EXCLUDED.signed_token,
                    posted_at       = EXCLUDED.posted_at,
                    expires_at      = EXCLUDED.expires_at,
                    status          = 'pending',
                    decided_by      = NULL,
                    snapshot_hash   = NULL,
                    approved_items_json = NULL,
                    decided_at      = NULL
                WHERE hygiene_approval_requests.status = 'pending'
                """),
                {
                    "batch_id": batch_id,
                    "vm_id": vm_id,
                    "assessment_json": assessment_json,
                    "signed_token": signed_token,
                    "posted_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                },
            )
        logger.info("Hygiene approval request created for batch=%s vm=%s", batch_id, vm_id)
        row = await self.get(batch_id, vm_id)
        assert row is not None
        return row

    async def decide(
        self,
        batch_id: str,
        vm_id: str,
        *,
        approved: bool,
        decided_by: str | None,
        snapshot_hash: str | None = None,
        approved_items: list[dict[str, str]] | None = None,
    ) -> bool:
        """Record a decision. Returns True iff this caller won the race."""
        status = STATUS_APPROVED if approved else STATUS_REJECTED
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE hygiene_approval_requests
                SET status = :status, decided_by = :decided_by,
                    snapshot_hash = :snapshot_hash,
                    approved_items_json = :approved_items_json,
                    decided_at = :decided_at
                WHERE batch_id = :batch_id AND vm_id = :vm_id
                  AND status = 'pending'
                """),
                {
                    "status": status,
                    "decided_by": decided_by,
                    "snapshot_hash": snapshot_hash,
                    "approved_items_json": (
                        json.dumps(approved_items) if approved_items is not None else None
                    ),
                    "decided_at": now,
                    "batch_id": batch_id,
                    "vm_id": vm_id,
                },
            )
            won = result.rowcount == 1
        if won:
            logger.info("Hygiene approval %s/%s %s by %s", batch_id, vm_id, status, decided_by or "unknown")
            self._notify(batch_id, vm_id)
        else:
            logger.info(
                "Hygiene decision for %s/%s by %s ignored — already decided",
                batch_id, vm_id, decided_by or "unknown",
            )
        return won

    async def mark_timeout(self, batch_id: str, vm_id: str) -> bool:
        """Transition pending → timeout. Returns True iff this call did it."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE hygiene_approval_requests
                SET status = 'timeout', decided_at = :decided_at
                WHERE batch_id = :batch_id AND vm_id = :vm_id AND status = 'pending'
                """),
                {"decided_at": now, "batch_id": batch_id, "vm_id": vm_id},
            )
            won = result.rowcount == 1
        if won:
            logger.warning("Hygiene approval %s/%s timed out — auto-rejected", batch_id, vm_id)
            self._notify(batch_id, vm_id)
        return won

    async def expire_overdue(self) -> list[tuple[str, str]]:
        """Mark past-expiry pending rows as timeout. Returns (batch_id, vm_id) pairs."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE hygiene_approval_requests
                SET status = 'timeout', decided_at = :now
                WHERE status = 'pending' AND expires_at <= :now
                RETURNING batch_id, vm_id
                """),
                {"now": now},
            )
            expired = [(str(row[0]), str(row[1])) for row in result.fetchall()]
        for batch_id, vm_id in expired:
            logger.warning("Hygiene approval timed out for batch=%s vm=%s", batch_id, vm_id)
            self._notify(batch_id, vm_id)
        return expired

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, batch_id: str, vm_id: str) -> HygieneApprovalRow | None:
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM hygiene_approval_requests "  # noqa: S608
                    "WHERE batch_id = :batch_id AND vm_id = :vm_id"
                ),
                {"batch_id": batch_id, "vm_id": vm_id},
            )
            row = result.mappings().fetchone()
        return _row_to_hygiene(row) if row is not None else None

    async def list_pending(self) -> list[HygieneApprovalRow]:
        """All pending requests, oldest first (UI queue order)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM hygiene_approval_requests "  # noqa: S608
                    "WHERE status = 'pending' ORDER BY posted_at ASC"
                ),
            )
            rows = result.mappings().fetchall()
        return [_row_to_hygiene(row) for row in rows]

    async def count_pending(self) -> int:
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT COUNT(*) FROM hygiene_approval_requests WHERE status = 'pending'"),
            )
            count = result.scalar()
        return int(count or 0)

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    async def wait_for_decision(
        self,
        batch_id: str,
        vm_id: str,
        timeout_seconds: int = 1800,
    ) -> HygieneApprovalRow | None:
        """Block until decided; return None on timeout.

        Wakes instantly for in-process decisions and within _POLL_SECONDS for
        cross-process decisions.
        """
        key = (batch_id, vm_id)
        event = self._waiters.setdefault(key, asyncio.Event())
        deadline = datetime.now(tz=UTC) + timedelta(seconds=timeout_seconds)
        try:
            while True:
                row = await self.get(batch_id, vm_id)
                if row is None:
                    return None
                if row.is_decided():
                    if row.status == STATUS_TIMEOUT:
                        return None
                    return row
                if datetime.now(tz=UTC) >= deadline:
                    await self.mark_timeout(batch_id, vm_id)
                    refreshed = await self.get(batch_id, vm_id)
                    if refreshed is None or refreshed.status == STATUS_TIMEOUT:
                        return None
                    return refreshed
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), timeout=self._POLL_SECONDS)
                event.clear()
        finally:
            self._waiters.pop(key, None)

    def _notify(self, batch_id: str, vm_id: str) -> None:
        event = self._waiters.get((batch_id, vm_id))
        if event is not None:
            event.set()


def _row_to_hygiene(row: Any) -> HygieneApprovalRow:
    def _dt(v: object) -> datetime | None:
        return datetime.fromisoformat(str(v)) if v else None

    posted_at = _dt(row["posted_at"])
    expires_at = _dt(row["expires_at"])
    assert posted_at is not None and expires_at is not None
    return HygieneApprovalRow(
        id=int(row["id"]),
        batch_id=str(row["batch_id"]),
        vm_id=str(row["vm_id"]),
        assessment_json=str(row["assessment_json"]),
        signed_token=str(row["signed_token"]) if row["signed_token"] else "",
        posted_at=posted_at,
        expires_at=expires_at,
        status=str(row["status"]),
        decided_by=str(row["decided_by"]) if row["decided_by"] is not None else None,
        snapshot_hash=str(row["snapshot_hash"]) if row["snapshot_hash"] is not None else None,
        approved_items_json=str(row["approved_items_json"]) if row["approved_items_json"] is not None else None,
        decided_at=_dt(row["decided_at"]),
    )
