# 54 — PostgreSQL Dual-Backend (SQLAlchemy Core Async)

## What was built and why

Errander-AI's v1 stores all use SQLite via `aiosqlite` directly. For v2 (hardened
multi-process deployments), PostgreSQL support is required. This change replaces every
`aiosqlite` call site with SQLAlchemy Core async so the same code works against either
backend without branching.

## Key concepts

### AsyncDatabase wrapper

`errander/db/core.py` provides a thin `AsyncDatabase` class:

```python
class AsyncDatabase:
    def __init__(self, url: str) -> None:
        self._url = _normalize_url(url)
        self._engine = create_async_engine(self._url, **_engine_kwargs(self._url))

    @property
    def dialect(self) -> str:
        return "sqlite" if self._url.startswith("sqlite") else "postgresql"

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.begin() as conn:
            yield conn  # auto-commits on clean exit, rolls back on exception
```

One `AsyncDatabase` instance per process. Pass it to every store; the engine's
connection pool is shared.

### URL normalization

`_normalize_url` handles all common URL forms:

| Input | SQLAlchemy URL |
|---|---|
| `":memory:"` | `sqlite+aiosqlite:///:memory:` |
| `"errander.sqlite"` | `sqlite+aiosqlite:///errander.sqlite` |
| `"postgresql://..."` | `postgresql+asyncpg://...` |
| `"postgres://..."` (Heroku) | `postgresql+asyncpg://...` |

### StaticPool for in-memory SQLite

**This is the critical gotcha.** Without `StaticPool`, each `engine.begin()` call opens
a new SQLite connection — which for `:memory:` means a brand-new empty database. Every
migration run would see empty tables.

```python
def _engine_kwargs(url: str) -> dict:
    if ":memory:" in url:
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,   # MANDATORY for :memory:
        }
    if url.startswith("sqlite"):
        return {"poolclass": NullPool}  # no pool for file SQLite
    return {}                           # Postgres: default async pool
```

`StaticPool` tells SQLAlchemy to reuse the same underlying connection for all `begin()`
calls on the same engine. This is why a single `AsyncDatabase(":memory:")` instance
shared between `AuditStore` and `VMFactsStore` works correctly — both stores see the
same in-memory database.

### Named parameter style

SQLAlchemy Core uses `:name` style for all dialects (unlike `aiosqlite`'s `?`):

```python
# Before (aiosqlite)
await conn.execute("INSERT INTO foo VALUES (?, ?)", (a, b))

# After (SQLAlchemy)
await conn.execute(text("INSERT INTO foo VALUES (:a, :b)"), {"a": a, "b": b})
```

Always use `text()` wrapper for raw SQL strings. Positional row access still works
(`row[0]`), but `result.mappings().fetchone()` gives dict-style access (`row["column"]`).

### Dialect-specific DML fixes

Five patterns needed dialect-specific handling:

| Pattern | SQLite | PostgreSQL |
|---|---|---|
| `GROUP_CONCAT(DISTINCT col)` | as-is | `STRING_AGG(DISTINCT col, ',')` |
| `INSERT OR REPLACE INTO` | as-is | `ON CONFLICT(...) DO UPDATE SET ...` |
| `INSERT OR IGNORE INTO` | as-is | `ON CONFLICT(...) DO NOTHING` |
| `rowid DESC` order | as-is | `id DESC` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | as-is | `BIGSERIAL PRIMARY KEY` |

The `_adapt_ddl(sql, dialect)` function in `migrations.py` handles the DDL substitution.
The DML fixes are applied inline in each store using `self._db.dialect`.

### Migrations #10-#12

Three stores previously created their own tables via inline DDL in `initialize()` — outside
the migration registry. This caused issues when stores were used standalone in tests
(migrations never ran). They're now in the migration registry:

- Migration #10: `settings_overrides` + `inventory_overrides` (from `overrides.py`)
- Migration #11: `ai_decisions` + indexes (from `ai_audit.py`)
- Migration #12: `deferred_executions` + indexes (from `deferred.py`)

Each store's `initialize()` now calls `run_migrations(conn, dialect)` which is idempotent
(tracks applied versions in `schema_migrations`), so calling it from multiple stores is safe.

### TYPE_CHECKING imports

With `from __future__ import annotations` (PEP 563), all annotations are lazy strings at
runtime. Imports used only in type signatures can be placed in `if TYPE_CHECKING:` blocks —
ruff's `TC001/TC002/TC003` rules enforce this:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

class MyStore:
    def __init__(self, db: AsyncDatabase) -> None:  # annotation = string at runtime
        self._db = db
```

The `AsyncDatabase` instance is passed at runtime; it doesn't need to be importable from
within `MyStore`'s module. IDEs and mypy still see it correctly.

## Test infrastructure

### Connection sharing in tests

Old pattern (broken — needed `aiosqlite.Connection` injection):
```python
facts._db = audit._db  # share the aiosqlite connection
```

New pattern (works — `StaticPool` makes sharing implicit):
```python
db = AsyncDatabase(":memory:")
audit = AuditStore(db, strict_mode=False)
facts = VMFactsStore(db)  # both see the same in-memory DB
```

### `TEST_DB_URL` for Postgres CI

`tests/conftest.py` captures `ERRANDER_TEST_DB_URL` at import time (before `clean_errander_env`
strips all `ERRANDER_*` vars):

```python
TEST_DB_URL: str = os.getenv("ERRANDER_TEST_DB_URL", ":memory:")
```

The `session_db` and `async_db` fixtures use this URL. When the Postgres CI job sets
`ERRANDER_TEST_DB_URL=postgresql+asyncpg://...`, tests that opt in via `async_db` fixture
run against real Postgres. Other tests default to `:memory:` SQLite.

## Postgres CI job

`.github/workflows/ci.yml` adds a `test-postgres` job that:
1. Spins up a `postgres:15` service container
2. Creates `errander_agent` + `errander_web` roles
3. Runs `pytest tests/safety/ tests/observability/ tests/ai_evals/`
4. Verifies `errander_web` cannot INSERT on `audit_events`

The role-grant verification step is the key safety check — it proves the web role is
read-only on audit tables even after `DEFAULT PRIVILEGES` grants.

## Gotchas

1. **`StaticPool` is mandatory for `:memory:`** — see above. Without it, every `begin()`
   sees an empty database.

2. **Batch-replace scripts need careful multiline import parsing** — the first version of
   the TC fix script corrupted `db/core.py` by moving import items inside the
   `if TYPE_CHECKING:` block. Test mechanical scripts on one file before running at scale.

3. **`clean_errander_env` autouse fixture strips all `ERRANDER_*` env vars** — `TEST_DB_URL`
   must be captured at module import time, not inside a fixture, or it'll always be `:memory:`.

4. **`ai_audit.py` inline ALTER TABLE guards** — these added columns dynamically in
   `initialize()`. Once the full table DDL is in migration #11, these guards were removed.
   On fresh databases, migration #11 creates the correct schema. Existing SQLite databases
   (from before this change) won't be affected since migration #11 is only applied to fresh
   databases — existing ones already have the columns from the ALTER TABLE.
