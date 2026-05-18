# 41 — Durability Measurement (Phase A1 + B1/B2)

## What was built and why

Two parallel projects shipped in one session:

**Phase A1 — Measurement infrastructure**
- `--measure-durability` CLI: computes batch completion rate, duration percentiles, approval wait stats, per-action duration percentiles, and interrupted-batch count from existing `audit_events` data. No new tables, no schema migrations.
- Startup orphan-batch scanner: detects batches with `batch_started` but no `batch_completed`/`fleet_abort` in the last 7 days. Logs each as WARNING, increments `BATCHES_INTERRUPTED_TOTAL` Prometheus counter.
- Two new counters in `metrics.py`: `AGENT_STARTS_TOTAL` (proxy for restart frequency) and `BATCHES_INTERRUPTED_TOTAL`.

**Phase B1/B2 — Operational learning memory**
- `VMFactsStore`: reads `audit_events` to derive three evidence-based fact models — `ActionOutcomeFact` (success rate, last failure reason, last success timestamp), `VMRebootPatternFact` (reboots per patching cycle), `ActionRejectionFact` (rejection count + reasons in last 90d).
- `OperatorAssistant` integration: `_build_context()` queries `VMFactsStore` when provided; `_format_prompt()` adds an "Operational history facts" section; `_fallback_response()` flags low success-rate actions as high-risk and frequently-rejected actions as medium-risk.

## Key concepts

### Percentile math without numpy

The `_pct(values, p)` function in `durability.py` uses the nearest-rank method:

```python
def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]
```

This avoids the numpy dependency while giving accurate p50/p95/max for the sample sizes we care about (typically < 100).

### Subquery pattern to avoid variable-length IN clauses

SQLite parameterized queries don't support `IN (?, ?, ?)` with a dynamic list. Instead of building a parameterized list, use a subquery:

```sql
WHERE batch_id IN (
    SELECT DISTINCT batch_id FROM audit_events
    WHERE event_type = 'batch_started' AND timestamp >= ?
)
```

One parameter (`cutoff`), no variable-length binding. Used throughout `durability.py`.

### asynccontextmanager for test DB fixture

`aiosqlite.connect()` returns a `Connection` object that starts a background thread when awaited. Once started, the thread cannot be started again — so `async with await _make_db() as db` double-awaits and raises `RuntimeError: threads can only be started once`.

Fix: use `@asynccontextmanager`:

```python
@asynccontextmanager
async def _make_db() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(":memory:") as db:
        await run_migrations(db)
        yield db
```

Tests use `async with _make_db() as db:` — the `async with` starts the thread via `__aenter__`, and `__aexit__` closes it cleanly.

### TYPE_CHECKING guard for cross-layer annotations

`FleetContext` (in `errander/models/analysis.py`) needed three new fields typed with fact models from `errander/safety/vm_facts.py`. Directly importing at module level would have been fine since there's no circular dependency, but using `TYPE_CHECKING` avoids the runtime import overhead and satisfies ruff's TC001 rule:

```python
from __future__ import annotations  # makes all annotations lazy strings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.safety.vm_facts import ActionOutcomeFact, VMRebootPatternFact, ActionRejectionFact
```

Fields on the dataclass:
```python
action_outcomes: list[ActionOutcomeFact] = field(default_factory=list)
reboot_patterns: list[VMRebootPatternFact] = field(default_factory=list)
frequently_rejected_actions: list[ActionRejectionFact] = field(default_factory=list)
```

Because annotations are lazy (PEP 563), the `ActionOutcomeFact` references are strings at runtime — they're never evaluated, so the TYPE_CHECKING-guarded import is never executed.

### TypedDict for nested accumulator dicts

When accumulating structured data in a dict, use a TypedDict instead of `dict[str, object]` to get proper mypy typing:

```python
class _Entry(TypedDict):
    count: int
    reasons: list[str]

action_type_data: dict[str, _Entry] = {}
```

This lets mypy type-check `entry["count"] += 1` and `entry["reasons"].append(reason)` correctly, avoiding the `list(info["reasons"])` → `list(object)` error.

### execute_fetchall returns Iterable, not list

`aiosqlite`'s type stubs type `execute_fetchall` as returning `Iterable[Row]`, not `list[Row]`. Iterating with `for row in rows:` works fine. Integer indexing (`rows[0]`) does not — wrap with `list()`:

```python
reboot_rows = list(await db.execute_fetchall("SELECT COUNT(*) ...", [vm_id]))
count = int(str(reboot_rows[0][0])) if reboot_rows else 0
```

### ruff TC rules in test files

Ruff's TCH (type-checking import) rules suggest moving annotation-only imports to `TYPE_CHECKING` blocks. In test files, this breaks pytest fixture injection: pytest calls `get_type_hints()` at runtime to resolve fixture type annotations, so `import pytest` must remain a real import. Fix: add test files to `per-file-ignores`:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["TCH"]
```

## Gotchas

1. **Loop variable narrowing**: mypy tracks narrowed types across loop iterations. If you narrow `end_str: str | None` to `str` via `if x is None: continue`, the next iteration's assignment `end_str = dict.get(key)` (returning `str | None`) conflicts. Fix: rename the variable (`act_end_str`) or add an explicit annotation at the top of the loop body.

2. **`MAX(timestamp)` with ties**: `SELECT event_type, MAX(timestamp) FROM ... GROUP BY batch_id` returns an undefined `event_type` when multiple rows share the same max timestamp. SQLite picks any row. Fix: `SELECT event_type, timestamp FROM ... ORDER BY timestamp DESC, rowid DESC LIMIT 1` — rowid breaks timestamp ties by insertion order.

3. **Windows console encoding**: `print()` to cp1252 console fails on `→` (U+2192) and `—` (U+2014). Use ASCII equivalents (`->`, spaces) in `print_durability_report()`.

4. **VMFactsStore.__new__ in tests**: Bypassing `__init__` with `VMFactsStore.__new__(VMFactsStore)` then setting `facts._db = audit._db` lets tests share a single in-memory connection. This works because `_ensure_connected()` checks only `_db`, not `_db_path`. Don't try to use this object as a context manager — `__aenter__` calls `initialize()` which reads `_db_path` (unset).

## Quiz

1. Why does `async with await _make_db() as db:` raise a RuntimeError?
2. What query pattern do we use instead of `WHERE batch_id IN (?, ?, ...)`?
3. Why are `ActionOutcomeFact` etc. under `TYPE_CHECKING` in `analysis.py` instead of imported at module level?
4. What does `BATCHES_INTERRUPTED_TOTAL` measure, and when is it incremented?
5. Why can't we move `import pytest` to `TYPE_CHECKING` in test files?
