"""Shared pytest fixtures for Errander-AI tests.

Provides common test fixtures:
- Sample VMTarget and VMInfo objects
- Mock SSH connections
- In-memory audit database
- Fake Slack client
- Fake LLM client (returns deterministic responses)
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from errander.models.vm import OSFamily, VMTarget

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

# Read at import time so clean_errander_env's monkeypatch loop does not affect it.
TEST_DB_URL: str = os.getenv("ERRANDER_TEST_DB_URL", ":memory:")


@pytest.fixture(autouse=True)
def clean_errander_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all ERRANDER_* env vars before each test.

    Prevents real .env values (e.g. ERRANDER_LLM_MODEL, ERRANDER_UI_PASSWORD)
    exported to the shell from leaking into tests that expect a clean slate.
    Tests that need specific values set them explicitly via monkeypatch.setenv.
    """
    for key in list(os.environ.keys()):
        if key.startswith("ERRANDER_"):
            monkeypatch.delenv(key, raising=False)


@pytest_asyncio.fixture(scope="session")
async def session_db() -> AsyncGenerator[AsyncDatabase, None]:
    """Session-scoped AsyncDatabase using TEST_DB_URL.

    When ERRANDER_TEST_DB_URL is set to a Postgres URL (e.g. in CI), all tests
    that opt in via the async_db fixture run against Postgres. Otherwise falls
    back to a shared in-memory SQLite DB.
    """
    from errander.db.core import AsyncDatabase
    from errander.safety.migrations import run_migrations

    db = AsyncDatabase(TEST_DB_URL)
    async with db.begin() as conn:
        await run_migrations(conn, db.dialect)
    yield db
    await db.close()


@pytest_asyncio.fixture
async def async_db(session_db: AsyncDatabase) -> AsyncGenerator[AsyncDatabase, None]:
    """Per-test AsyncDatabase.

    For in-memory SQLite: yields a fresh isolated DB (no cross-test state).
    For Postgres or file SQLite: yields the shared session_db (tests must use
    unique IDs to avoid conflicts).
    """
    from errander.db.core import AsyncDatabase
    from errander.safety.migrations import run_migrations

    if ":memory:" in TEST_DB_URL:
        db = AsyncDatabase(":memory:")
        async with db.begin() as conn:
            await run_migrations(conn, "sqlite")
        yield db
        await db.close()
    else:
        yield session_db


@pytest.fixture
def sample_vm_target() -> VMTarget:
    """A sample Ubuntu VM target for testing."""
    return VMTarget(
        vm_id="test-vm-1",
        hostname="10.0.1.10",
        ssh_user="errander-ai",
        ssh_key_path="/tmp/test_key",
        os_family=OSFamily.UBUNTU,
        policy="moderate",
        tags={"env": "test"},
    )
