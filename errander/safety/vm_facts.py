"""Operational learning memory — per-VM and per-action outcome facts (Phase B1).

Aggregates evidence-based facts from existing audit stores: action success rates,
reboot patterns, and approval rejection history.  Layer A only — read-only.  No
new tables; computes on demand from audit_events data already collected by the
agent.

The caller (OperatorAssistant) passes these facts into the LLM prompt so the
model can reason about historical patterns ("patching often fails on this VM
due to dpkg lock", "this action was repeatedly rejected by humans").
"""

from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import aiosqlite
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SAMPLE_SIZE = 20
_REJECTION_WINDOW_DAYS = 90


class ActionOutcomeFact(BaseModel):
    """Historical outcome statistics for one (vm_id, action_type) pair."""

    vm_id: str
    action_type: str
    success_rate: float
    sample_size: int
    last_failure_reason: str | None
    last_success_at: datetime | None


class VMRebootPatternFact(BaseModel):
    """How often a VM required a reboot following patching."""

    vm_id: str
    reboots_required_after_patching: int
    sample_size: int


class ActionRejectionFact(BaseModel):
    """Approval rejection history for an action type (last 90 days)."""

    action_type: str
    rejections_last_90d: int
    rejection_reasons: list[str]


class VMFactsStore:
    """Read-only store that computes operational-learning facts on demand.

    All queries run against the existing audit_events table — no migrations
    or new tables required.

    Usage:
        async with VMFactsStore("errander.sqlite") as store:
            outcomes = await store.action_outcomes("prod/web-01")
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

    async def __aenter__(self) -> VMFactsStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "VMFactsStore not initialized — call initialize() or use as async context manager"
            raise RuntimeError(msg)
        return self._db

    async def action_outcomes(
        self,
        vm_id: str,
        action_type: str | None = None,
    ) -> list[ActionOutcomeFact]:
        """Return success-rate facts for a VM, optionally filtered by action_type.

        Uses the last _SAMPLE_SIZE (20) terminal events per action_type.
        """
        db = self._ensure_connected()

        query = """
            SELECT event_type, action_type, detail, timestamp
            FROM audit_events
            WHERE vm_id = ?
              AND event_type IN ('action_completed', 'action_failed')
              AND action_type IS NOT NULL
        """
        params: list[str] = [vm_id]
        if action_type is not None:
            query += " AND action_type = ?"
            params.append(action_type)
        query += " ORDER BY timestamp DESC"

        rows = await db.execute_fetchall(query, params)

        groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for row in rows:
            et, at, detail, ts = str(row[0]), str(row[1]), str(row[2]), str(row[3])
            if len(groups[at]) < _SAMPLE_SIZE:
                groups[at].append((et, detail, ts))

        results: list[ActionOutcomeFact] = []
        for at, events in groups.items():
            success_count = sum(1 for et, _, _ in events if et == "action_completed")
            total = len(events)
            success_rate = success_count / total if total > 0 else 0.0

            last_failure_reason: str | None = None
            last_success_at: datetime | None = None
            for et, detail, ts in events:
                if et == "action_failed" and last_failure_reason is None:
                    last_failure_reason = detail.strip() or None
                if et == "action_completed" and last_success_at is None:
                    with suppress(ValueError):
                        last_success_at = datetime.fromisoformat(ts)

            results.append(ActionOutcomeFact(
                vm_id=vm_id,
                action_type=at,
                success_rate=success_rate,
                sample_size=total,
                last_failure_reason=last_failure_reason,
                last_success_at=last_success_at,
            ))

        return results

    async def reboot_pattern(self, vm_id: str) -> VMRebootPatternFact | None:
        """Return reboot pattern fact for a VM, or None if no patching history."""
        db = self._ensure_connected()

        reboot_rows = list(await db.execute_fetchall(
            """
            SELECT COUNT(*)
            FROM audit_events
            WHERE vm_id = ? AND event_type = 'reboot_required_detected'
            """,
            [vm_id],
        ))
        reboot_count = int(str(reboot_rows[0][0])) if reboot_rows else 0

        patching_rows = list(await db.execute_fetchall(
            """
            SELECT COUNT(*)
            FROM audit_events
            WHERE vm_id = ?
              AND action_type = 'patching'
              AND event_type IN ('action_completed', 'action_failed')
            """,
            [vm_id],
        ))
        sample = int(str(patching_rows[0][0])) if patching_rows else 0

        if sample == 0:
            return None

        return VMRebootPatternFact(
            vm_id=vm_id,
            reboots_required_after_patching=reboot_count,
            sample_size=sample,
        )

    async def rejection_facts(self) -> list[ActionRejectionFact]:
        """Return per-action-type rejection counts for the last 90 days.

        Infers action_type from ACTION_PLANNED/ACTION_STARTED events in the
        same batches that received APPROVAL_REJECTED.
        """
        db = self._ensure_connected()
        cutoff = (datetime.now(tz=UTC) - timedelta(days=_REJECTION_WINDOW_DAYS)).isoformat()

        rejected_rows = await db.execute_fetchall(
            """
            SELECT batch_id, detail
            FROM audit_events
            WHERE event_type = 'approval_rejected' AND timestamp >= ?
            """,
            [cutoff],
        )
        if not rejected_rows:
            return []

        rejected_batches: dict[str, str] = {
            str(r[0]): str(r[1]) for r in rejected_rows
        }

        class _Entry(TypedDict):
            count: int
            reasons: list[str]

        action_type_data: dict[str, _Entry] = {}

        for batch_id, reason in rejected_batches.items():
            planned_rows = await db.execute_fetchall(
                """
                SELECT DISTINCT action_type
                FROM audit_events
                WHERE batch_id = ? AND action_type IS NOT NULL
                """,
                [batch_id],
            )
            action_types = (
                [str(r[0]) for r in planned_rows]
                if planned_rows else ["(unknown)"]
            )
            for at in action_types:
                if at not in action_type_data:
                    action_type_data[at] = {"count": 0, "reasons": []}
                entry = action_type_data[at]
                entry["count"] += 1
                if reason and reason not in entry["reasons"]:
                    entry["reasons"].append(reason)

        return [
            ActionRejectionFact(
                action_type=at,
                rejections_last_90d=info["count"],
                rejection_reasons=info["reasons"][:10],
            )
            for at, info in sorted(action_type_data.items())
        ]
