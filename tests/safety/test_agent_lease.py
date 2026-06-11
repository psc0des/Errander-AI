"""Tests for AgentLease — single-process enforcement (Project A, A5)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from errander.safety.agent_lease import AgentLease, AgentLeaseError
from errander.safety.migrations import run_migrations
from tests.conftest import make_test_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    db = make_test_db()
    async with db.begin() as conn:
        await run_migrations(conn)
    yield db
    await db.close()


@pytest_asyncio.fixture
async def lease(db):
    yield AgentLease(db, pid=12345, hostname="test-host")


# ---------------------------------------------------------------------------
# acquire tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acquire_on_empty_table_succeeds(lease):
    await lease.acquire()
    holder = await lease.current_holder()
    assert holder is not None
    assert holder["pid"] == 12345
    assert holder["hostname"] == "test-host"


@pytest.mark.asyncio
async def test_acquire_twice_same_pid_succeeds(db):
    lease1 = AgentLease(db, pid=12345, hostname="test-host")
    await lease1.acquire()
    await lease1.release()
    lease2 = AgentLease(db, pid=12345, hostname="test-host")
    await lease2.acquire()
    holder = await lease2.current_holder()
    assert holder is not None


@pytest.mark.asyncio
async def test_acquire_with_live_lease_raises(db):
    lease1 = AgentLease(db, pid=11111, hostname="host-A")
    await lease1.acquire()

    lease2 = AgentLease(db, pid=22222, hostname="host-B")
    with pytest.raises(AgentLeaseError, match="11111"):
        await lease2.acquire()


@pytest.mark.asyncio
async def test_acquire_evicts_expired_lease(db):
    # Insert a stale lease manually
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_lease (id, pid, hostname, acquired_at, last_heartbeat)"
                " VALUES (1, 99999, 'dead-host', :acq, :hb)"
            ),
            {"acq": "2020-01-01T00:00:00+00:00", "hb": "2020-01-01T00:00:00+00:00"},
        )

    lease = AgentLease(db, pid=12345, hostname="new-host", ttl_seconds=90)
    await lease.acquire()  # must succeed — old lease is expired
    holder = await lease.current_holder()
    assert holder is not None
    assert holder["pid"] == 12345


# ---------------------------------------------------------------------------
# heartbeat tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_updates_timestamp(db):
    import asyncio
    lease = AgentLease(db, pid=12345, hostname="test-host")
    await lease.acquire()
    holder_before = await lease.current_holder()
    await asyncio.sleep(0.05)  # ensure time advances slightly
    await lease.heartbeat()
    holder_after = await lease.current_holder()
    assert holder_after is not None
    assert holder_before is not None
    # Heartbeat should update last_heartbeat
    assert holder_after["last_heartbeat"] >= holder_before["last_heartbeat"]


@pytest.mark.asyncio
async def test_heartbeat_noop_if_not_owner(db):
    lease1 = AgentLease(db, pid=11111, hostname="host-A")
    await lease1.acquire()

    lease2 = AgentLease(db, pid=22222, hostname="host-B")
    # lease2 doesn't own the lease — heartbeat should silently do nothing
    await lease2.heartbeat()
    holder = await lease1.current_holder()
    assert holder is not None
    assert holder["pid"] == 11111  # lease1 still holds it


# ---------------------------------------------------------------------------
# release tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_clears_lease(lease):
    await lease.acquire()
    await lease.release()
    holder = await lease.current_holder()
    assert holder is None


@pytest.mark.asyncio
async def test_release_noop_if_not_owner(db):
    lease1 = AgentLease(db, pid=11111, hostname="host-A")
    await lease1.acquire()

    lease2 = AgentLease(db, pid=22222, hostname="host-B")
    await lease2.release()  # should not delete lease1's row
    holder = await lease1.current_holder()
    assert holder is not None
    assert holder["pid"] == 11111


@pytest.mark.asyncio
async def test_release_idempotent(lease):
    await lease.acquire()
    await lease.release()
    await lease.release()  # second call must not raise
    holder = await lease.current_holder()
    assert holder is None


# ---------------------------------------------------------------------------
# current_holder / is_expired tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_holder_none_when_no_lease(lease):
    holder = await lease.current_holder()
    assert holder is None


@pytest.mark.asyncio
async def test_current_holder_returns_all_fields(lease):
    await lease.acquire()
    holder = await lease.current_holder()
    assert holder is not None
    assert "pid" in holder
    assert "hostname" in holder
    assert "acquired_at" in holder
    assert "last_heartbeat" in holder


@pytest.mark.asyncio
async def test_is_expired_true_when_no_lease(lease):
    result = await lease.is_expired()
    assert result is True


@pytest.mark.asyncio
async def test_is_expired_false_for_live_lease(lease):
    await lease.acquire()
    result = await lease.is_expired()
    assert result is False


@pytest.mark.asyncio
async def test_is_expired_true_for_stale_lease(db):
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_lease (id, pid, hostname, acquired_at, last_heartbeat)"
                " VALUES (1, 99999, 'dead-host', :acq, :hb)"
            ),
            {"acq": "2020-01-01T00:00:00+00:00", "hb": "2020-01-01T00:00:00+00:00"},
        )

    lease = AgentLease(db, pid=12345, hostname="test-host", ttl_seconds=90)
    result = await lease.is_expired()
    assert result is True
