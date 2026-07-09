"""Durable agent-proposal store (detect-and-propose, fable-plan Phase 1).

Mirrors ApprovalRequestStore conventions: async DB-backed rows (migration
#16), atomic ``decide()`` via ``UPDATE ... WHERE status = 'pending'``
(exactly one caller wins), and durable-first writes so proposals survive
agent restarts.

This store is deliberately NOT the approval store — a proposal is a
suggestion record, never an authorization (fable-plan §5.1). An approved
actionable proposal is *claimed* by the agent-side reconciler
(:meth:`mark_execution_started`) and executed through the existing
deterministic sub-graph path; the web process only ever records decisions.

Dedup (fable-plan Phase 1 rule): one open proposal per (vm_id, action_key),
enforced by a partial unique index; :meth:`create_or_refresh` upserts
evidence onto the open row instead of duplicating.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
    ProposalStatus,
)

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)

#: Default proposal lifetime — stale suggestions expire, never linger.
DEFAULT_EXPIRY_DAYS = 7

_COLUMNS = (
    "proposal_id, env_name, vm_id, kind, action_type, action_key, "
    "signal_kind, origin, probe_id, evidence_json, confidence, status, "
    "created_at, updated_at, expires_at, decided_by, decided_by_group, "
    "decided_at, snoozed_until, execution_started_at, execution_status"
)


class ProposalStore:
    """Async DB store for agent proposals (agent_proposals, migration #16)."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_or_refresh(
        self,
        proposal: AgentProposal,
        *,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> tuple[AgentProposal, bool]:
        """Insert a proposal, or refresh the open one for (vm_id, action_key).

        Returns ``(stored_proposal, created)`` — ``created`` is False when an
        open (pending) proposal already existed and only its evidence,
        confidence, probe_id, updated_at, and expires_at were refreshed.
        The partial unique index makes the upsert race-safe.
        """
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(days=expiry_days)
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                INSERT INTO agent_proposals
                    (proposal_id, env_name, vm_id, kind, action_type, action_key,
                     signal_kind, origin, probe_id, evidence_json, confidence,
                     status, created_at, updated_at, expires_at)
                VALUES (:proposal_id, :env_name, :vm_id, :kind, :action_type,
                        :action_key, :signal_kind, :origin, :probe_id,
                        :evidence_json, :confidence, 'pending',
                        :created_at, :updated_at, :expires_at)
                ON CONFLICT (vm_id, action_key) WHERE status = 'pending'
                DO UPDATE SET
                    evidence_json = EXCLUDED.evidence_json,
                    confidence    = EXCLUDED.confidence,
                    probe_id      = EXCLUDED.probe_id,
                    updated_at    = EXCLUDED.updated_at,
                    expires_at    = EXCLUDED.expires_at
                RETURNING proposal_id, (created_at = updated_at) AS created
                """),
                {
                    "proposal_id": proposal.proposal_id,
                    "env_name": proposal.env_name,
                    "vm_id": proposal.vm_id,
                    "kind": proposal.kind.value,
                    "action_type": proposal.action_type,
                    "action_key": proposal.action_key,
                    "signal_kind": proposal.signal_kind,
                    "origin": proposal.origin,
                    "probe_id": proposal.probe_id,
                    "evidence_json": json.dumps(
                        [e.model_dump(mode="json") for e in proposal.evidence]
                    ),
                    "confidence": proposal.confidence,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                },
            )
            row = result.fetchone()
        assert row is not None  # INSERT ... RETURNING always yields a row
        stored_id, created = str(row[0]), bool(row[1])
        stored = await self.get(stored_id)
        assert stored is not None  # just upserted
        if created:
            logger.info(
                "Proposal created: %s %s/%s (%s)",
                stored_id, proposal.vm_id, proposal.action_key, proposal.origin,
            )
        else:
            logger.info(
                "Proposal refreshed: %s %s/%s", stored_id, proposal.vm_id,
                proposal.action_key,
            )
        return stored, created

    async def decide(
        self,
        proposal_id: str,
        *,
        approved: bool,
        decided_by: str | None,
        decided_by_group: str | None = None,
    ) -> bool:
        """Record a decision. Returns True iff this caller won the race."""
        status = (
            ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
        ).value
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE agent_proposals
                SET status = :status, decided_by = :decided_by,
                    decided_by_group = :decided_by_group, decided_at = :decided_at,
                    updated_at = :decided_at
                WHERE proposal_id = :proposal_id AND status = 'pending'
                """),
                {
                    "status": status,
                    "decided_by": decided_by,
                    "decided_by_group": decided_by_group,
                    "decided_at": now,
                    "proposal_id": proposal_id,
                },
            )
            won = result.rowcount == 1
        if won:
            logger.info(
                "Proposal %s %s by %s", proposal_id, status, decided_by or "unknown",
            )
        else:
            logger.info(
                "Decision for proposal %s by %s ignored — already decided",
                proposal_id, decided_by or "unknown",
            )
        return won

    async def snooze(
        self,
        proposal_id: str,
        *,
        snoozed_until: datetime,
        decided_by: str | None,
        decided_by_group: str | None = None,
    ) -> bool:
        """Snooze a pending proposal. Returns True iff this caller won."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE agent_proposals
                SET status = 'snoozed', snoozed_until = :snoozed_until,
                    decided_by = :decided_by, decided_by_group = :decided_by_group,
                    decided_at = :now, updated_at = :now
                WHERE proposal_id = :proposal_id AND status = 'pending'
                """),
                {
                    "snoozed_until": snoozed_until.isoformat(),
                    "decided_by": decided_by,
                    "decided_by_group": decided_by_group,
                    "now": now,
                    "proposal_id": proposal_id,
                },
            )
            won = result.rowcount == 1
        if won:
            logger.info(
                "Proposal %s snoozed until %s by %s",
                proposal_id, snoozed_until.isoformat(), decided_by or "unknown",
            )
        return won

    async def expire_overdue(self) -> list[str]:
        """Mark every past-expiry pending proposal as expired.

        Returns the proposal_ids expired — the reconciler audit-logs each.
        """
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE agent_proposals
                SET status = 'expired', updated_at = :now
                WHERE status = 'pending' AND expires_at <= :now
                RETURNING proposal_id
                """),
                {"now": now},
            )
            expired = [str(row[0]) for row in result.fetchall()]
        for proposal_id in expired:
            logger.info("Expired stale proposal %s", proposal_id)
        return expired

    async def mark_execution_started(self, proposal_id: str) -> bool:
        """Atomically claim an approved actionable proposal for execution.

        Returns True iff this caller claimed it. Mirrors
        ApprovalRequestStore.mark_execution_started — the reconciler and any
        future executor settle on rowcount so a proposal is never run twice.
        """
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE agent_proposals
                SET execution_started_at = :now, updated_at = :now
                WHERE proposal_id = :proposal_id
                  AND status = 'approved'
                  AND kind = 'action'
                  AND execution_started_at IS NULL
                """),
                {"now": now, "proposal_id": proposal_id},
            )
            claimed = result.rowcount == 1
        if not claimed:
            logger.warning(
                "Execution claim for proposal %s refused — not approved-actionable "
                "or already claimed",
                proposal_id,
            )
        return claimed

    async def set_execution_status(self, proposal_id: str, status: str) -> None:
        """Record the terminal execution outcome ('success' | 'failed')."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_proposals SET execution_status = :status, "
                    "updated_at = :now WHERE proposal_id = :proposal_id"
                ),
                {"status": status, "now": now, "proposal_id": proposal_id},
            )

    async def wake_snoozed(self) -> list[str]:
        """Return past-due snoozed proposals to pending (snooze honored verbatim)."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                UPDATE agent_proposals
                SET status = 'pending', snoozed_until = NULL, updated_at = :now
                WHERE status = 'snoozed' AND snoozed_until <= :now
                RETURNING proposal_id
                """),
                {"now": now},
            )
            woken = [str(row[0]) for row in result.fetchall()]
        for proposal_id in woken:
            logger.info("Snoozed proposal %s returned to pending", proposal_id)
        return woken

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, proposal_id: str) -> AgentProposal | None:
        """Fetch one proposal by id."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM agent_proposals "  # noqa: S608 — constant column list
                    "WHERE proposal_id = :proposal_id"
                ),
                {"proposal_id": proposal_id},
            )
            row = result.mappings().fetchone()
        return _row_to_proposal(row) if row is not None else None

    async def get_pending(self) -> list[AgentProposal]:
        """All pending proposals, oldest first (UI queue order)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM agent_proposals "  # noqa: S608
                    "WHERE status = 'pending' ORDER BY created_at ASC"
                ),
            )
            rows = result.mappings().fetchall()
        return [_row_to_proposal(row) for row in rows]

    async def count_pending(self) -> int:
        """Number of pending proposals (UI nav badge)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT COUNT(*) FROM agent_proposals WHERE status = 'pending'"),
            )
            count = result.scalar()
        return int(count or 0)

    async def get_history(self, limit: int = 50) -> list[AgentProposal]:
        """Recently decided/expired proposals, newest first."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM agent_proposals "  # noqa: S608
                    "WHERE status != 'pending' "
                    "ORDER BY updated_at DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
            rows = result.mappings().fetchall()
        return [_row_to_proposal(row) for row in rows]

    async def get_approved_unclaimed(self) -> list[AgentProposal]:
        """Approved actionable proposals no executor has claimed (reconciler feed)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM agent_proposals "  # noqa: S608
                    "WHERE status = 'approved' AND kind = 'action' "
                    "AND execution_started_at IS NULL "
                    "ORDER BY decided_at ASC"
                ),
            )
            rows = result.mappings().fetchall()
        return [_row_to_proposal(row) for row in rows]

    async def count_rejections(self, vm_id: str, action_key: str) -> int:
        """Rejections recorded for (vm_id, action_key) — suppression input (Phase 4)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM agent_proposals "
                    "WHERE vm_id = :vm_id AND action_key = :action_key "
                    "AND status = 'rejected'"
                ),
                {"vm_id": vm_id, "action_key": action_key},
            )
            count = result.scalar()
        return int(count or 0)

    async def rejection_window_state(
        self, vm_id: str, action_key: str,
    ) -> tuple[int, datetime | None]:
        """(rejection_count, most_recent_decided_at) for (vm_id, action_key).

        Suppression input (Phase 4) — pairs the all-time count with the
        latest rejection's timestamp so the caller can apply a rolling
        cooldown window on top of a threshold count.
        """
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT COUNT(*), MAX(decided_at) FROM agent_proposals "
                    "WHERE vm_id = :vm_id AND action_key = :action_key "
                    "AND status = 'rejected'"
                ),
                {"vm_id": vm_id, "action_key": action_key},
            )
            row = result.fetchone()
        if row is None or row[0] is None:
            return 0, None
        count = int(row[0])
        latest = datetime.fromisoformat(str(row[1])) if row[1] is not None else None
        return count, latest

    async def is_suppressed(
        self, vm_id: str, action_key: str, *, threshold: int, window_days: int,
    ) -> bool:
        """True iff a NEW proposal for (vm_id, action_key) should be refused.

        Rejected >= threshold times AND the most recent rejection is within
        window_days. Does not affect refreshing an already-open proposal —
        callers must check :meth:`get_open` first (see
        :meth:`create_or_refresh_unless_suppressed`).
        """
        count, latest = await self.rejection_window_state(vm_id, action_key)
        if count < threshold or latest is None:
            return False
        cutoff = datetime.now(tz=UTC) - timedelta(days=window_days)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return latest >= cutoff

    async def get_open(self, vm_id: str, action_key: str) -> AgentProposal | None:
        """The open (pending) proposal for (vm_id, action_key), if any."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM agent_proposals "  # noqa: S608
                    "WHERE vm_id = :vm_id AND action_key = :action_key "
                    "AND status = 'pending'"
                ),
                {"vm_id": vm_id, "action_key": action_key},
            )
            row = result.mappings().fetchone()
        return _row_to_proposal(row) if row is not None else None

    async def create_or_refresh_unless_suppressed(
        self,
        proposal: AgentProposal,
        *,
        suppression_threshold: int,
        suppression_window_days: int,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> tuple[AgentProposal | None, bool]:
        """Like :meth:`create_or_refresh`, but refuses to CREATE a genuinely
        new proposal when (vm_id, action_key) is currently suppressed
        (fable-plan Phase 4). Refreshing an already-open pending proposal is
        never suppressed — suppression only blocks a fresh re-propose after
        the operator has rejected the same pair repeatedly.

        Returns ``(None, False)`` when suppressed; otherwise identical to
        :meth:`create_or_refresh`'s ``(stored, created)``.
        """
        existing = await self.get_open(proposal.vm_id, proposal.action_key)
        if existing is None:
            suppressed = await self.is_suppressed(
                proposal.vm_id, proposal.action_key,
                threshold=suppression_threshold, window_days=suppression_window_days,
            )
            if suppressed:
                return None, False
        return await self.create_or_refresh(proposal, expiry_days=expiry_days)


def _row_to_proposal(row: Any) -> AgentProposal:
    def _dt(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value else None

    evidence: list[ProposalEvidence] = []
    try:
        parsed = json.loads(str(row["evidence_json"] or "[]"))
        if isinstance(parsed, list):
            evidence = [ProposalEvidence.model_validate(e) for e in parsed]
    except (json.JSONDecodeError, ValueError):
        logger.warning("Corrupt evidence_json in agent_proposals row — ignoring")

    created_at = _dt(row["created_at"])
    updated_at = _dt(row["updated_at"])
    assert created_at is not None and updated_at is not None  # NOT NULL columns
    return AgentProposal(
        proposal_id=str(row["proposal_id"]),
        env_name=str(row["env_name"]),
        vm_id=str(row["vm_id"]),
        kind=ProposalKind(str(row["kind"])),
        action_type=str(row["action_type"] or ""),
        signal_kind=str(row["signal_kind"]),
        origin=str(row["origin"]),
        probe_id=str(row["probe_id"] or ""),
        evidence=evidence,
        confidence=str(row["confidence"]),
        status=ProposalStatus(str(row["status"])),
        created_at=created_at,
        updated_at=updated_at,
        expires_at=_dt(row["expires_at"]),
        decided_by=str(row["decided_by"]) if row["decided_by"] is not None else None,
        decided_by_group=(
            str(row["decided_by_group"]) if row["decided_by_group"] is not None else None
        ),
        decided_at=_dt(row["decided_at"]),
        snoozed_until=_dt(row["snoozed_until"]),
        execution_started_at=_dt(row["execution_started_at"]),
        execution_status=(
            str(row["execution_status"]) if row["execution_status"] is not None else None
        ),
    )
