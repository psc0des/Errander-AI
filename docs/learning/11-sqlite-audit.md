# 11 — SQLite Audit Integration

## What Was Built and Why

Errander-AI already had an `AuditStore` backed by SQLite, but it was write-heavy — you could log events and filter by batch/VM/event_type. Three things were missing:

1. **`action_type` filter** — `get_events()` had no way to ask "show me only disk_cleanup events"
2. **Batch summaries** — no way to ask "what batches ran recently?" without reading every individual event
3. **CLI access** — no way to query the audit DB without writing Python

The goal: make the audit trail queryable from the command line, natively, with no external tools.

```bash
# What ran in the last 10 batches?
uv run python -m errander --audit --batches --last 10

# What happened during a specific batch?
uv run python -m errander --audit --batch-id run-2026-04-03 --last 50

# Did disk_cleanup complete on all VMs?
uv run python -m errander --audit --action-type disk_cleanup --event-type action_completed
```

---

## Key Concepts

### 1. Extending a filter-builder with a new clause

`get_events()` builds a `WHERE` clause dynamically by appending to `clauses` and `params` lists. Adding `action_type` is just one more optional append:

```python
if action_type is not None:
    clauses.append("action_type = ?")
    params.append(action_type)
```

The `?` placeholder pattern (used by both `sqlite3` and `aiosqlite`) prevents SQL injection. Never interpolate values directly into query strings.

### 2. Aggregation with GROUP_CONCAT(DISTINCT ...)

`get_recent_batches()` needs to return, per batch: when it started, how many events it had, and which VMs were touched — all in one query.

```sql
SELECT
    batch_id,
    MIN(timestamp) AS started_at,
    COUNT(*) AS event_count,
    GROUP_CONCAT(DISTINCT vm_id) AS vm_ids
FROM audit_events
GROUP BY batch_id
ORDER BY started_at DESC
LIMIT ?
```

Key points:
- `MIN(timestamp)` → earliest event = when the batch started
- `COUNT(*)` → total events in the batch
- `GROUP_CONCAT(DISTINCT vm_id)` → comma-separated unique VM IDs per batch
- SQLite supports `DISTINCT` inside aggregate functions natively

The result is one row per batch, not one row per event — much cheaper for a "what ran recently?" query.

### 3. Parsing GROUP_CONCAT output in Python

`GROUP_CONCAT` returns a comma-separated string (or `None` if all values were NULL). The Python side needs to handle both cases:

```python
vm_ids_raw = row[3]
vm_ids: list[str] = (
    [v for v in str(vm_ids_raw).split(",") if v and v != "None"]
    if vm_ids_raw is not None
    else []
)
```

The `v != "None"` guard handles the case where SQLite returns the literal string `"None"` when `vm_id` column values are NULL — SQLite's `GROUP_CONCAT` includes NULL values in some edge cases when cast to string.

### 4. CLI audit mode — short-circuit before any heavy setup

The `--audit` flag is checked immediately after `load_settings()`, before building SSH connections, starting the metrics server, or loading the inventory:

```python
settings = load_settings(...)

if args.audit:
    return await run_audit_query(args, settings)  # exits here

inventory = validate_inventory(...)  # only reached for --run-now / scheduler
```

This keeps the audit command fast and dependency-free — it only needs the DB path from settings.

### 5. EventType validation at the CLI boundary

User input (`--event-type action_started`) must be validated before hitting the database. The `EventType` enum constructor raises `ValueError` on unknown values:

```python
try:
    event_type = EventType(args.event_type.lower())
except ValueError:
    valid = [e.value for e in EventType]
    print(f"Unknown event type '{args.event_type}'. Valid: {valid}")
    return 1
```

This is the right place to validate — at the boundary between user input and internal code.

---

## Integration Tests: Running the Real Graph

The most valuable tests in this feature aren't the unit tests — they're the integration tests that run the actual `vm_graph` with a real `:memory:` SQLite database and assert on what ends up in the audit trail.

### Pattern: mock SSH, keep everything else real

```python
with (
    patch("errander.agent.vm_graph.detect_os", new=AsyncMock(return_value=fake_vm_info)),
    patch("errander.agent.vm_graph._run_disk_cleanup", new=AsyncMock(return_value=disk_cleanup_result)),
):
    await graph.ainvoke(initial)
```

Only the I/O boundary (SSH) is mocked. The graph routing, `audit_results_node`, and `AuditStore.log_event()` all run for real. This gives confidence that the right events are written with the right fields.

### What to assert

Don't just assert `len(events) >= 1`. Assert on the specific fields that matter:

```python
events = await audit_store.get_events(
    batch_id="integration-batch-01",
    action_type="disk_cleanup",
)
assert len(events) >= 1
assert events[0].event_type == EventType.ACTION_COMPLETED
assert events[0].vm_id == "test/vm-01"
assert events[0].batch_id == "integration-batch-01"
```

This tests that the graph correctly populated all three key fields through the full pipeline.

### Testing error paths

Lock failure → `audit_results_node` still runs (it's on all paths via `route_after_lock → audit_results`). The test pre-acquires the lock, runs the graph, then checks that an `ACTION_FAILED` event with "locked" in the detail was written:

```python
await locker.acquire(vm_id, "other-batch")
# ... run graph ...
failed = [e for e in events if e.event_type == EventType.ACTION_FAILED]
assert len(failed) >= 1
assert "locked" in failed[0].detail.lower()
```

---

## Gotchas

### GROUP_CONCAT and NULL values
SQLite's `GROUP_CONCAT` skips `NULL` values by default in standard mode, but when Python converts the row via `str()`, NULLs can appear as `"None"`. Always filter them out explicitly when parsing the result.

### `aiosqlite.execute_fetchall` vs `cursor.fetchall`
`aiosqlite` provides `execute_fetchall(query, params)` as a convenience that combines `execute()` + `fetchall()` in one call. Use it for SELECT queries where you want all results. For queries where you only need one row (like `COUNT(*)`), use `cursor = await db.execute(...)` then `await cursor.fetchone()`.

### Settings is a dataclass — fields are mutable in tests
`Settings` uses `@dataclass`, so test code can safely override fields:
```python
settings = load_settings()
settings.audit_db_url = str(tmp_path / "test.sqlite")
```
No need for `patch()` — just assign directly.

---

## Quiz

1. Why does `get_recent_batches()` use `MIN(timestamp)` instead of just `timestamp`?
2. What SQL keyword makes `GROUP_CONCAT` return unique VM IDs only?
3. Why is `--audit` checked before `validate_inventory()`?
4. What's the difference between `execute_fetchall()` and `execute()` + `fetchone()` in aiosqlite?
5. Why do integration tests mock SSH but not `AuditStore`?
