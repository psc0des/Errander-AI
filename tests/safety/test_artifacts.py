"""Tests for ArtifactStore — oversized blob storage (Project A, A4)."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from errander.safety.artifacts import ArtifactStore
from errander.safety.migrations import run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        await run_migrations(conn)
        yield conn


@pytest_asyncio.fixture
async def store(db):
    yield ArtifactStore(db)


# ---------------------------------------------------------------------------
# store tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_returns_artifact_id(store):
    artifact_id = await store.store(
        batch_id="batch-001",
        vm_id="prod/web-01",
        artifact_kind="patch_output",
        content="0 upgraded, 0 newly installed.",
    )
    assert isinstance(artifact_id, str)
    assert len(artifact_id) == 36  # UUID4 format


@pytest.mark.asyncio
async def test_store_unique_ids_per_call(store):
    id1 = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="first",
    )
    id2 = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="second",
    )
    assert id1 != id2


@pytest.mark.asyncio
async def test_store_large_blob(store):
    large = "line\n" * 5000  # ~25KB
    artifact_id = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content=large,
    )
    result = await store.retrieve(artifact_id)
    assert result == large


# ---------------------------------------------------------------------------
# retrieve tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_returns_stored_content(store):
    content = "apt-get output: 3 upgraded, 1 newly installed."
    artifact_id = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content=content,
    )
    result = await store.retrieve(artifact_id)
    assert result == content


@pytest.mark.asyncio
async def test_retrieve_missing_returns_none(store):
    result = await store.retrieve("00000000-0000-0000-0000-000000000000")
    assert result is None


@pytest.mark.asyncio
async def test_retrieve_different_artifact_kinds(store):
    patch_id = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="patching done",
    )
    prune_id = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="prune_output", content="pruning done",
    )
    assert await store.retrieve(patch_id) == "patching done"
    assert await store.retrieve(prune_id) == "pruning done"


# ---------------------------------------------------------------------------
# retrieve_by_kind tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_by_kind_empty(store):
    result = await store.retrieve_by_kind("batch-001", "prod/web-01", "patch_output")
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_by_kind_returns_matching_blobs(store):
    await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="run 1",
    )
    await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="run 2",
    )
    result = await store.retrieve_by_kind("batch-001", "prod/web-01", "patch_output")
    assert len(result) == 2
    assert "run 1" in result
    assert "run 2" in result


@pytest.mark.asyncio
async def test_retrieve_by_kind_filters_by_vm(store):
    await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="web-01 output",
    )
    await store.store(
        batch_id="batch-001", vm_id="prod/db-01",
        artifact_kind="patch_output", content="db-01 output",
    )
    result = await store.retrieve_by_kind("batch-001", "prod/web-01", "patch_output")
    assert result == ["web-01 output"]


@pytest.mark.asyncio
async def test_retrieve_by_kind_filters_by_batch(store):
    await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="batch-001 output",
    )
    await store.store(
        batch_id="batch-002", vm_id="prod/web-01",
        artifact_kind="patch_output", content="batch-002 output",
    )
    result = await store.retrieve_by_kind("batch-001", "prod/web-01", "patch_output")
    assert result == ["batch-001 output"]


# ---------------------------------------------------------------------------
# purge_before tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_before_deletes_old_artifacts(store, db):
    # Insert an artifact with an old timestamp directly
    await db.execute(
        "INSERT INTO artifacts (id, batch_id, vm_id, artifact_kind, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("old-id", "batch-old", "prod/web-01", "patch_output", "old content",
         "2020-01-01T00:00:00+00:00"),
    )
    await db.commit()

    deleted = await store.purge_before("2025-01-01T00:00:00+00:00")
    assert deleted == 1
    result = await store.retrieve("old-id")
    assert result is None


@pytest.mark.asyncio
async def test_purge_before_preserves_recent_artifacts(store):
    artifact_id = await store.store(
        batch_id="batch-001", vm_id="prod/web-01",
        artifact_kind="patch_output", content="recent",
    )
    deleted = await store.purge_before("2020-01-01T00:00:00+00:00")
    assert deleted == 0
    result = await store.retrieve(artifact_id)
    assert result == "recent"


@pytest.mark.asyncio
async def test_purge_before_returns_count(store, db):
    for i in range(5):
        await db.execute(
            "INSERT INTO artifacts (id, batch_id, vm_id, artifact_kind, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"old-{i}", "batch-old", "prod/web-01", "patch_output", f"content-{i}",
             "2020-01-01T00:00:00+00:00"),
        )
    await db.commit()
    deleted = await store.purge_before("2025-01-01T00:00:00+00:00")
    assert deleted == 5
