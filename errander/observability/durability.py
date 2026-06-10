"""Durability measurement logic for --measure-durability CLI (Phase A1.3).

Queries audit_events directly (no Prometheus dependency) and computes:
- Batch completion rate over a configurable window
- Batch duration percentiles (p50/p95/max)
- Approval wait percentiles and outcome counts
- Per-action duration percentiles
- Interrupted batch count (proxy for agent restarts during a live batch)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

logger = logging.getLogger(__name__)


@dataclass
class ActionDurabilityStats:
    """Duration stats for one action type."""

    action_type: str
    p50: float
    p95: float
    max_duration: float
    sample_size: int


@dataclass
class DurabilityReport:
    """Computed durability snapshot over a time window."""

    window_days: int
    total_batches: int
    completed_batches: int
    interrupted_batches: int
    completion_rate: float
    batch_duration_p50: float
    batch_duration_p95: float
    batch_duration_max: float
    batch_duration_sample: int
    approval_wait_p50: float
    approval_wait_p95: float
    approval_wait_max: float
    approval_wait_sample: int
    approval_auto_rejected: int
    approval_granted: int
    approval_rejected: int
    action_stats: list[ActionDurabilityStats] = field(default_factory=list)
    agent_restarts_during_batch: int = 0


def _pct(values: list[float], p: int) -> float:
    """Return the p-th percentile of values using nearest-rank method."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def _parse_ts(s: str) -> datetime:
    """Parse an ISO timestamp string, handling naive and aware variants."""
    return datetime.fromisoformat(s)


async def compute_durability_report(
    db: AsyncDatabase,
    window_days: int,
) -> DurabilityReport:
    """Query audit_events and compute a DurabilityReport."""
    cutoff = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()

    # ── 1. Batch starts in window ───────────────────────────────────────────
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, MIN(timestamp) AS start_at
            FROM audit_events
            WHERE event_type = 'batch_started' AND timestamp >= :cutoff
            GROUP BY batch_id
            """),
            {"cutoff": cutoff},
        )
        batch_start_rows = result.fetchall()

    batch_starts: dict[str, str] = {str(r[0]): str(r[1]) for r in batch_start_rows}
    total_batches = len(batch_starts)

    # ── 2. Batch terminal events ────────────────────────────────────────────
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, event_type, MIN(timestamp) AS end_at
            FROM audit_events
            WHERE event_type IN ('batch_completed', 'fleet_abort')
              AND batch_id IN (
                  SELECT DISTINCT batch_id FROM audit_events
                  WHERE event_type = 'batch_started' AND timestamp >= :cutoff
              )
            GROUP BY batch_id, event_type
            """),
            {"cutoff": cutoff},
        )
        batch_term_rows = result.fetchall()

    batch_completed: dict[str, str] = {}
    batch_aborted: dict[str, str] = {}
    for row in batch_term_rows:
        bid, etype, end_at = str(row[0]), str(row[1]), str(row[2])
        if etype == "batch_completed":
            batch_completed[bid] = end_at
        else:
            batch_aborted[bid] = end_at

    terminal_ids = set(batch_completed) | set(batch_aborted)
    completed_count = len({bid for bid in batch_starts if bid in batch_completed})
    interrupted_count = sum(1 for bid in batch_starts if bid not in terminal_ids)
    completion_rate = completed_count / total_batches * 100.0 if total_batches else 0.0

    # ── 3. Batch duration percentiles ───────────────────────────────────────
    batch_durations: list[float] = []
    for bid, start_str in batch_starts.items():
        if bid not in batch_completed:
            continue
        end_str = batch_completed[bid]
        try:
            dur = (_parse_ts(end_str) - _parse_ts(start_str)).total_seconds()
            if dur >= 0:
                batch_durations.append(dur)
        except (ValueError, TypeError):
            pass

    # ── 4. Approval wait stats ───────────────────────────────────────────────
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, MIN(timestamp) AS req_at
            FROM audit_events
            WHERE event_type = 'approval_requested' AND timestamp >= :cutoff
            GROUP BY batch_id
            """),
            {"cutoff": cutoff},
        )
        appr_req_rows = result.fetchall()

    appr_reqs: dict[str, str] = {str(r[0]): str(r[1]) for r in appr_req_rows}

    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, event_type, MIN(timestamp) AS resp_at
            FROM audit_events
            WHERE event_type IN ('approval_granted', 'approval_rejected', 'approval_timeout')
            GROUP BY batch_id, event_type
            """),
        )
        appr_term_rows = result.fetchall()

    appr_terms: dict[str, tuple[str, str]] = {}
    for row in appr_term_rows:
        bid, etype, ts = str(row[0]), str(row[1]), str(row[2])
        if bid not in appr_terms or ts < appr_terms[bid][1]:
            appr_terms[bid] = (etype, ts)

    approval_waits: list[float] = []
    approved_count = rejected_count = auto_rejected_count = 0
    for bid, req_str in appr_reqs.items():
        if bid not in appr_terms:
            continue
        resp_type, resp_str = appr_terms[bid]
        try:
            wait = (_parse_ts(resp_str) - _parse_ts(req_str)).total_seconds()
            if wait >= 0:
                approval_waits.append(wait)
        except (ValueError, TypeError):
            pass
        if resp_type == "approval_granted":
            approved_count += 1
        elif resp_type == "approval_timeout":
            auto_rejected_count += 1
        else:
            rejected_count += 1

    # ── 5. Per-action duration stats ─────────────────────────────────────────
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, COALESCE(vm_id, ''), action_type, MIN(timestamp) AS start_at
            FROM audit_events
            WHERE event_type = 'action_started' AND timestamp >= :cutoff
              AND action_type IS NOT NULL
            GROUP BY batch_id, COALESCE(vm_id, ''), action_type
            """),
            {"cutoff": cutoff},
        )
        act_start_rows = result.fetchall()

    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, COALESCE(vm_id, ''), action_type, MIN(timestamp) AS end_at
            FROM audit_events
            WHERE event_type IN ('action_completed', 'action_failed')
              AND action_type IS NOT NULL
            GROUP BY batch_id, COALESCE(vm_id, ''), action_type
            """),
        )
        act_term_rows = result.fetchall()

    act_term_map: dict[tuple[str, str, str], str] = {
        (str(r[0]), str(r[1]), str(r[2])): str(r[3])
        for r in act_term_rows
    }

    action_durations: dict[str, list[float]] = {}
    for row in act_start_rows:
        bid, vm, at, start_str = str(row[0]), str(row[1]), str(row[2]), str(row[3])
        act_end_str: str | None = act_term_map.get((bid, vm, at))
        if act_end_str is None:
            continue
        try:
            dur = (_parse_ts(act_end_str) - _parse_ts(start_str)).total_seconds()
            if dur >= 0:
                action_durations.setdefault(at, []).append(dur)
        except (ValueError, TypeError):
            pass

    action_stats = [
        ActionDurabilityStats(
            action_type=at,
            p50=_pct(durs, 50),
            p95=_pct(durs, 95),
            max_duration=max(durs),
            sample_size=len(durs),
        )
        for at, durs in sorted(action_durations.items())
    ]

    return DurabilityReport(
        window_days=window_days,
        total_batches=total_batches,
        completed_batches=completed_count,
        interrupted_batches=interrupted_count,
        completion_rate=completion_rate,
        batch_duration_p50=_pct(batch_durations, 50),
        batch_duration_p95=_pct(batch_durations, 95),
        batch_duration_max=max(batch_durations) if batch_durations else 0.0,
        batch_duration_sample=len(batch_durations),
        approval_wait_p50=_pct(approval_waits, 50),
        approval_wait_p95=_pct(approval_waits, 95),
        approval_wait_max=max(approval_waits) if approval_waits else 0.0,
        approval_wait_sample=len(approval_waits),
        approval_auto_rejected=auto_rejected_count,
        approval_granted=approved_count,
        approval_rejected=rejected_count,
        action_stats=action_stats,
        agent_restarts_during_batch=interrupted_count,
    )


def print_durability_report(report: DurabilityReport) -> None:
    """Print the durability report to stdout."""
    print(f"Errander durability snapshot  window: last {report.window_days} days")
    print(
        f"  Batches:        total={report.total_batches}"
        f"   completed={report.completed_batches}"
        f"   interrupted={report.interrupted_batches}"
        f"   completion_rate={report.completion_rate:.1f}%"
    )

    def _fmt_s(secs: float) -> str:
        return f"{secs:.1f}s"

    print("  Batch duration (BATCH_STARTED -> BATCH_COMPLETED):")
    if report.batch_duration_sample > 0:
        print(
            f"    p50={_fmt_s(report.batch_duration_p50)}"
            f"   p95={_fmt_s(report.batch_duration_p95)}"
            f"   max={_fmt_s(report.batch_duration_max)}"
            f"   sample={report.batch_duration_sample}"
        )
    else:
        print("    (no completed batches in window)")

    print("  Approval wait (APPROVAL_REQUESTED -> first of GRANTED/REJECTED/TIMEOUT):")
    if report.approval_wait_sample > 0:
        print(
            f"    p50={_fmt_s(report.approval_wait_p50)}"
            f"   p95={_fmt_s(report.approval_wait_p95)}"
            f"   max={_fmt_s(report.approval_wait_max)}"
            f"   sample={report.approval_wait_sample}"
        )
        print(
            f"    auto-rejected={report.approval_auto_rejected}"
            f"   granted={report.approval_granted}"
            f"   rejected={report.approval_rejected}"
        )
    else:
        print("    (no approval events in window)")

    print("  Longest actions (ACTION_STARTED -> ACTION_COMPLETED/FAILED):")
    if report.action_stats:
        for stat in report.action_stats:
            print(
                f"    {stat.action_type:<20}"
                f"  p95={_fmt_s(stat.p95)}"
                f"   max={_fmt_s(stat.max_duration)}"
                f"   sample={stat.sample_size}"
            )
    else:
        print("    (no action events in window)")

    print(f"  Agent restarts during a live batch: {report.agent_restarts_during_batch}")
