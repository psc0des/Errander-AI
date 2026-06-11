"""Async database wrapper — PostgreSQL only (asyncpg via SQLAlchemy Core).

URL normalization:
  "postgresql://..."          -> "postgresql+asyncpg://..."
  "postgres://..."            -> "postgresql+asyncpg://..."  (Heroku-style)
  "postgresql+asyncpg://..."  -> kept as-is

Anything else (SQLite paths, ":memory:", other dialects) is rejected with a
ValueError — Errander-AI is PostgreSQL-only (owner decision 2026-06-10:
one standard, less headache for users). See SETUP.md for the Docker Compose
quick start that provides a local PostgreSQL with zero configuration.

The begin() context manager yields a SQLAlchemy AsyncConnection in an open
transaction: auto-commits on clean exit, rolls back on exception.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _normalize_url(url: str) -> str:
    """Convert a PostgreSQL URL to a fully-qualified SQLAlchemy asyncpg URL.

    Raises ValueError for anything that is not PostgreSQL.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(
        f"Unsupported database URL {url!r} — Errander-AI requires PostgreSQL. "
        "Set ERRANDER_AUDIT_DB_URL to a postgresql:// URL "
        "(quick start: `docker compose up -d` provides one — see SETUP.md)."
    )


class AsyncDatabase:
    """Lifecycle wrapper around a SQLAlchemy async engine (PostgreSQL).

    One instance per process is the intended pattern.  Pass it to every store
    that needs database access; the underlying connection pool is shared.

    Usage::

        db = AsyncDatabase("postgresql://errander:errander@localhost/errander")
        await db.close()   # on shutdown

        async with db.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
    """

    def __init__(self, url: str) -> None:
        self._url = _normalize_url(url)
        self._engine: AsyncEngine = create_async_engine(self._url)

    @contextlib.asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncConnection]:
        """Yield an AsyncConnection inside a transaction.

        Auto-commits on clean exit; rolls back on any exception.
        """
        async with self._engine.begin() as conn:
            yield conn

    async def close(self) -> None:
        """Dispose the engine and its connection pool."""
        await self._engine.dispose()
