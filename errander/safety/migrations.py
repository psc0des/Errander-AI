"""Database schema migration framework.

Numbered, idempotent migrations tracked in the schema_migrations table.
SQL is written for PostgreSQL portability — no SQLite-specific types or
pragmas. The INTEGER PRIMARY KEY in SQLite maps to SERIAL/BIGSERIAL in PG.

Called by AuditStore.initialize() on every startup. Each migration runs in
its own transaction so a partial failure leaves prior migrations intact.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Migration registry
# Each entry is (version: int, sql: str).  SQL may contain multiple
# statements separated by ";".  Statements are executed individually.
# ---------------------------------------------------------------------------

_MIGRATIONS: list[tuple[int, str]] = [
    # 0000 — original audit_events table (moved from inline DDL in AuditStore)
    (
        0,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            vm_id TEXT,
            action_type TEXT,
            detail TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_audit_batch
            ON audit_events (batch_id);
        CREATE INDEX IF NOT EXISTS idx_audit_vm
            ON audit_events (vm_id);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_events (timestamp DESC)
        """,
    ),
    # 0001 — lightweight mutable per-VM state (needs_reboot, uptime)
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS vm_state (
            vm_id TEXT PRIMARY KEY,
            needs_reboot INTEGER NOT NULL DEFAULT 0,
            needs_reboot_reason TEXT,
            needs_reboot_pkgs TEXT,
            needs_reboot_detected_at TEXT,
            last_uptime_seconds REAL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    # 0002 — per-kind drift baselines (authorized_keys, sudoers, ports, cron)
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS vm_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            baseline_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL DEFAULT '',
            captured_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_blob TEXT NOT NULL,
            metadata TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vm_baselines_lookup
            ON vm_baselines (vm_id, baseline_kind, scope_key, captured_at DESC)
        """,
    ),
    # 0003 — disk usage history for trend detection (90-day retention)
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS vm_disk_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            mountpoint TEXT NOT NULL,
            used_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vm_disk_history_lookup
            ON vm_disk_history (vm_id, mountpoint, captured_at DESC)
        """,
    ),
]


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply any pending database schema migrations.

    Idempotent — safe to call on every startup.  Each migration is wrapped
    in its own implicit transaction (aiosqlite commits per execute + commit).

    Args:
        db: Open aiosqlite connection.  The caller owns the connection lifecycle.
    """
    # Bootstrap: schema_migrations must exist before we can read from it.
    # This CREATE is idempotent; migration 0 will also run it (harmless).
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    await db.commit()

    cursor = await db.execute("SELECT version FROM schema_migrations ORDER BY version")
    rows = await cursor.fetchall()
    applied: set[int] = {int(str(row[0])) for row in rows}

    for version, sql in _MIGRATIONS:
        if version in applied:
            continue

        logger.info("Applying database migration %04d", version)
        for raw_stmt in sql.split(";"):
            stmt = raw_stmt.strip()
            if stmt:
                await db.execute(stmt)

        await db.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(tz=UTC).isoformat()),
        )
        await db.commit()
        logger.info("Migration %04d applied", version)
