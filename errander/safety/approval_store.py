"""Durable approval request store (R3 keystone).

Replaces the in-memory ApprovalManager: every live-approval request is a row
in the approval_requests table (migration #13), so pending approvals survive
agent restarts. Decisions are written by either the Slack reaction watcher or
the web UI handler; the graph coroutine waits in :meth:`wait_for_decision`.

Race safety: :meth:`decide` is an atomic ``UPDATE ... WHERE status='pending'``
— exactly one caller wins (rowcount == 1); everyone else observes the recorded
decision. The same pattern guards :meth:`mark_execution_started` so the
restart reconciler and a live graph never double-execute one approval.

In-process wakeup: decisions made in the same process set an asyncio.Event so
wait_for_decision returns immediately; decisions from another process (Step 4
process split) are caught by the DB poll loop.
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
logger = logging.getLogger(__name__)

#: Status values (mirrors the CHECK constraint in migration #13).
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_TIMEOUT = "timeout"

_COLUMNS = (
    "batch_id, env_name, plan_id, plan_hash, report, vm_plans_json, "
    "posted_at, expires_at, status, slack_message_ts, decided_by, "
    "decided_by_group, decided_at, approved_items_json, execution_started_at"
)


@dataclass
class ApprovalRequest:
    """One durable approval request row.

    Field names intentionally match the old in-memory PendingApproval where
    they overlap (batch_id, report, posted_at, slack_message_ts, vm_plans,
    decided_by, approved_items) so the web UI templates read either shape.
    """

    batch_id: str
    env_name: str
    plan_id: str
    plan_hash: str
    report: str
    posted_at: datetime
    expires_at: datetime
    status: str
    vm_plans: list[dict[str, object]] | None = None
    slack_message_ts: str | None = None
    decided_by: str | None = None
    decided_by_group: str | None = None
    decided_at: datetime | None = None
    approved_items: list[dict[str, object]] | None = None
    execution_started_at: datetime | None = None

    def is_decided(self) -> bool:
        """True once any decision (approve/reject/timeout) is recorded."""
        return self.status != STATUS_PENDING

    @property
    def approved(self) -> bool | None:
        """True/False once decided; None while pending (template compat)."""
        if self.status == STATUS_PENDING:
            return None
        return self.status == STATUS_APPROVED


class ApprovalRequestStore:
    """Async DB store for approval requests.

    Shares the same AsyncDatabase as AuditStore. The approval_requests table
    is created by migration #13.

    Usage::

        store = ApprovalRequestStore(db)
        await store.initialize()

        # In the agent graph (one coroutine):
        await store.create(batch_id, env_name=..., plan_id=..., ...)
        request = await store.wait_for_decision(batch_id, timeout_seconds=1800)

        # In the HTTP handler / Slack watcher (other coroutines or processes):
        won = await store.decide(batch_id, approved=True, decided_by="ui:admin")
    """

    #: DB poll cadence inside wait_for_decision — covers cross-process
    #: decisions that can't set the in-process event (Step 4 process split).
    _POLL_SECONDS = 2.0

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db
        # In-process wakeup registry: batch_id → event set by decide().
        self._waiters: dict[str, asyncio.Event] = {}

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(
        self,
        batch_id: str,
        *,
        env_name: str,
        plan_id: str,
        plan_hash: str,
        report: str,
        vm_plans: list[dict[str, object]] | None = None,
        timeout_seconds: int = 1800,
        slack_message_ts: str | None = None,
    ) -> ApprovalRequest:
        """Persist a new pending approval request (durable-first).

        Called BEFORE the Slack post so a crash between the two leaves a
        recoverable pending row, never an invisible Slack message.
        ON CONFLICT resets a same-batch_id row to pending (crash-restart
        re-entering the gate for an undecided batch).
        """
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(seconds=timeout_seconds)
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO approval_requests
                    (batch_id, env_name, plan_id, plan_hash, report, vm_plans_json,
                     posted_at, expires_at, status, slack_message_ts)
                VALUES (:batch_id, :env_name, :plan_id, :plan_hash, :report, :vm_plans_json,
                        :posted_at, :expires_at, 'pending', :slack_message_ts)
                ON CONFLICT(batch_id) DO UPDATE SET
                    plan_id       = EXCLUDED.plan_id,
                    plan_hash     = EXCLUDED.plan_hash,
                    report        = EXCLUDED.report,
                    vm_plans_json = EXCLUDED.vm_plans_json,
                    posted_at     = EXCLUDED.posted_at,
                    expires_at    = EXCLUDED.expires_at
                WHERE approval_requests.status = 'pending'
                """),
                {
                    "batch_id": batch_id,
                    "env_name": env_name,
                    "plan_id": plan_id,
                    "plan_hash": plan_hash,
                    "report": report,
                    "vm_plans_json": (
                        json.dumps(vm_plans, default=str) if vm_plans is not None else None
                    ),
                    "posted_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "slack_message_ts": slack_message_ts,
                },
            )
        logger.info("Approval request persisted for batch %s", batch_id)
        request = await self.get(batch_id)
        assert request is not None  # just inserted
        return request

    async def set_slack_ts(self, batch_id: str, slack_message_ts: str) -> None:
        """Stamp the Slack message ts after the (best-effort) Slack post."""
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE approval_requests SET slack_message_ts = :ts "
                    "WHERE batch_id = :batch_id"
                ),
                {"ts": slack_message_ts, "batch_id": batch_id},
            )

    async def decide(
        self,
        batch_id: str,
        *,
        approved: bool,
        decided_by: str | None,
        approved_items: list[dict[str, object]] | None = None,
    ) -> bool:
        """Record a decision. Returns True iff this caller won the race.

        Atomic ``UPDATE ... WHERE status = 'pending'`` — concurrent deciders
        (Slack watcher vs UI button vs reconciler timeout) settle on rowcount:
        exactly one observes rowcount == 1.

        decided_by is namespaced by channel: ``slack:U123`` / ``ui:admin``.
        """
        status = STATUS_APPROVED if approved else STATUS_REJECTED
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE approval_requests
                SET status = :status, decided_by = :decided_by, decided_at = :decided_at,
                    approved_items_json = :approved_items_json
                WHERE batch_id = :batch_id AND status = 'pending'
                """),
                {
                    "status": status,
                    "decided_by": decided_by,
                    "decided_at": now,
                    "approved_items_json": (
                        json.dumps(approved_items, default=str)
                        if approved_items is not None else None
                    ),
                    "batch_id": batch_id,
                },
            )
            won = result.rowcount == 1
        if won:
            logger.info(
                "Batch %s %s by %s", batch_id, status, decided_by or "unknown",
            )
            self._notify(batch_id)
        else:
            logger.info(
                "Decision for batch %s by %s ignored — already decided",
                batch_id, decided_by or "unknown",
            )
        return won

    async def mark_timeout(self, batch_id: str) -> bool:
        """Transition pending → timeout. Returns True iff this call did it."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE approval_requests
                SET status = 'timeout', decided_at = :decided_at
                WHERE batch_id = :batch_id AND status = 'pending'
                """),
                {"decided_at": now, "batch_id": batch_id},
            )
            won = result.rowcount == 1
        if won:
            logger.warning("Approval for batch %s timed out — auto-rejected", batch_id)
            self._notify(batch_id)
        return won

    async def expire_overdue(self) -> list[str]:
        """Mark every past-expiry pending request as timeout.

        Returns the batch_ids expired — the reconciler audit-logs each one.
        """
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE approval_requests
                SET status = 'timeout', decided_at = :now
                WHERE status = 'pending' AND expires_at <= :now
                RETURNING batch_id
                """),
                {"now": now},
            )
            expired = [str(row[0]) for row in result.fetchall()]
        for batch_id in expired:
            logger.warning("Expired stale approval request for batch %s", batch_id)
            self._notify(batch_id)
        return expired

    async def mark_execution_started(self, batch_id: str) -> bool:
        """Atomically claim an approved request for execution.

        Returns True iff this caller claimed it (was approved and unclaimed).
        Stamped for both immediate execution and deferred-store handoff, so
        the restart reconciler never picks up a batch some executor already
        owns.
        """
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE approval_requests
                SET execution_started_at = :now
                WHERE batch_id = :batch_id
                  AND status = 'approved'
                  AND execution_started_at IS NULL
                """),
                {"now": now, "batch_id": batch_id},
            )
            claimed = result.rowcount == 1
        if not claimed:
            logger.warning(
                "Execution claim for batch %s refused — not approved or already claimed",
                batch_id,
            )
        return claimed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, batch_id: str) -> ApprovalRequest | None:
        """Fetch one request by batch_id."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM approval_requests "  # noqa: S608 — constant column list
                    "WHERE batch_id = :batch_id"
                ),
                {"batch_id": batch_id},
            )
            row = result.mappings().fetchone()
        return _row_to_request(row) if row is not None else None

    async def get_pending(self) -> list[ApprovalRequest]:
        """All pending requests, oldest first (UI queue order)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM approval_requests "  # noqa: S608
                    "WHERE status = 'pending' ORDER BY posted_at ASC"
                ),
            )
            rows = result.mappings().fetchall()
        return [_row_to_request(row) for row in rows]

    async def count_pending(self) -> int:
        """Number of pending requests (UI nav badge)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'"),
            )
            count = result.scalar()
        return int(count or 0)

    async def get_history(self, limit: int = 20) -> list[ApprovalRequest]:
        """Recently decided requests, newest decision first."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM approval_requests "  # noqa: S608
                    "WHERE status != 'pending' "
                    "ORDER BY decided_at DESC NULLS LAST LIMIT :limit"
                ),
                {"limit": limit},
            )
            rows = result.mappings().fetchall()
        return [_row_to_request(row) for row in rows]

    async def get_orphaned_approved(self) -> list[ApprovalRequest]:
        """Approved requests no executor has claimed (restart recovery).

        These exist when the agent died between the operator's decision and
        the execution claim — the reconciler re-executes them through the
        deferred-replay path.
        """
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM approval_requests "  # noqa: S608
                    "WHERE status = 'approved' AND execution_started_at IS NULL "
                    "ORDER BY decided_at ASC"
                ),
            )
            rows = result.mappings().fetchall()
        return [_row_to_request(row) for row in rows]

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    async def wait_for_decision(
        self,
        batch_id: str,
        timeout_seconds: int = 1800,
    ) -> ApprovalRequest:
        """Block until the request is decided; auto-timeout when overdue.

        Wakes instantly for in-process decisions (asyncio.Event) and within
        _POLL_SECONDS for decisions written by another process. On timeout
        the row is atomically transitioned to 'timeout' (a decision that
        lands first still wins — mark_timeout loses the race and we return
        the recorded decision).

        Raises:
            KeyError: If batch_id has no approval_requests row.
        """
        event = self._waiters.setdefault(batch_id, asyncio.Event())
        deadline = datetime.now(tz=UTC) + timedelta(seconds=timeout_seconds)
        try:
            while True:
                request = await self.get(batch_id)
                if request is None:
                    raise KeyError(f"No approval request for batch {batch_id!r}")
                if request.is_decided():
                    return request
                if datetime.now(tz=UTC) >= deadline:
                    await self.mark_timeout(batch_id)
                    refreshed = await self.get(batch_id)
                    assert refreshed is not None
                    return refreshed
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), timeout=self._POLL_SECONDS)
                event.clear()
        finally:
            self._waiters.pop(batch_id, None)

    def has_waiter(self, batch_id: str) -> bool:
        """True when an in-process coroutine is waiting on this batch.

        The restart reconciler uses this to leave live gate-owned requests
        alone and only adopt requests orphaned by a previous process.
        """
        return batch_id in self._waiters

    def _notify(self, batch_id: str) -> None:
        """Wake an in-process wait_for_decision coroutine, if any."""
        event = self._waiters.get(batch_id)
        if event is not None:
            event.set()


def _row_to_request(row: Any) -> ApprovalRequest:
    def _dt(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value else None

    def _json_list(value: object) -> list[dict[str, object]] | None:
        if not value:
            return None
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            logger.warning("Corrupt JSON column in approval_requests row — ignoring")
            return None
        return parsed if isinstance(parsed, list) else None

    posted_at = _dt(row["posted_at"])
    expires_at = _dt(row["expires_at"])
    assert posted_at is not None and expires_at is not None  # NOT NULL columns
    return ApprovalRequest(
        batch_id=str(row["batch_id"]),
        env_name=str(row["env_name"]),
        plan_id=str(row["plan_id"]),
        plan_hash=str(row["plan_hash"]),
        report=str(row["report"]),
        posted_at=posted_at,
        expires_at=expires_at,
        status=str(row["status"]),
        vm_plans=_json_list(row["vm_plans_json"]),
        slack_message_ts=(
            str(row["slack_message_ts"]) if row["slack_message_ts"] is not None else None
        ),
        decided_by=str(row["decided_by"]) if row["decided_by"] is not None else None,
        decided_by_group=(
            str(row["decided_by_group"]) if row["decided_by_group"] is not None else None
        ),
        decided_at=_dt(row["decided_at"]),
        approved_items=_json_list(row["approved_items_json"]),
        execution_started_at=_dt(row["execution_started_at"]),
    )
