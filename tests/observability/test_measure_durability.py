"""Tests for the --measure-durability computation logic (Phase A1.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from sqlalchemy import text

from errander.db.core import AsyncDatabase
from errander.observability.durability import (
    compute_durability_report,
    print_durability_report,
)
from errander.safety.migrations import run_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _make_db() -> AsyncIterator[AsyncDatabase]:
    db = AsyncDatabase(":memory:")
    async with db.begin() as conn:
        await run_migrations(conn, "sqlite")
    try:
        yield db
    finally:
        await db.close()


async def _insert(
    db: AsyncDatabase,
    event_type: str,
    batch_id: str,
    timestamp: datetime,
    vm_id: str | None = None,
    action_type: str | None = None,
    detail: str = "",
) -> None:
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events"
                " (event_type, batch_id, vm_id, action_type, detail, timestamp, metadata)"
                " VALUES (:et, :bid, :vid, :at, :det, :ts, '{}')"
            ),
            {"et": event_type, "bid": batch_id, "vid": vm_id,
             "at": action_type, "det": detail, "ts": timestamp.isoformat()},
        )


def _ts(offset_seconds: float = 0.0) -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=abs(offset_seconds))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyDatabase:
    async def test_empty_db_returns_zero_totals(self) -> None:
        async with _make_db() as db:
            r = await compute_durability_report(db, window_days=14)
        assert r.total_batches == 0
        assert r.completed_batches == 0
        assert r.interrupted_batches == 0
        assert r.completion_rate == 0.0
        assert r.batch_duration_sample == 0
        assert r.approval_wait_sample == 0
        assert r.action_stats == []


class TestBatchStats:
    async def test_completed_batch_counted(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(120))
            await _insert(db, "batch_completed", "b1", _ts(0))
            r = await compute_durability_report(db, 14)
        assert r.total_batches == 1
        assert r.completed_batches == 1
        assert r.interrupted_batches == 0
        assert r.completion_rate == pytest.approx(100.0)

    async def test_interrupted_batch_counted(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(120))
            await _insert(db, "action_started", "b1", _ts(60))
            r = await compute_durability_report(db, 14)
        assert r.total_batches == 1
        assert r.completed_batches == 0
        assert r.interrupted_batches == 1

    async def test_aborted_batch_not_interrupted(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(120))
            await _insert(db, "fleet_abort", "b1", _ts(0))
            r = await compute_durability_report(db, 14)
        assert r.total_batches == 1
        assert r.interrupted_batches == 0

    async def test_batch_duration_computed(self) -> None:
        start = _ts(300)
        end = _ts(0)
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", start)
            await _insert(db, "batch_completed", "b1", end)
            r = await compute_durability_report(db, 14)
        assert r.batch_duration_sample == 1
        assert r.batch_duration_p50 == pytest.approx(300.0, abs=2.0)
        assert r.batch_duration_p95 == pytest.approx(300.0, abs=2.0)
        assert r.batch_duration_max == pytest.approx(300.0, abs=2.0)

    async def test_completion_rate_calculation(self) -> None:
        async with _make_db() as db:
            # 2 completed, 1 interrupted
            for i in range(2):
                await _insert(db, "batch_started", f"ok-{i}", _ts(3600))
                await _insert(db, "batch_completed", f"ok-{i}", _ts(100))
            await _insert(db, "batch_started", "stuck", _ts(3600))
            r = await compute_durability_report(db, 14)
        assert r.total_batches == 3
        assert r.completed_batches == 2
        assert r.completion_rate == pytest.approx(66.67, abs=0.1)

    async def test_batch_outside_window_excluded(self) -> None:
        old = datetime.now(tz=UTC) - timedelta(days=20)
        async with _make_db() as db:
            await _insert(db, "batch_started", "old-batch", old)
            await _insert(db, "batch_completed", "old-batch", old)
            r = await compute_durability_report(db, 14)
        assert r.total_batches == 0


class TestApprovalWaitStats:
    async def test_approval_wait_computed(self) -> None:
        req_ts = _ts(600)
        granted_ts = _ts(0)
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(700))
            await _insert(db, "approval_requested", "b1", req_ts)
            await _insert(db, "approval_granted", "b1", granted_ts)
            r = await compute_durability_report(db, 14)
        assert r.approval_wait_sample == 1
        assert r.approval_wait_p50 == pytest.approx(600.0, abs=2.0)
        assert r.approval_granted == 1
        assert r.approval_rejected == 0
        assert r.approval_auto_rejected == 0

    async def test_approval_timeout_counted_as_auto_rejected(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(700))
            await _insert(db, "approval_requested", "b1", _ts(600))
            await _insert(db, "approval_timeout", "b1", _ts(0))
            r = await compute_durability_report(db, 14)
        assert r.approval_auto_rejected == 1
        assert r.approval_rejected == 0
        assert r.approval_granted == 0

    async def test_approval_rejected_counted(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(700))
            await _insert(db, "approval_requested", "b1", _ts(600))
            await _insert(db, "approval_rejected", "b1", _ts(0))
            r = await compute_durability_report(db, 14)
        assert r.approval_rejected == 1


class TestPerActionStats:
    async def test_per_action_duration_grouped(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(300))
            await _insert(db, "action_started", "b1", _ts(250), vm_id="vm1", action_type="patching")
            await _insert(db, "action_completed", "b1", _ts(0), vm_id="vm1", action_type="patching")
            r = await compute_durability_report(db, 14)
        assert len(r.action_stats) == 1
        stat = r.action_stats[0]
        assert stat.action_type == "patching"
        assert stat.sample_size == 1
        assert stat.p50 == pytest.approx(250.0, abs=2.0)

    async def test_multiple_action_types_separate(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "b1", _ts(600))
            await _insert(db, "action_started", "b1", _ts(550), vm_id="v1", action_type="disk_cleanup")
            await _insert(db, "action_completed", "b1", _ts(500), vm_id="v1", action_type="disk_cleanup")
            await _insert(db, "action_started", "b1", _ts(450), vm_id="v1", action_type="log_rotation")
            await _insert(db, "action_completed", "b1", _ts(400), vm_id="v1", action_type="log_rotation")
            r = await compute_durability_report(db, 14)
        action_types = {s.action_type for s in r.action_stats}
        assert "disk_cleanup" in action_types
        assert "log_rotation" in action_types

    async def test_p95_with_multiple_samples(self) -> None:
        """With 20 samples of varying duration, p50 and p95 are distinguishable."""
        async with _make_db() as db:
            base = datetime.now(tz=UTC)
            for i in range(20):
                duration_s = (i + 1) * 30  # durations: 30, 60, 90, ..., 600 s
                end = base - timedelta(days=1) - timedelta(seconds=i * 5)
                start = end - timedelta(seconds=duration_s)
                bid = f"b{i}"
                await _insert(db, "batch_started", bid, start - timedelta(seconds=10))
                await _insert(db, "action_started", bid, start, vm_id="v1", action_type="patching")
                await _insert(db, "action_completed", bid, end, vm_id="v1", action_type="patching")
            r = await compute_durability_report(db, 14)
        assert r.action_stats[0].sample_size == 20
        assert r.action_stats[0].p95 > r.action_stats[0].p50


class TestAgentRestarts:
    async def test_agent_restarts_equals_interrupted_batches(self) -> None:
        async with _make_db() as db:
            await _insert(db, "batch_started", "stuck1", _ts(3600))
            await _insert(db, "batch_started", "stuck2", _ts(3600))
            await _insert(db, "batch_started", "ok", _ts(3600))
            await _insert(db, "batch_completed", "ok", _ts(0))
            r = await compute_durability_report(db, 14)
        assert r.agent_restarts_during_batch == 2


class TestPrintDurabilityReport:
    async def test_print_does_not_raise(self) -> None:
        async with _make_db() as db:
            r = await compute_durability_report(db, 14)
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            print_durability_report(r)
            output = mock_out.getvalue()
        assert "Errander durability snapshot" in output
        assert "Batches:" in output
        assert "Batch duration" in output
        assert "Approval wait" in output
        assert "Agent restarts" in output

    async def test_print_shows_window_days(self) -> None:
        async with _make_db() as db:
            r = await compute_durability_report(db, 7)
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            print_durability_report(r)
            output = mock_out.getvalue()
        assert "last 7 days" in output
