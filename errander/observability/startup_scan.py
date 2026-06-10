"""Startup orphan-batch scanner (Phase A1.2).

Called once at agent startup, after run_migrations and before the scheduler.
Detects batches that started in the last 7 days but never reached a terminal
event (BATCH_COMPLETED or FLEET_ABORT), logs each as a WARNING, and returns
the count so the caller can increment BATCHES_INTERRUPTED_TOTAL.

No schema changes in Phase A1 — purely reads audit_events.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

logger = logging.getLogger(__name__)

_SCAN_WINDOW_DAYS = 7


async def scan_orphan_batches(db: AsyncDatabase) -> int:
    """Detect interrupted batches in the last 7 days and log each as WARNING.

    A batch is "interrupted" if it has a BATCH_STARTED event in the window
    but no BATCH_COMPLETED or FLEET_ABORT event.

    Returns:
        Number of interrupted batches found.  The caller is responsible for
        incrementing BATCHES_INTERRUPTED_TOTAL by this count.
    """
    cutoff = (datetime.now(tz=UTC) - timedelta(days=_SCAN_WINDOW_DAYS)).isoformat()

    async with db.begin() as conn:
        result = await conn.execute(
            text("""
            SELECT batch_id, MIN(timestamp) AS started_at
            FROM audit_events
            WHERE event_type = 'batch_started'
              AND timestamp >= :cutoff
            GROUP BY batch_id
            """),
            {"cutoff": cutoff},
        )
        started_rows = result.fetchall()

    interrupted: list[dict[str, str]] = []
    order_col = "rowid" if db.dialect == "sqlite" else "id"
    for row in started_rows:
        batch_id = str(row[0])
        started_at = str(row[1])

        async with db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT event_type
                FROM audit_events
                WHERE batch_id = :batch_id
                  AND event_type IN ('batch_completed', 'fleet_abort')
                LIMIT 1
                """),
                {"batch_id": batch_id},
            )
            terminal_rows = result.fetchall()

        if terminal_rows:
            continue

        async with db.begin() as conn:
            result = await conn.execute(
                text(f"""
                SELECT event_type, timestamp AS last_at
                FROM audit_events
                WHERE batch_id = :batch_id
                ORDER BY timestamp DESC, {order_col} DESC
                LIMIT 1
                """),
                {"batch_id": batch_id},
            )
            last_rows = result.fetchall()

        last_event_type = "unknown"
        last_seen_at = started_at
        if last_rows and last_rows[0][0] is not None:
            last_event_type = str(last_rows[0][0])
            last_seen_at = str(last_rows[0][1])

        interrupted.append({
            "batch_id": batch_id,
            "started_at": started_at,
            "last_seen_event_type": last_event_type,
            "last_seen_at": last_seen_at,
        })

    for orphan in interrupted:
        logger.warning(
            "Orphaned batch detected: batch_id=%s started_at=%s "
            "last_seen_event_type=%s last_seen_at=%s",
            orphan["batch_id"],
            orphan["started_at"],
            orphan["last_seen_event_type"],
            orphan["last_seen_at"],
        )

    return len(interrupted)
