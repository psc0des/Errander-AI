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
    # 0004 — live resource metrics time-series (CPU, MEM, DISK% per mountpoint)
    #
    # Collected every 60 s by errander.observability.vm_metrics.collect_all().
    # Retention: 8 days (cleaned up hourly by cleanup_old_metrics()).
    # At 60 s cadence, ~6 metrics/VM, 11 VMs → ~790 k rows/week → trivial for SQLite.
    #
    # metric examples: 'cpu', 'mem', 'disk_/', 'disk_/var', 'disk_/tmp'
    # value_pct is 0-100 float (percentage utilisation)
    # ts is Unix epoch integer seconds (consistent with strftime('%s','now') in SQLite)
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS vm_metrics (
            hostname   TEXT    NOT NULL,
            metric     TEXT    NOT NULL,
            value_pct  REAL    NOT NULL,
            ts         INTEGER NOT NULL,
            PRIMARY KEY (hostname, metric, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_vm_metrics_lookup
            ON vm_metrics (hostname, metric, ts DESC)
        """,
    ),
    # 0005 — batch lifecycle table for LangGraph workflow durability (Project A, A2)
    #
    # One row per batch run. Inserted as RUNNING at batch start; updated to a
    # terminal status (completed, completed_with_failures, aborted,
    # needs_operator_review) when the orchestrator graph finishes or aborts.
    #
    # dry_run is stored as INTEGER (0/1, SQLite boolean convention).
    # finished_at is NULL while the batch is still running.
    # error captures the abort reason for non-success terminal states.
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS batches (
            id          TEXT    PRIMARY KEY,
            env_name    TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'running',
            started_at  TEXT    NOT NULL,
            finished_at TEXT,
            dry_run     INTEGER NOT NULL DEFAULT 1,
            vm_count    INTEGER NOT NULL DEFAULT 0,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_batches_status
            ON batches (status);
        CREATE INDEX IF NOT EXISTS idx_batches_started_at
            ON batches (started_at DESC)
        """,
    ),
    # 0006 — artifact store for oversized graph state blobs (Project A, A4)
    #
    # Subgraph state fields exceeding 4 KB (patch_output, prune_output,
    # rotation_output, version_snapshot) are stored here instead of inside
    # LangGraph checkpoint state.  The subgraph stores the artifact_id (a
    # UUID4 string, ~36 bytes) and looks up the blob for reporting.
    #
    # Retention: blobs are purged by ArtifactStore.purge_before() after
    # batch reporting completes.  No auto-vacuum — caller is responsible.
    (
        6,
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id            TEXT    PRIMARY KEY,
            batch_id      TEXT    NOT NULL,
            vm_id         TEXT    NOT NULL,
            artifact_kind TEXT    NOT NULL,
            content       TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_batch
            ON artifacts (batch_id, vm_id, artifact_kind);
        CREATE INDEX IF NOT EXISTS idx_artifacts_created_at
            ON artifacts (created_at)
        """,
    ),
    # 0007 — agent lease table for single-process enforcement (Project A, A5)
    #
    # Exactly one row (id=1) when an agent process is running.
    # Acquired at startup via AgentLease.acquire(); released at shutdown via
    # AgentLease.release().  Heartbeat updated every 30 s.  Leases older
    # than 90 s are considered expired and can be evicted by a new agent.
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS agent_lease (
            id             INTEGER PRIMARY KEY DEFAULT 1,
            pid            INTEGER NOT NULL,
            hostname       TEXT    NOT NULL,
            acquired_at    TEXT    NOT NULL,
            last_heartbeat TEXT    NOT NULL,
            CHECK (id = 1)
        )
        """,
    ),
    # 0008 — plan_snapshots: persists full plan JSON for each approval gate
    # invocation so operators can inspect the complete package list before
    # approving (P2-1). TTL-expired rows are not auto-deleted but are treated
    # as stale by the web endpoint (read-only, safe to leave).
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS plan_snapshots (
            plan_id    TEXT    PRIMARY KEY,
            batch_id   TEXT    NOT NULL,
            env_name   TEXT    NOT NULL DEFAULT '',
            plan_hash  TEXT    NOT NULL,
            plan_json  TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_plan_snapshots_batch
            ON plan_snapshots (batch_id)
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
