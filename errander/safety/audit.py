"""Audit logging for all agent actions.

Every action is logged to the audit trail BEFORE and AFTER execution.
Audit events are immutable — written to PostgreSQL.

The audit trail answers: what happened, when, to which VM, by which batch,
and what was the outcome.

Schema uses TEXT types and ISO timestamps for cross-dialect compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from errander.models.events import AuditEvent, EventType
from errander.safety.migrations import run_migrations

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
    from errander.safety.artifacts import ArtifactStore
    from errander.safety.batches import BatchStore

logger = logging.getLogger(__name__)


class AuditWriteError(RuntimeError):
    """Raised when an audit write fails in strict mode (finding #13).

    In strict mode, live production actions abort rather than continue
    silently with a missing audit trail.
    """


_INSERT_SQL = """
INSERT INTO audit_events (event_type, batch_id, vm_id, action_type, detail, timestamp, metadata)
VALUES (:event_type, :batch_id, :vm_id, :action_type, :detail, :timestamp, :metadata)
"""

_SELECT_SQL = """
SELECT event_type, batch_id, vm_id, action_type, detail, timestamp, metadata
FROM audit_events
"""


class AuditStore:
    """Async database-backed audit event store (PostgreSQL).

    Usage::

        db = AsyncDatabase("postgresql://errander:errander@localhost/errander")
        async with AuditStore(db) as store:
            await store.log_event(event)
            events = await store.get_events(batch_id="run-123")

    For testing, use the make_test_db() helper from tests/conftest.py.
    """

    def __init__(self, db: AsyncDatabase, strict_mode: bool = True) -> None:
        self._db = db
        self._strict_mode = strict_mode

    async def initialize(self) -> None:
        """Apply all pending schema migrations."""
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        """Dispose the underlying database engine."""
        await self._db.close()

    async def __aenter__(self) -> AuditStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def make_batch_store(self) -> BatchStore:
        """Return a BatchStore sharing this store's database."""
        from errander.safety.batches import BatchStore as _BatchStore
        return _BatchStore(self._db)

    def make_artifact_store(self) -> ArtifactStore:
        """Return an ArtifactStore sharing this store's database."""
        from errander.safety.artifacts import ArtifactStore as _ArtifactStore
        return _ArtifactStore(self._db)

    async def log_event(self, event: AuditEvent, dry_run: bool = False) -> None:
        """Write an audit event to the persistent store.

        In strict mode (default): raises AuditWriteError after retry exhaustion
        for live actions so the caller can abort rather than continue silently
        with a missing audit trail (finding #13).

        In best-effort mode (dry_run=True or strict_mode=False): logs the
        error and continues — acceptable for sandbox runs where the audit trail
        is informational only.

        Args:
            event: The audit event to record.
            dry_run: When True, always uses best-effort (never raises).
        """
        metadata_json = json.dumps(event.metadata, default=str, ensure_ascii=False)
        timestamp_iso = event.timestamp.isoformat()
        params = {
            "event_type": event.event_type.value,
            "batch_id": event.batch_id,
            "vm_id": event.vm_id,
            "action_type": event.action_type,
            "detail": event.detail,
            "timestamp": timestamp_iso,
            "metadata": metadata_json,
        }
        fail_closed = self._strict_mode and not dry_run

        for attempt in (1, 2):
            try:
                async with self._db.begin() as conn:
                    await conn.execute(text(_INSERT_SQL), params)
                return
            except OperationalError as exc:
                if attempt == 1:
                    logger.warning("Audit write retry (%s)", exc)
                    await asyncio.sleep(0.1)
                    continue
                logger.error("Audit write failed after retry: %s", exc)
                if fail_closed:
                    raise AuditWriteError(
                        f"Audit write failed in strict mode — aborting live action: {exc}"
                    ) from exc
                return
            except SQLAlchemyError as exc:
                logger.error("Audit write failed: %s", exc)
                if fail_closed:
                    raise AuditWriteError(
                        f"Audit write failed in strict mode — aborting live action: {exc}"
                    ) from exc
                return

    async def get_events(
        self,
        batch_id: str | None = None,
        vm_id: str | None = None,
        event_type: EventType | None = None,
        action_type: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events with optional filters.

        Args:
            batch_id: Filter by batch run.
            vm_id: Filter by VM.
            event_type: Filter by event type.
            action_type: Filter by action type (e.g. "disk_cleanup").
            limit: Maximum events to return.

        Returns:
            List of matching audit events, most recent first.
        """
        clauses: list[str] = []
        params_dict: dict[str, object] = {}

        if batch_id is not None:
            clauses.append("batch_id = :batch_id")
            params_dict["batch_id"] = batch_id
        if vm_id is not None:
            clauses.append("vm_id = :vm_id")
            params_dict["vm_id"] = vm_id
        if event_type is not None:
            clauses.append("event_type = :event_type")
            params_dict["event_type"] = event_type.value
        if action_type is not None:
            clauses.append("action_type = :action_type")
            params_dict["action_type"] = action_type

        params_dict["limit"] = limit

        query = _SELECT_SQL
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC, id DESC LIMIT :limit"

        async with self._db.begin() as conn:
            result = await conn.execute(text(query), params_dict)
            rows = result.fetchall()
        return [_row_to_event(row) for row in rows]

    async def get_recent_batches(self, limit: int = 20) -> list[dict[str, object]]:
        """Return a summary of the most recent batch runs.

        Each entry contains:
            - batch_id: str
            - started_at: str (ISO timestamp of first event in batch)
            - event_count: int (total events in batch)
            - vm_ids: list[str] (distinct VMs touched)

        Args:
            limit: Maximum number of batches to return.

        Returns:
            List of batch summaries, most recent first.
        """
        query = """
        SELECT
            batch_id,
            MIN(timestamp) AS started_at,
            COUNT(*) AS event_count,
            STRING_AGG(DISTINCT vm_id, ',') AS vm_ids
        FROM audit_events
        GROUP BY batch_id
        ORDER BY started_at DESC
        LIMIT :limit
        """
        async with self._db.begin() as conn:
            result = await conn.execute(text(query), {"limit": limit})
            rows = result.fetchall()

        output: list[dict[str, object]] = []
        for row in rows:
            vm_ids_raw = row[3]
            vm_ids: list[str] = (
                [v for v in str(vm_ids_raw).split(",") if v and v != "None"]
                if vm_ids_raw is not None
                else []
            )
            output.append({
                "batch_id": str(row[0]),
                "started_at": str(row[1]),
                "event_count": int(str(row[2])),
                "vm_ids": vm_ids,
            })
        return output

    async def get_monitoring_stats(
        self,
        daily_days: int = 7,
        summary_days: int = 30,
    ) -> dict[str, object]:
        """Return aggregated stats for the monitoring page.

        Returns a dict with keys: ``summary``, ``daily``, ``by_type``,
        ``approvals``, ``safety``.
        """
        from datetime import timedelta

        now = datetime.now(UTC)
        summary_cutoff = (now - timedelta(days=summary_days)).isoformat()
        daily_cutoff = (now - timedelta(days=daily_days)).isoformat()

        async with self._db.begin() as conn:
            # 30-day summary totals
            s_result = await conn.execute(
                text("""
                SELECT
                    SUM(CASE WHEN event_type = 'action_completed' THEN 1 ELSE 0 END) AS succeeded,
                    SUM(CASE WHEN event_type = 'action_failed'    THEN 1 ELSE 0 END) AS failed,
                    COUNT(DISTINCT CASE WHEN vm_id IS NOT NULL AND vm_id != '' THEN vm_id END) AS active_vms,
                    COUNT(DISTINCT batch_id) AS total_batches
                FROM audit_events
                WHERE action_type IS NOT NULL AND action_type != ''
                  AND timestamp >= :cutoff
                """),
                {"cutoff": summary_cutoff},
            )
            s = s_result.fetchone()
            succeeded = int(s[0] or 0) if s else 0
            failed = int(s[1] or 0) if s else 0
            total = succeeded + failed
            success_rate = round(100.0 * succeeded / total, 1) if total > 0 else 0.0
            summary: dict[str, object] = {
                "total_actions": total,
                "succeeded": succeeded,
                "failed": failed,
                "success_rate": success_rate,
                "active_vms": int(s[2] or 0) if s else 0,
                "total_batches": int(s[3] or 0) if s else 0,
            }

            # Daily breakdown (last daily_days days)
            d_result = await conn.execute(
                text("""
                SELECT
                    date(timestamp) AS day,
                    action_type,
                    SUM(CASE WHEN event_type = 'action_completed' THEN 1 ELSE 0 END) AS ok,
                    SUM(CASE WHEN event_type = 'action_failed'    THEN 1 ELSE 0 END) AS fail
                FROM audit_events
                WHERE action_type IS NOT NULL AND action_type != ''
                  AND event_type IN ('action_completed', 'action_failed')
                  AND timestamp >= :cutoff
                GROUP BY day, action_type
                ORDER BY day, action_type
                """),
                {"cutoff": daily_cutoff},
            )
            daily: list[dict[str, object]] = [
                {
                    "day": str(r[0]),
                    "action_type": str(r[1]),
                    "ok": int(r[2] or 0),
                    "fail": int(r[3] or 0),
                }
                for r in d_result.fetchall()
            ]

            # Per-type totals (last summary_days days), sorted by volume desc
            t_result = await conn.execute(
                text("""
                SELECT
                    action_type,
                    SUM(CASE WHEN event_type = 'action_completed' THEN 1 ELSE 0 END) AS ok,
                    SUM(CASE WHEN event_type = 'action_failed'    THEN 1 ELSE 0 END) AS fail
                FROM audit_events
                WHERE action_type IS NOT NULL AND action_type != ''
                  AND event_type IN ('action_completed', 'action_failed')
                  AND timestamp >= :cutoff
                GROUP BY action_type
                ORDER BY COUNT(*) DESC
                """),
                {"cutoff": summary_cutoff},
            )
            by_type: list[dict[str, object]] = [
                {
                    "action_type": str(r[0]),
                    "ok": int(r[1] or 0),
                    "fail": int(r[2] or 0),
                }
                for r in t_result.fetchall()
            ]

            # Approval funnel (last summary_days days)
            a_result = await conn.execute(
                text("""
                SELECT
                    SUM(CASE WHEN event_type = 'approval_requested' THEN 1 ELSE 0 END) AS requested,
                    SUM(CASE WHEN event_type = 'approval_granted'   THEN 1 ELSE 0 END) AS granted,
                    SUM(CASE WHEN event_type = 'approval_rejected'  THEN 1 ELSE 0 END) AS rejected,
                    SUM(CASE WHEN event_type = 'approval_timeout'   THEN 1 ELSE 0 END) AS timed_out
                FROM audit_events
                WHERE event_type IN
                    ('approval_requested','approval_granted','approval_rejected','approval_timeout')
                  AND timestamp >= :cutoff
                """),
                {"cutoff": summary_cutoff},
            )
            ar = a_result.fetchone()
            apv_requested = int(ar[0] or 0) if ar else 0
            apv_granted   = int(ar[1] or 0) if ar else 0
            apv_rejected  = int(ar[2] or 0) if ar else 0
            apv_timed_out = int(ar[3] or 0) if ar else 0
            apv_responded = apv_granted + apv_rejected
            apv_rate = (
                round(100.0 * apv_responded / apv_requested, 1)
                if apv_requested > 0 else 100.0
            )
            approvals: dict[str, object] = {
                "requested": apv_requested,
                "granted": apv_granted,
                "rejected": apv_rejected,
                "timed_out": apv_timed_out,
                "response_rate": apv_rate,
            }

            # Safety & health signals (last summary_days days)
            sig_result = await conn.execute(
                text("""
                SELECT event_type, COUNT(*) AS cnt
                FROM audit_events
                WHERE event_type IN (
                    'drift_detected','drift_kind_changed',
                    'sudo_preflight_failed','target_preflight_failed','disk_gate_blocked',
                    'reboot_required_detected','service_health_regression',
                    'failed_ssh_logins_observed'
                )
                AND timestamp >= :cutoff
                GROUP BY event_type
                """),
                {"cutoff": summary_cutoff},
            )
            counts: dict[str, int] = {str(r[0]): int(r[1] or 0) for r in sig_result.fetchall()}
            safety: dict[str, object] = {
                "drift_detected": (
                    counts.get("drift_detected", 0) + counts.get("drift_kind_changed", 0)
                ),
                "preflight_blocks": (
                    counts.get("sudo_preflight_failed", 0)
                    + counts.get("target_preflight_failed", 0)
                    + counts.get("disk_gate_blocked", 0)
                ),
                "reboot_required":     counts.get("reboot_required_detected", 0),
                "service_regressions": counts.get("service_health_regression", 0),
                "ssh_anomalies":       counts.get("failed_ssh_logins_observed", 0),
            }

        return {
            "summary": summary,
            "daily": daily,
            "by_type": by_type,
            "approvals": approvals,
            "safety": safety,
        }

    async def count_events(
        self,
        batch_id: str | None = None,
        vm_id: str | None = None,
    ) -> int:
        """Count audit events matching filters.

        Args:
            batch_id: Filter by batch run.
            vm_id: Filter by VM.

        Returns:
            Number of matching events.
        """
        clauses: list[str] = []
        params_dict: dict[str, object] = {}

        if batch_id is not None:
            clauses.append("batch_id = :batch_id")
            params_dict["batch_id"] = batch_id
        if vm_id is not None:
            clauses.append("vm_id = :vm_id")
            params_dict["vm_id"] = vm_id

        query = "SELECT COUNT(*) FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        async with self._db.begin() as conn:
            result = await conn.execute(text(query), params_dict)
            row = result.fetchone()
        return int(row[0]) if row else 0

    async def save_plan_snapshot(
        self,
        plan_id: str,
        batch_id: str,
        env_name: str,
        plan_hash: str,
        plan_json: str,
    ) -> None:
        """Persist the full plan JSON for later inspection (P2-1).

        Idempotent — duplicate plan_id is silently ignored (plan is immutable
        once generated).
        """
        now_ts = datetime.now(UTC).isoformat()
        try:
            async with self._db.begin() as conn:
                await conn.execute(
                    text("""
                    INSERT INTO plan_snapshots
                        (plan_id, batch_id, env_name, plan_hash, plan_json, created_at)
                    VALUES (:plan_id, :batch_id, :env_name, :plan_hash, :plan_json, :created_at)
                    ON CONFLICT(plan_id) DO NOTHING
                    """),
                    {
                        "plan_id": plan_id,
                        "batch_id": batch_id,
                        "env_name": env_name,
                        "plan_hash": plan_hash,
                        "plan_json": plan_json,
                        "created_at": now_ts,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save plan snapshot %s: %s", plan_id, exc)

    async def get_plan_snapshot(self, plan_id: str) -> dict[str, object] | None:
        """Retrieve a stored plan snapshot by plan_id (P2-1).

        Returns None when the plan_id is not found.
        """
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT plan_id, batch_id, env_name, plan_hash, plan_json, created_at "
                    "FROM plan_snapshots WHERE plan_id = :plan_id"
                ),
                {"plan_id": plan_id},
            )
            row = result.fetchone()
        if row is None:
            return None
        return {
            "plan_id": str(row[0]),
            "batch_id": str(row[1]),
            "env_name": str(row[2]),
            "plan_hash": str(row[3]),
            "plan_json": str(row[4]),
            "created_at": str(row[5]),
        }


def _row_to_event(row: Any) -> AuditEvent:
    """Convert a database row to an AuditEvent."""
    return AuditEvent(
        event_type=EventType(str(row[0])),
        batch_id=str(row[1]),
        vm_id=str(row[2]) if row[2] is not None else None,
        action_type=str(row[3]) if row[3] is not None else None,
        detail=str(row[4]),
        timestamp=datetime.fromisoformat(str(row[5])),
        metadata=json.loads(str(row[6])),
    )
