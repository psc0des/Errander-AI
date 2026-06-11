"""Database schema migration framework (PostgreSQL).

Numbered, idempotent migrations tracked in the schema_migrations table.
DDL is written in PostgreSQL flavor — Errander-AI is PostgreSQL-only
(owner decision 2026-06-10).

Called by AuditStore.initialize() on every startup.  Each migration runs in
its own transaction so a partial failure leaves prior migrations intact.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration registry
# Each entry is (version: int, sql: str).  SQL may contain multiple
# statements separated by ";".  Statements are executed individually.
# ---------------------------------------------------------------------------

_MIGRATIONS: list[tuple[int, str]] = [
    # 0000 — original audit_events table
    (
        0,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id BIGSERIAL PRIMARY KEY,
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
            id BIGSERIAL PRIMARY KEY,
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
            id BIGSERIAL PRIMARY KEY,
            vm_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            mountpoint TEXT NOT NULL,
            used_bytes BIGINT NOT NULL,
            total_bytes BIGINT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vm_disk_history_lookup
            ON vm_disk_history (vm_id, mountpoint, captured_at DESC)
        """,
    ),
    # 0004 — live resource metrics time-series (CPU, MEM, DISK% per mountpoint)
    #
    # Collected every 60 s by errander.observability.vm_metrics.collect_all().
    # Retention: 8 days (cleaned up hourly by cleanup_old_metrics()).
    #
    # metric examples: 'cpu', 'mem', 'disk_/', 'disk_/var', 'disk_/tmp'
    # value_pct is 0-100 float (percentage utilisation)
    # ts is Unix epoch integer seconds
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS vm_metrics (
            hostname   TEXT    NOT NULL,
            metric     TEXT    NOT NULL,
            value_pct  REAL    NOT NULL,
            ts         BIGINT  NOT NULL,
            PRIMARY KEY (hostname, metric, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_vm_metrics_lookup
            ON vm_metrics (hostname, metric, ts DESC)
        """,
    ),
    # 0005 — batch lifecycle table for LangGraph workflow durability
    #
    # One row per batch run.  dry_run stored as INTEGER (0/1).
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
    # 0006 — artifact store for oversized graph state blobs
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
    # 0007 — agent lease table for single-process enforcement
    #
    # Exactly one row (id=1).  CHECK constraint enforces the singleton.
    # Note: no AUTOINCREMENT — DEFAULT 1 is used; works identically on PG.
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
    # 0009 — replay eval tables (AI Trust Layer Phase 2)
    (
        9,
        """
        CREATE TABLE IF NOT EXISTS ai_eval_runs (
            id           BIGSERIAL PRIMARY KEY,
            run_id       TEXT    NOT NULL UNIQUE,
            model        TEXT    NOT NULL,
            decision_type TEXT,
            source_count INTEGER NOT NULL DEFAULT 0,
            pass_count   INTEGER NOT NULL DEFAULT 0,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            error_count  INTEGER NOT NULL DEFAULT 0,
            timestamp    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_eval_runs_ts
            ON ai_eval_runs (timestamp DESC);
        CREATE TABLE IF NOT EXISTS ai_eval_results (
            id            BIGSERIAL PRIMARY KEY,
            run_id        TEXT    NOT NULL,
            original_id   INTEGER,
            decision_type TEXT    NOT NULL,
            model         TEXT    NOT NULL,
            prompt_hash   TEXT    NOT NULL,
            response_raw  TEXT,
            outcome       TEXT    NOT NULL,
            violations    TEXT,
            latency_ms    REAL,
            timestamp     TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_eval_results_run
            ON ai_eval_results (run_id);
        CREATE INDEX IF NOT EXISTS idx_eval_results_ts
            ON ai_eval_results (timestamp DESC)
        """,
    ),
    # 0010 — settings and inventory overrides (previously in OverridesStore.initialize())
    (
        10,
        """
        CREATE TABLE IF NOT EXISTS settings_overrides (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            is_secret INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT 'ui',
            note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS inventory_overrides (
            id BIGSERIAL PRIMARY KEY,
            env_name TEXT NOT NULL,
            vm_name TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('yaml_override', 'db_addition')),
            disabled INTEGER NOT NULL DEFAULT 0,
            host TEXT,
            ssh_user TEXT,
            ssh_key_path TEXT,
            os_family TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT 'ui',
            note TEXT DEFAULT '',
            UNIQUE(env_name, vm_name)
        )
        """,
    ),
    # 0011 — AI decision audit log (previously in AIDecisionStore.initialize())
    (
        11,
        """
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id          BIGSERIAL PRIMARY KEY,
            batch_id    TEXT NOT NULL,
            vm_id       TEXT,
            decision_type TEXT NOT NULL,
            model       TEXT NOT NULL,
            base_url    TEXT NOT NULL,
            prompt_template_id TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            response_raw TEXT,
            outcome     TEXT NOT NULL,
            latency_ms  REAL,
            prompt_tokens  INTEGER,
            completion_tokens INTEGER,
            timestamp   TEXT NOT NULL,
            prompt_full TEXT,
            context_snapshot TEXT,
            model_params TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_batch ON ai_decisions (batch_id);
        CREATE INDEX IF NOT EXISTS idx_ai_vm    ON ai_decisions (vm_id);
        CREATE INDEX IF NOT EXISTS idx_ai_ts    ON ai_decisions (timestamp DESC)
        """,
    ),
    # 0012 — deferred execution store (previously in DeferredExecutionStore.initialize())
    (
        12,
        """
        CREATE TABLE IF NOT EXISTS deferred_executions (
            id           BIGSERIAL PRIMARY KEY,
            batch_id     TEXT    NOT NULL UNIQUE,
            env_name     TEXT    NOT NULL,
            approved_at  TEXT    NOT NULL,
            approved_by  TEXT,
            window_start TEXT    NOT NULL,
            expiry_at    TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending',
            created_at   TEXT    NOT NULL,
            executed_at  TEXT,
            plan_json    TEXT,
            plan_hash    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_deferred_env_status
            ON deferred_executions (env_name, status);
        CREATE INDEX IF NOT EXISTS idx_deferred_window
            ON deferred_executions (window_start)
        """,
    ),
]


async def run_migrations(conn: AsyncConnection) -> None:
    """Apply any pending database schema migrations.

    Idempotent — safe to call on every startup.  Each migration is applied
    within the caller's transaction (conn is already inside engine.begin()).

    Args:
        conn: Open SQLAlchemy AsyncConnection (inside an active transaction).
    """
    # Bootstrap: schema_migrations must exist before we can read from it.
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """))

    result = await conn.execute(
        text("SELECT version FROM schema_migrations ORDER BY version")
    )
    applied: set[int] = {int(row[0]) for row in result.fetchall()}

    for version, sql in _MIGRATIONS:
        if version in applied:
            continue

        logger.info("Applying database migration %04d", version)
        for raw_stmt in sql.split(";"):
            stmt = raw_stmt.strip()
            if stmt:
                await conn.execute(text(stmt))

        await conn.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at)"
                " VALUES (:version, :applied_at)"
            ),
            {"version": version, "applied_at": datetime.now(tz=UTC).isoformat()},
        )
        logger.info("Migration %04d applied", version)
