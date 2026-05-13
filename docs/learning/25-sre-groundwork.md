---
title: "25 — SRE Monitoring Groundwork (PR-G)"
---

# SRE Monitoring Groundwork

## What was built and why

PR-G adds the foundational layer for SRE-style signal collection without changing any existing agent behaviour. Every subsequent SRE feature (package lock detection, reboot tracking, drift detection, disk growth trends) builds on exactly these pieces.

Six deliverables:

| Label | What |
|---|---|
| G1 | `ActionStatus.BLOCKED` — new enum value, distinct from FAILED and SKIPPED |
| G2 | 8 new `EventType` values for SRE signals |
| G3 | `VMTarget.critical_services` field with host-overrides-env inheritance |
| G4 | Migration runner (`errander/safety/migrations.py`), `AuditStore` delegates to it |
| G5 | Three new SQLite-backed async stores: `VMStateStore`, `BaselineStore`, `VMDiskHistoryStore` |
| G6 | `BatchReport` model, `SRESignalSettings` config block |

## Key concepts

### ActionStatus.BLOCKED vs. FAILED vs. SKIPPED

`BLOCKED` means a pre-flight gate deliberately refused to let the action run — not a runtime error, not a maintenance-window skip. Audit events use `ACTION_COMPLETED` with `{"status": "blocked", ...}` in metadata so alerting rules page on `ACTION_FAILED` but summarise `BLOCKED` in daily reports.

### Numbered idempotent migrations

Every schema change is a numbered migration in `_MIGRATIONS: list[tuple[int, str]]`. The runner bootstraps a `schema_migrations` table (CREATE IF NOT EXISTS), reads which versions are already applied, and runs only the pending ones. Each migration wraps its statements in a commit.

```python
for raw_stmt in sql.split(";"):
    stmt = raw_stmt.strip()
    if stmt:
        await db.execute(stmt)
```

Splitting by ";" handles multi-statement migrations (CREATE TABLE + CREATE INDEX) without needing SQLite-specific pragma or executescript().

**Critical invariant**: never add a UNIQUE constraint on a generated timestamp column. Two `await store.save()` calls within the same test body may produce identical `datetime.now(UTC).isoformat()` strings → `IntegrityError`. Row uniqueness comes from the auto-increment `id`.

### BaselineStore — deterministic "latest" with tiebreaker

```sql
SELECT ... FROM vm_baselines
WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
ORDER BY captured_at DESC, id DESC
LIMIT 1
```

Adding `, id DESC` after the timestamp sort means the highest auto-increment wins when two rows share the same microsecond — crucial for test stability.

The same tiebreaker appears in `_prune()`:

```sql
DELETE FROM vm_baselines
WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
  AND id NOT IN (
      SELECT id FROM vm_baselines
      WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
      ORDER BY captured_at DESC, id DESC
      LIMIT ?
  )
```

### VMStateStore — UPSERT for mutable per-VM facts

Unlike append-only audit events, per-VM facts (needs_reboot, uptime) overwrite the previous record:

```sql
INSERT INTO vm_state (vm_id, needs_reboot, updated_at)
VALUES (?, 0, ?)
ON CONFLICT(vm_id) DO UPDATE SET
    needs_reboot = 0,
    needs_reboot_reason = NULL,
    ...
    updated_at = excluded.updated_at
```

`excluded.*` refers to the values that would have been inserted — SQLite's way to reference the "new" row in an ON CONFLICT clause.

### DriftCheck Protocol

```python
class DriftCheck(Protocol):
    kind: str
    async def capture(self, ssh: object, vm: object) -> list[BaselineCapture]: ...
```

`Protocol` means any class with the right attributes satisfies the interface — no inheritance required. Phase 2 will add concrete implementations (`AuthorizedKeysDriftCheck`, `SudoersDriftCheck`, etc.) that each satisfy this protocol.

### aiosqlite.Row typing

Row converter helpers must be typed as `row: aiosqlite.Row`, not `row: object`. The `object` type doesn't support subscript access (`row[i]`), causing mypy `[index]` errors. `aiosqlite.Row` is a named alias for `tuple[Any, ...]` which supports indexing.

```python
def _row_to_vm_state(row: aiosqlite.Row) -> VMState:
    ...
    return VMState(vm_id=str(row[0]), ...)
```

## Quiz yourself

1. Why is `UNIQUE(vm_id, baseline_kind, scope_key, captured_at)` dangerous in tests?
2. What does `excluded.updated_at` refer to in an UPSERT's DO UPDATE clause?
3. Why does `ORDER BY captured_at DESC, id DESC` matter for correctness, not just performance?
4. What's the difference between `ActionStatus.BLOCKED` and `ActionStatus.FAILED` from an alerting perspective?
5. Why does `migrations.py` split SQL by ";" instead of using `executescript()`?
