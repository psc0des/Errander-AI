"""Tests for DeferredExecutionStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from errander.db.core import AsyncDatabase
from errander.safety.deferred import DeferredExecutionStore
from errander.safety.migrations import run_migrations


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# Always 30 days in the future so expiry_at (window_start + 7d) never passes
WINDOW_START = datetime.now(tz=UTC).replace(
    hour=23, minute=0, second=0, microsecond=0
) + timedelta(days=30)


async def _make_store() -> DeferredExecutionStore:
    db = AsyncDatabase(":memory:")
    async with db.begin() as conn:
        await run_migrations(conn, "sqlite")
    return DeferredExecutionStore(db)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestDeferredStoreLifecycle:
    async def test_initialize_and_close(self) -> None:
        store = await _make_store()
        assert store._db is not None
        await store.close()

    async def test_double_close_is_safe(self) -> None:
        store = await _make_store()
        await store.close()
        await store.close()  # should not raise


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestDeferredStoreSave:
    async def test_save_writes_record(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "production", "alice", WINDOW_START)
            pending = await store.get_pending("production")
            assert len(pending) == 1
            rec = pending[0]
            assert rec.batch_id == "b-001"
            assert rec.env_name == "production"
            assert rec.approved_by == "alice"
            assert rec.window_start == WINDOW_START
            assert rec.status == "pending"
            assert rec.executed_at is None
        finally:
            await store.close()

    async def test_save_sets_expiry_7_days_after_window_start(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            pending = await store.get_pending("dev")
            assert pending[0].expiry_at == WINDOW_START + timedelta(days=7)
        finally:
            await store.close()

    async def test_save_upsert_replaces_existing(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", "alice", WINDOW_START)
            new_window = WINDOW_START + timedelta(days=7)
            await store.save("b-001", "dev", "bob", new_window)
            pending = await store.get_pending("dev")
            assert len(pending) == 1
            assert pending[0].approved_by == "bob"
            assert pending[0].window_start == new_window
        finally:
            await store.close()

    async def test_save_null_approved_by(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            pending = await store.get_pending("dev")
            assert pending[0].approved_by is None
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# get_pending
# ---------------------------------------------------------------------------

class TestDeferredStoreGetPending:
    async def test_get_pending_returns_only_target_env(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "production", None, WINDOW_START)
            await store.save("b-002", "staging", None, WINDOW_START)
            prod = await store.get_pending("production")
            stg = await store.get_pending("staging")
            assert len(prod) == 1
            assert prod[0].batch_id == "b-001"
            assert len(stg) == 1
            assert stg[0].batch_id == "b-002"
        finally:
            await store.close()

    async def test_get_pending_excludes_non_pending_status(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            await store.mark_executing("b-001")
            assert await store.get_pending("dev") == []
        finally:
            await store.close()

    async def test_get_pending_excludes_expired(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            await store.expire_old()
            # expire_old only affects records whose expiry_at has passed;
            # WINDOW_START is in the future so this record won't be expired yet —
            # test instead by directly saving with a past window_start.
            past_window = _utc(2020, 1, 1, 0, 0, 0)
            await store.save("b-002", "dev", None, past_window)
            await store.expire_old()
            pending = await store.get_pending("dev")
            batch_ids = {r.batch_id for r in pending}
            assert "b-002" not in batch_ids
        finally:
            await store.close()

    async def test_get_pending_returns_empty_list_when_none(self) -> None:
        store = await _make_store()
        try:
            assert await store.get_pending("dev") == []
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# mark_executing / mark_done
# ---------------------------------------------------------------------------

class TestDeferredStoreTransitions:
    async def test_mark_executing(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            await store.mark_executing("b-001")
            # get_pending only returns 'pending' records
            assert await store.get_pending("dev") == []
        finally:
            await store.close()

    async def test_mark_done_stamps_executed_at(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            await store.mark_executing("b-001")
            await store.mark_done("b-001")
            async with store._db.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT status, executed_at FROM deferred_executions"
                        " WHERE batch_id = :bid"
                    ),
                    {"bid": "b-001"},
                )
                row = result.mappings().fetchone()
            assert row is not None
            assert row["status"] == "done"
            assert row["executed_at"] is not None
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# expire_old
# ---------------------------------------------------------------------------

class TestDeferredStoreExpireOld:
    async def test_expire_old_returns_count(self) -> None:
        store = await _make_store()
        try:
            past = _utc(2020, 1, 1, 0, 0, 0)
            await store.save("b-001", "dev", None, past)
            await store.save("b-002", "dev", None, past)
            count = await store.expire_old()
            assert count == 2
        finally:
            await store.close()

    async def test_expire_old_ignores_non_expired(self) -> None:
        store = await _make_store()
        try:
            await store.save("b-001", "dev", None, WINDOW_START)
            count = await store.expire_old()
            assert count == 0
            assert len(await store.get_pending("dev")) == 1
        finally:
            await store.close()

    async def test_expire_old_skips_non_pending(self) -> None:
        store = await _make_store()
        try:
            past = _utc(2020, 1, 1, 0, 0, 0)
            await store.save("b-001", "dev", None, past)
            await store.mark_executing("b-001")
            count = await store.expire_old()
            # Only 'pending' records are expired — executing records are not touched
            assert count == 0
        finally:
            await store.close()
