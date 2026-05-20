"""Tests for the database schema migration framework."""

from __future__ import annotations

import aiosqlite

from errander.safety.migrations import run_migrations


async def _open_memory_db() -> aiosqlite.Connection:
    return await aiosqlite.connect(":memory:")


class TestRunMigrations:
    """Tests for run_migrations() idempotency and completeness."""

    async def test_creates_schema_migrations_table(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        row = await cursor.fetchone()
        assert row is not None
        await db.close()

    async def test_creates_all_expected_tables(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {str(row[0]) for row in await cursor.fetchall()}
        expected = {
            "schema_migrations",
            "audit_events",
            "vm_state",
            "vm_baselines",
            "vm_disk_history",
            "vm_metrics",
        }
        assert expected <= tables
        await db.close()

    async def test_records_applied_versions(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        cursor = await db.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [int(str(row[0])) for row in await cursor.fetchall()]
        assert versions == [0, 1, 2, 3, 4]
        await db.close()

    async def test_idempotent_on_second_run(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        # Second call must not raise and must not duplicate version records
        await run_migrations(db)
        cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
        row = await cursor.fetchone()
        assert int(str(row[0])) == 5  # exactly 5 migrations (0–4)
        await db.close()

    async def test_audit_events_schema_correct(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        # Insert and read back a row to confirm the schema is correct
        await db.execute(
            "INSERT INTO audit_events "
            "(event_type, batch_id, detail, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            ("action_started", "batch-1", "test", "2025-01-01T00:00:00+00:00", "{}"),
        )
        await db.commit()
        cursor = await db.execute("SELECT event_type, batch_id FROM audit_events")
        row = await cursor.fetchone()
        assert row is not None
        assert str(row[0]) == "action_started"
        await db.close()

    async def test_vm_state_schema_correct(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        await db.execute(
            "INSERT INTO vm_state (vm_id, needs_reboot, updated_at) VALUES (?, ?, ?)",
            ("dev/web-01", 1, "2025-01-01T00:00:00+00:00"),
        )
        await db.commit()
        cursor = await db.execute("SELECT vm_id, needs_reboot FROM vm_state")
        row = await cursor.fetchone()
        assert row is not None
        assert str(row[0]) == "dev/web-01"
        assert int(str(row[1])) == 1
        await db.close()

    async def test_vm_baselines_schema_correct(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        await db.execute(
            "INSERT INTO vm_baselines "
            "(vm_id, baseline_kind, scope_key, captured_at, content_hash, content_blob) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dev/web-01", "sudoers", "", "2025-01-01T00:00:00+00:00", "abc123", "root ALL=ALL"),
        )
        await db.commit()
        cursor = await db.execute("SELECT baseline_kind FROM vm_baselines")
        row = await cursor.fetchone()
        assert row is not None
        assert str(row[0]) == "sudoers"
        await db.close()

    async def test_vm_disk_history_schema_correct(self) -> None:
        db = await _open_memory_db()
        await run_migrations(db)
        await db.execute(
            "INSERT INTO vm_disk_history (vm_id, captured_at, mountpoint, used_bytes, total_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            ("dev/web-01", "2025-01-01T00:00:00+00:00", "/", 5_000_000_000, 50_000_000_000),
        )
        await db.commit()
        cursor = await db.execute("SELECT mountpoint, used_bytes FROM vm_disk_history")
        row = await cursor.fetchone()
        assert row is not None
        assert str(row[0]) == "/"
        assert int(str(row[1])) == 5_000_000_000
        await db.close()

    async def test_vm_metrics_schema_correct(self) -> None:
        import time
        db = await _open_memory_db()
        await run_migrations(db)
        now = int(time.time())
        await db.execute(
            "INSERT INTO vm_metrics (hostname, metric, value_pct, ts) VALUES (?, ?, ?, ?)",
            ("prod-api-01", "cpu", 42.5, now),
        )
        await db.commit()
        cursor = await db.execute("SELECT hostname, metric, value_pct FROM vm_metrics")
        row = await cursor.fetchone()
        assert row is not None
        assert str(row[0]) == "prod-api-01"
        assert str(row[1]) == "cpu"
        assert abs(float(str(row[2])) - 42.5) < 0.01
        await db.close()


class TestAuditStoreUsesMigrations:
    """Verify AuditStore.initialize() delegates to run_migrations."""

    async def test_audit_store_creates_vm_state_table(self) -> None:
        """AuditStore.initialize() must create ALL tables, not just audit_events."""
        from errander.safety.audit import AuditStore
        async with AuditStore(":memory:") as store:
            db = store._db
            assert db is not None
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vm_state'"
            )
            row = await cursor.fetchone()
            assert row is not None, "vm_state table missing after AuditStore.initialize()"
