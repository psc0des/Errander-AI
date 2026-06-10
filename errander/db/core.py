"""Thin async database wrapper supporting SQLite (aiosqlite) and PostgreSQL (asyncpg).

URL normalization:
  ":memory:"            -> "sqlite+aiosqlite:///:memory:"
  "path.sqlite"         -> "sqlite+aiosqlite:///path.sqlite"
  "sqlite:///path"      -> kept as-is (already valid SQLAlchemy URL)
  "postgresql://..."    -> "postgresql+asyncpg://..."
  "postgres://..."      -> "postgresql+asyncpg://..."  (Heroku-style)
  "postgresql+asyncpg://..." -> kept as-is

The begin() context manager yields a SQLAlchemy AsyncConnection in an open
transaction: auto-commits on clean exit, rolls back on exception.

StaticPool is used for :memory: SQLite so that multiple begin() calls share
the same underlying connection — without it each call opens a new connection
and sees an empty database (all migrations lost).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
from sqlalchemy.pool import NullPool, StaticPool


def _normalize_url(url: str) -> str:
    """Convert any supported DB URL to a fully-qualified SQLAlchemy URL."""
    if url == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if "://" not in url:
        return f"sqlite+aiosqlite:///{url}"
    return url


def _engine_kwargs(url: str) -> dict[str, Any]:
    if ":memory:" in url:
        # StaticPool: reuse the same connection so in-memory state persists
        # across multiple begin() calls in the same process.
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    if url.startswith("sqlite"):
        # NullPool: no connection pool for file-based SQLite (single-writer).
        return {"poolclass": NullPool}
    # PostgreSQL: use SQLAlchemy's default async pool (size tunable later).
    return {}


class AsyncDatabase:
    """Lifecycle wrapper around a SQLAlchemy async engine.

    One instance per process is the intended pattern.  Pass it to every store
    that needs database access; the underlying connection pool is shared.

    Usage::

        db = AsyncDatabase("errander.sqlite")
        await db.close()   # on shutdown

        async with db.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
    """

    def __init__(self, url: str) -> None:
        self._url = _normalize_url(url)
        self._engine: AsyncEngine = create_async_engine(
            self._url,
            **_engine_kwargs(self._url),
        )

    @property
    def dialect(self) -> str:
        """Return 'sqlite' or 'postgresql'."""
        return "sqlite" if self._url.startswith("sqlite") else "postgresql"

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
