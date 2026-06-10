"""Tests for the startup orphan-batch scanner (Phase A1.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from errander.db.core import AsyncDatabase
from errander.observability.startup_scan import scan_orphan_batches
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


async def _insert_event(
    db: AsyncDatabase,
    *,
    event_type: str,
    batch_id: str,
    timestamp: datetime | None = None,
    vm_id: str | None = None,
    action_type: str | None = None,
    detail: str = "",
) -> None:
    ts = (timestamp or datetime.now(tz=UTC)).isoformat()
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_events"
                " (event_type, batch_id, vm_id, action_type, detail, timestamp, metadata)"
                " VALUES (:et, :bid, :vid, :at, :det, :ts, '{}')"
            ),
            {"et": event_type, "bid": batch_id, "vid": vm_id,
             "at": action_type, "det": detail, "ts": ts},
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanOrphanBatches:
    async def test_empty_db_returns_zero(self) -> None:
        async with _make_db() as db:
            count = await scan_orphan_batches(db)
        assert count == 0

    async def test_completed_batch_not_counted(self) -> None:
        async with _make_db() as db:
            await _insert_event(db, event_type="batch_started", batch_id="batch-ok")
            await _insert_event(db, event_type="batch_completed", batch_id="batch-ok")
            count = await scan_orphan_batches(db)
        assert count == 0

    async def test_aborted_batch_not_counted(self) -> None:
        async with _make_db() as db:
            await _insert_event(db, event_type="batch_started", batch_id="batch-abort")
            await _insert_event(db, event_type="fleet_abort", batch_id="batch-abort")
            count = await scan_orphan_batches(db)
        assert count == 0

    async def test_interrupted_batch_counted(self) -> None:
        async with _make_db() as db:
            await _insert_event(db, event_type="batch_started", batch_id="batch-stuck")
            await _insert_event(db, event_type="action_started", batch_id="batch-stuck")
            count = await scan_orphan_batches(db)
        assert count == 1

    async def test_old_interrupted_batch_outside_window_not_counted(self) -> None:
        """Batches older than 7 days should not be reported."""
        old_ts = datetime.now(tz=UTC) - timedelta(days=10)
        async with _make_db() as db:
            await _insert_event(
                db, event_type="batch_started", batch_id="old-batch", timestamp=old_ts,
            )
            count = await scan_orphan_batches(db)
        assert count == 0

    async def test_mixed_batches_only_interrupted_counted(self) -> None:
        """Seed completed + in-flight + interrupted; only interrupted returned."""
        now = datetime.now(tz=UTC)
        async with _make_db() as db:
            # completed
            await _insert_event(db, event_type="batch_started", batch_id="done", timestamp=now)
            await _insert_event(db, event_type="batch_completed", batch_id="done", timestamp=now)

            # fleet-aborted (terminal — not orphaned)
            await _insert_event(db, event_type="batch_started", batch_id="aborted", timestamp=now)
            await _insert_event(db, event_type="fleet_abort", batch_id="aborted", timestamp=now)

            # interrupted (no terminal event)
            await _insert_event(db, event_type="batch_started", batch_id="stuck-1", timestamp=now)
            await _insert_event(db, event_type="action_started", batch_id="stuck-1", timestamp=now)

            count = await scan_orphan_batches(db)

        assert count == 1

    async def test_multiple_interrupted_batches(self) -> None:
        now = datetime.now(tz=UTC)
        async with _make_db() as db:
            for i in range(3):
                await _insert_event(
                    db, event_type="batch_started", batch_id=f"stuck-{i}", timestamp=now,
                )
            count = await scan_orphan_batches(db)
        assert count == 3

    async def test_reports_last_seen_event(self, caplog: pytest.LogCaptureFixture) -> None:
        now = datetime.now(tz=UTC)
        async with _make_db() as db:
            await _insert_event(db, event_type="batch_started", batch_id="stuck", timestamp=now)
            await _insert_event(db, event_type="action_started", batch_id="stuck", timestamp=now)
            with caplog.at_level("WARNING"):
                count = await scan_orphan_batches(db)
        assert count == 1
        assert "stuck" in caplog.text
        assert "action_started" in caplog.text
