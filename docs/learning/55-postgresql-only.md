# 55 — PostgreSQL-Only Migration (dropping SQLite)

## What was built and why

One day after shipping the SQLite+PostgreSQL dual-backend (learning doc 54), the owner
made a strategic call: **drop SQLite entirely and standardize on PostgreSQL** — "we need
to create standard and it will be less headache for users." This doc covers the conversion
and, more importantly, what we learned about what "dual-backend" actually tested.

The dual-backend work was not wasted: the SQLAlchemy Core async layer (`AsyncDatabase`,
named-param `text()` queries, the migration registry) is exactly what made this step a
deletion exercise rather than a rewrite. We removed the SQLite half; the abstraction stayed.

## Key changes

### 1. `AsyncDatabase` rejects non-Postgres URLs

```python
def _normalize_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):       # Heroku-style
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(...)  # points the user at SETUP.md / docker compose
```

`StaticPool` / `NullPool` SQLite branches are gone — Postgres uses SQLAlchemy's default
async pool. The `dialect` property is gone; so is every `if dialect == "sqlite"` branch
(`GROUP_CONCAT`→`STRING_AGG`, `rowid`→`id`, `_adapt_ddl`).

### 2. DDL written in Postgres flavor directly

`INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY` in the migration registry
itself, not at runtime. `run_migrations(conn, dialect)` → `run_migrations(conn)`.

### 3. The int32 trap

**SQLite's INTEGER is always 64-bit. PostgreSQL's INTEGER is 32-bit.** The first Postgres
test run failed instantly:

```
asyncpg.exceptions.DataError: invalid input for query argument $4: 5000000000
(value out of int32 range)
```

`vm_disk_history.used_bytes` stored byte counts — 5 GB overflows int32. Fix: `BIGINT` for
byte counts and epoch timestamps. **Lesson: any column that ever held a number > 2.1 billion
on SQLite was silently fine; on Postgres it's a runtime error.** Audit your INTEGER columns
when porting.

### 4. LangGraph checkpointer

`AsyncSqliteSaver.from_conn_string(audit_db_url)` would have silently degraded to
no-checkpointing on a Postgres URL (the init is wrapped in try/except). Replaced with
`langgraph-checkpoint-postgres`'s `AsyncPostgresSaver`. Two gotchas:

- It uses **psycopg3, not asyncpg** — needs a plain `postgresql://` URL (strip `+asyncpg`).
- It needs a one-time `.setup()` call to create its checkpoint tables (idempotent).

### 5. Test isolation: TRUNCATE replaces fresh `:memory:` databases

The old pattern: every test constructed `AsyncDatabase(":memory:")` → automatically isolated.
The new pattern in `tests/conftest.py`:

- **Session-scoped migration fixture** (sync, runs `asyncio.run()` — avoids pytest-asyncio
  loop-scope headaches with session async fixtures) applies migrations once; fails fast with
  "run `docker compose up -d`" if Postgres is unreachable.
- **Autouse `_clean_test_db` fixture** truncates every table except `schema_migrations` /
  `checkpoint_migrations` before each test: same observable isolation, one shared database.
- `make_test_db()` is a plain importable function returning `AsyncDatabase(TEST_DB_URL)` —
  the mechanical replacement for all 159 `AsyncDatabase(":memory:")` call sites.

Suite time went from ~35 s (in-memory SQLite) to ~5 min (real Postgres over TCP with
per-test truncates). That is the honest cost of testing against the real engine.

### 6. The CI lie we caught

The Step-1 "Postgres CI job" set `ERRANDER_TEST_DB_URL`, but the tests constructed
`AsyncDatabase(":memory:")` *inline*, ignoring the env var — so the "Postgres" job was
mostly re-testing SQLite. **Lesson: an env-var-driven test matrix only works if every
test actually routes through the env var.** The `make_test_db()` chokepoint fixes this
structurally: there is now exactly one place tests get a database from.

### 7. Zero-config story preserved via Docker Compose

`docker-compose.yml` ships a `postgres:16` with `errander` (runtime) and `errander_test`
(pytest) databases. The `ERRANDER_AUDIT_DB_URL` default matches it, so
clone → `docker compose up -d` → run still works with no configuration.

## Gotchas recap

1. INTEGER → BIGINT for byte counts / epochs (int32 overflow).
2. `PRAGMA table_info` doesn't exist on Postgres — use `information_schema.columns`
   (or `sa_inspect` for table lists).
3. AsyncPostgresSaver wants psycopg-style URLs and a `.setup()` call.
4. "Fresh DB without migrations" test premises die with a shared migrated database —
   tests asserting "no such table" behavior were rewritten to patch `begin()` instead.
5. Editing migration DDL in place requires resetting dev/test databases — the migration
   registry tracks versions, so already-applied migrations never re-run.

## Quiz yourself

1. Why did `used_bytes INTEGER` work on SQLite for months but explode on Postgres day one?
2. Why is the session migration fixture sync (`asyncio.run`) instead of an async fixture?
3. What two tables must per-test TRUNCATE skip, and what breaks if it doesn't?
4. Why did the old Postgres CI job mostly test SQLite despite setting `ERRANDER_TEST_DB_URL`?
5. Why does `AsyncPostgresSaver` get a different URL string than `AsyncDatabase`?
