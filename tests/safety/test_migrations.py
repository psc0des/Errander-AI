"""Tests for the database schema migration framework."""

from __future__ import annotations

import time

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from errander.db.core import AsyncDatabase
from errander.safety.migrations import run_migrations

# TEST_DB_URL captured at import time so clean_errander_env does not affect it.
from tests.conftest import TEST_DB_URL


async def _make_db() -> AsyncDatabase:
    db = AsyncDatabase(TEST_DB_URL)
    async with db.begin() as conn:
        await run_migrations(conn)
    return db


async def _get_tables(db: AsyncDatabase) -> set[str]:
    """Return the set of table names — works on both SQLite and PostgreSQL."""
    async with db.begin() as conn:
        tables: set[str] = set(
            await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
        )
    return tables


class TestRunMigrations:
    """Tests for run_migrations() idempotency and completeness."""

    async def test_creates_schema_migrations_table(self) -> None:
        db = await _make_db()
        tables = await _get_tables(db)
        assert "schema_migrations" in tables
        await db.close()

    async def test_creates_all_expected_tables(self) -> None:
        db = await _make_db()
        tables = await _get_tables(db)
        expected = {
            "schema_migrations",
            "audit_events",
            "vm_state",
            "vm_baselines",
            "vm_disk_history",
            "vm_metrics",
            "batches",
            "artifacts",
            "agent_lease",
            "plan_snapshots",
            "ai_eval_runs",
            "ai_eval_results",
            "settings_overrides",
            "inventory_overrides",
            "ai_decisions",
            "deferred_executions",
        }
        assert expected <= tables
        await db.close()

    async def test_records_applied_versions(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
            )
            versions = [int(str(row[0])) for row in result.fetchall()]
        assert versions == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        await db.close()

    async def test_idempotent_on_second_run(self) -> None:
        db = await _make_db()
        # Second call must not raise and must not duplicate version records
        async with db.begin() as conn:
            await run_migrations(conn)
        async with db.begin() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM schema_migrations"))
            row = result.fetchone()
        assert int(str(row[0])) == 13  # exactly 13 migrations (0–12)
        await db.close()

    async def test_audit_events_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_events "
                    "(event_type, batch_id, detail, timestamp, metadata) "
                    "VALUES (:et, :bid, :detail, :ts, :meta)"
                ),
                {
                    "et": "action_started", "bid": "batch-1",
                    "detail": "test", "ts": "2025-01-01T00:00:00+00:00", "meta": "{}",
                },
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT event_type, batch_id FROM audit_events")
            )
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "action_started"
        await db.close()

    async def test_vm_state_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text("INSERT INTO vm_state (vm_id, needs_reboot, updated_at) VALUES (:vm_id, :nr, :ts)"),
                {"vm_id": "dev/web-01", "nr": 1, "ts": "2025-01-01T00:00:00+00:00"},
            )
        async with db.begin() as conn:
            result = await conn.execute(text("SELECT vm_id, needs_reboot FROM vm_state"))
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "dev/web-01"
        assert int(str(row[1])) == 1
        await db.close()

    async def test_vm_baselines_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO vm_baselines "
                    "(vm_id, baseline_kind, scope_key, captured_at, content_hash, content_blob) "
                    "VALUES (:vm_id, :kind, :scope, :ts, :hash, :blob)"
                ),
                {
                    "vm_id": "dev/web-01", "kind": "sudoers", "scope": "",
                    "ts": "2025-01-01T00:00:00+00:00", "hash": "abc123",
                    "blob": "root ALL=ALL",
                },
            )
        async with db.begin() as conn:
            result = await conn.execute(text("SELECT baseline_kind FROM vm_baselines"))
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "sudoers"
        await db.close()

    async def test_vm_disk_history_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO vm_disk_history "
                    "(vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
                    "VALUES (:vm_id, :ts, :mp, :used, :total)"
                ),
                {
                    "vm_id": "dev/web-01", "ts": "2025-01-01T00:00:00+00:00",
                    "mp": "/", "used": 5_000_000_000, "total": 50_000_000_000,
                },
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT mountpoint, used_bytes FROM vm_disk_history")
            )
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "/"
        assert int(str(row[1])) == 5_000_000_000
        await db.close()

    async def test_vm_metrics_schema_correct(self) -> None:
        db = await _make_db()
        now = int(time.time())
        async with db.begin() as conn:
            await conn.execute(
                text("INSERT INTO vm_metrics (hostname, metric, value_pct, ts) VALUES (:h, :m, :v, :ts)"),
                {"h": "prod-api-01", "m": "cpu", "v": 42.5, "ts": now},
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT hostname, metric, value_pct FROM vm_metrics")
            )
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "prod-api-01"
        assert str(row[1]) == "cpu"
        assert abs(float(str(row[2])) - 42.5) < 0.01
        await db.close()

    async def test_batches_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO batches (id, env_name, status, started_at, dry_run, vm_count) "
                    "VALUES (:id, :env, :status, :ts, :dry, :vms)"
                ),
                {
                    "id": "batch-test-01", "env": "PROD", "status": "running",
                    "ts": "2025-01-01T00:00:00+00:00", "dry": 1, "vms": 5,
                },
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT id, env_name, status, dry_run, vm_count, finished_at, error FROM batches")
            )
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == "batch-test-01"
        assert str(row[1]) == "PROD"
        assert str(row[2]) == "running"
        assert int(str(row[3])) == 1
        assert int(str(row[4])) == 5
        assert row[5] is None
        assert row[6] is None
        await db.close()

    async def test_artifacts_schema_correct(self) -> None:
        import uuid
        db = await _make_db()
        artifact_id = str(uuid.uuid4())
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO artifacts (id, batch_id, vm_id, artifact_kind, content, created_at) "
                    "VALUES (:id, :bid, :vm, :kind, :content, :ts)"
                ),
                {
                    "id": artifact_id, "bid": "batch-001", "vm": "prod/web-01",
                    "kind": "patch_output", "content": "apt-get output",
                    "ts": "2025-01-01T00:00:00+00:00",
                },
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT id, batch_id, vm_id, artifact_kind, content FROM artifacts")
            )
            row = result.fetchone()
        assert row is not None
        assert str(row[0]) == artifact_id
        assert str(row[1]) == "batch-001"
        assert str(row[2]) == "prod/web-01"
        assert str(row[3]) == "patch_output"
        assert str(row[4]) == "apt-get output"
        await db.close()

    async def test_agent_lease_schema_correct(self) -> None:
        db = await _make_db()
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agent_lease (id, pid, hostname, acquired_at, last_heartbeat) "
                    "VALUES (1, 42, 'myhost', :ts1, :ts2)"
                ),
                {"ts1": "2025-01-01T00:00:00+00:00", "ts2": "2025-01-01T00:00:01+00:00"},
            )
        async with db.begin() as conn:
            result = await conn.execute(
                text("SELECT id, pid, hostname FROM agent_lease WHERE id = 1")
            )
            row = result.fetchone()
        assert row is not None
        assert int(str(row[0])) == 1
        assert int(str(row[1])) == 42
        assert str(row[2]) == "myhost"
        await db.close()


class TestAuditStoreUsesMigrations:
    """Verify AuditStore.initialize() delegates to run_migrations."""

    async def test_audit_store_creates_vm_state_table(self) -> None:
        """AuditStore.initialize() must create ALL tables, not just audit_events."""
        from errander.safety.audit import AuditStore
        db = AsyncDatabase(TEST_DB_URL)
        async with AuditStore(db) as store:
            tables = await _get_tables(store._db)
        assert "vm_state" in tables, "vm_state table missing after AuditStore.initialize()"
        await db.close()
