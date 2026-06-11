"""Shared pytest fixtures for Errander-AI tests.

Provides common test fixtures:
- Sample VMTarget and VMInfo objects
- Mock SSH connections
- In-memory audit database
- Fake Slack client
- Fake LLM client (returns deterministic responses)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from errander.db.core import AsyncDatabase
from errander.models.vm import OSFamily, VMTarget

# Read at import time so clean_errander_env's monkeypatch loop does not affect it.
# Default matches the errander_test database created by docker-compose.yml.
TEST_DB_URL: str = os.getenv(
    "ERRANDER_TEST_DB_URL",
    "postgresql+asyncpg://errander:errander@localhost:5432/errander_test",
)

# Migration/version bookkeeping tables that per-test cleanup must never touch.
_PRESERVED_TABLES = frozenset({"schema_migrations", "checkpoint_migrations"})


def make_test_db() -> AsyncDatabase:
    """Return an AsyncDatabase bound to the shared test database.

    Migrations are applied once per session (see _migrate_test_db); all tables
    are truncated before every test (see _clean_test_db), so each test sees
    the same empty-database state a fresh in-memory DB used to provide.
    """
    return AsyncDatabase(TEST_DB_URL)


async def _run_session_migrations() -> None:
    from errander.safety.migrations import run_migrations

    db = AsyncDatabase(TEST_DB_URL)
    try:
        async with db.begin() as conn:
            await run_migrations(conn)
    finally:
        await db.close()


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_db() -> None:
    """Apply schema migrations to the test database once per session.

    Sync fixture running its own event loop so it works regardless of
    pytest-asyncio loop scoping.
    """
    try:
        asyncio.run(_run_session_migrations())
    except Exception as exc:  # noqa: BLE001 — translate to a clear operator message
        pytest.exit(
            f"Cannot reach the test PostgreSQL at {TEST_DB_URL!r}: {exc}\n"
            "Start it with `docker compose up -d` (see SETUP.md), or set "
            "ERRANDER_TEST_DB_URL to a reachable PostgreSQL.",
            returncode=1,
        )


@pytest_asyncio.fixture(autouse=True)
async def _clean_test_db(_migrate_test_db: None) -> AsyncIterator[None]:
    """Truncate all tables before each test — same isolation a fresh DB gave."""
    from sqlalchemy import text

    db = AsyncDatabase(TEST_DB_URL)
    try:
        async with db.begin() as conn:
            result = await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ))
            tables = [str(r[0]) for r in result.fetchall() if str(r[0]) not in _PRESERVED_TABLES]
            if tables:
                quoted = ", ".join(f'"{t}"' for t in tables)
                await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    finally:
        await db.close()
    yield


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
