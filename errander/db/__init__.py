"""Async database abstraction layer (SQLAlchemy Core).

Supports SQLite (aiosqlite driver, default) and PostgreSQL (asyncpg driver).
Install asyncpg for PostgreSQL: uv sync --extra postgres
"""

from errander.db.core import AsyncDatabase

__all__ = ["AsyncDatabase"]
