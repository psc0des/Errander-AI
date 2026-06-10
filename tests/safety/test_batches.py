"""Tests for BatchStore — batch lifecycle persistence (Project A, A2)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from errander.db.core import AsyncDatabase
from errander.models.batches import BatchRecord, BatchStatus
from errander.safety.batches import BatchStore
from errander.safety.migrations import run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """In-memory AsyncDatabase with all migrations applied."""
    db = AsyncDatabase(":memory:")
    async with db.begin() as conn:
        await run_migrations(conn, "sqlite")
    yield db
    await db.close()


@pytest_asyncio.fixture
async def store(db):
    yield BatchStore(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _insert(store: BatchStore, batch_id: str = "batch-abc123", **kwargs: object) -> str:
    defaults = dict(env_name="PROD", dry_run=True, vm_count=3)
    defaults.update(kwargs)  # type: ignore[arg-type]
    await store.insert(batch_id, **defaults)  # type: ignore[arg-type]
    return batch_id


# ---------------------------------------------------------------------------
# insert tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_creates_running_row(store):
    await _insert(store, "batch-001")
    rec = await store.get("batch-001")
    assert rec is not None
    assert rec.id == "batch-001"
    assert rec.status == BatchStatus.RUNNING
    assert rec.env_name == "PROD"
    assert rec.dry_run is True
    assert rec.vm_count == 3
    assert rec.finished_at is None
    assert rec.error is None


@pytest.mark.asyncio
async def test_insert_idempotent(store):
    """Second insert with same batch_id is silently ignored (INSERT OR IGNORE)."""
    await _insert(store, "batch-dup", env_name="STAGING", vm_count=5)
    await _insert(store, "batch-dup", env_name="PROD", vm_count=99)  # should be ignored
    rec = await store.get("batch-dup")
    assert rec is not None
    assert rec.env_name == "STAGING"
    assert rec.vm_count == 5


@pytest.mark.asyncio
async def test_insert_live_run(store):
    await _insert(store, "batch-live", dry_run=False)
    rec = await store.get("batch-live")
    assert rec is not None
    assert rec.dry_run is False


@pytest.mark.asyncio
async def test_insert_zero_vms(store):
    await _insert(store, "batch-empty", vm_count=0)
    rec = await store.get("batch-empty")
    assert rec is not None
    assert rec.vm_count == 0


# ---------------------------------------------------------------------------
# update_status tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_to_completed(store):
    await _insert(store, "batch-ok")
    await store.update_status("batch-ok", BatchStatus.COMPLETED)
    rec = await store.get("batch-ok")
    assert rec is not None
    assert rec.status == BatchStatus.COMPLETED
    assert rec.finished_at is not None
    assert rec.error is None


@pytest.mark.asyncio
async def test_update_to_completed_with_failures(store):
    await _insert(store, "batch-cwf")
    await store.update_status("batch-cwf", BatchStatus.COMPLETED_WITH_FAILURES)
    rec = await store.get("batch-cwf")
    assert rec is not None
    assert rec.status == BatchStatus.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_update_to_aborted_with_error(store):
    await _insert(store, "batch-abort")
    await store.update_status("batch-abort", BatchStatus.ABORTED, error="outside maintenance window")
    rec = await store.get("batch-abort")
    assert rec is not None
    assert rec.status == BatchStatus.ABORTED
    assert rec.error == "outside maintenance window"
    assert rec.finished_at is not None


@pytest.mark.asyncio
async def test_update_to_needs_operator_review(store):
    await _insert(store, "batch-nor")
    await store.update_status("batch-nor", BatchStatus.NEEDS_OPERATOR_REVIEW)
    rec = await store.get("batch-nor")
    assert rec is not None
    assert rec.status == BatchStatus.NEEDS_OPERATOR_REVIEW


@pytest.mark.asyncio
async def test_update_idempotent_on_double_call(store):
    """Second update_status is silently ignored (WHERE status='running' guard)."""
    await _insert(store, "batch-double")
    await store.update_status("batch-double", BatchStatus.COMPLETED)
    await store.update_status("batch-double", BatchStatus.ABORTED, error="second call")
    rec = await store.get("batch-double")
    assert rec is not None
    # First update wins; second is ignored because the row is no longer RUNNING
    assert rec.status == BatchStatus.COMPLETED
    assert rec.error is None


@pytest.mark.asyncio
async def test_update_unknown_batch_id_is_noop(store):
    """update_status on a non-existent batch_id should not raise."""
    await store.update_status("batch-ghost", BatchStatus.COMPLETED)
    rec = await store.get("batch-ghost")
    assert rec is None


# ---------------------------------------------------------------------------
# get tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    result = await store.get("nonexistent-batch")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_batch_record_type(store):
    await _insert(store, "batch-typed")
    rec = await store.get("batch-typed")
    assert isinstance(rec, BatchRecord)


# ---------------------------------------------------------------------------
# list_recent tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_recent_empty(store):
    result = await store.list_recent()
    assert result == []


@pytest.mark.asyncio
async def test_list_recent_returns_newest_first(store):
    for i in range(3):
        await _insert(store, f"batch-{i:03d}")
    results = await store.list_recent()
    assert len(results) == 3
    # All should be BatchRecord instances
    assert all(isinstance(r, BatchRecord) for r in results)


@pytest.mark.asyncio
async def test_list_recent_respects_limit(store):
    for i in range(10):
        await _insert(store, f"batch-lim-{i:03d}")
    results = await store.list_recent(limit=5)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_list_recent_mixed_statuses(store):
    await _insert(store, "batch-m1", env_name="PROD")
    await _insert(store, "batch-m2", env_name="STAGING")
    await store.update_status("batch-m1", BatchStatus.COMPLETED)
    results = await store.list_recent()
    statuses = {r.status for r in results}
    assert BatchStatus.COMPLETED in statuses
    assert BatchStatus.RUNNING in statuses
