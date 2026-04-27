# 24 — Deferred Execution: Window-Gated Approval

## What Was Built and Why

Previously, approval and execution were tightly coupled: if a batch ran at 2 AM (inside the window), the operator had to approve at 2 AM too. There was no way to approve in the morning and have the agent execute at the next scheduled window.

The deferred execution feature decouples approval from execution:

```
10 AM  Dry-run batch runs (discovers, plans, simulates)
 1 PM  Operator reviews Slack report and clicks Approve
11 PM  Maintenance window opens → agent executes live automatically
```

Approval means "yes, run maintenance on this fleet at the next window" — not "execute this exact plan right now."

---

## Architecture

Two mechanisms were added:

**1. Deferred record saved on dry-run approval (outside window)**

When `approval_gate_node` receives an approval AND the run is a dry-run AND we're currently outside the window, it:
- Computes `next_window_open(now, window)` — the next future window start
- Saves a `DeferredExecution` record to SQLite (`deferred_executions` table)
- Logs `EXECUTION_DEFERRED` audit event
- Posts Slack notification: "Execution scheduled for 2026-04-27 23:00 UTC"
- Returns `{"approved": True, "deferred": True}` → batch ends here, no live execution

**2. Window-opener scheduler job**

For each environment with a maintenance window, a second cron job (`window-opener-{env}`) is registered in addition to the normal maintenance job. It fires at exactly `window.start_hour:00` on window days.

On trigger, `_window_opener()`:
1. Calls `expire_old()` — marks any overdue pending records as expired
2. Calls `get_pending(env_name)` — retrieves pending deferred records
3. For each record: marks it `executing`, logs `DEFERRED_EXECUTION_STARTED`, calls `run_env_batch(dry_run=False, force=True, ...)`, marks it `done`

Re-discovery happens at execution time (not stored), so if VM state changed between approval and execution, the agent works from fresh data.

---

## Key Concepts

### `DeferredExecutionStore` — SQLite alongside `AuditStore`

```python
store = DeferredExecutionStore("errander.sqlite")  # same file as AuditStore
await store.initialize()   # creates table if not exists
await store.save(batch_id, env_name, approved_by, window_start)
pending = await store.get_pending("production")
await store.mark_executing(batch_id)
await store.mark_done(batch_id)
count = await store.expire_old()   # marks past-expiry pending records as expired
await store.close()
```

The table uses `ON CONFLICT(batch_id) DO UPDATE` so saving the same batch_id twice replaces the record (idempotent).

`expiry_at = window_start + 7 days` — stale approvals auto-expire. `get_pending()` filters `WHERE expiry_at > now` so expired records are never picked up.

Status transitions:
```
pending → executing → done
         ↘ expired (via expire_old, if expiry_at has passed)
```

### `next_window_open(now, window) → datetime`

Finds the next strictly future window start (UTC):

```python
for days_ahead in range(0, 8):
    candidate = local_now + timedelta(days=days_ahead)
    candidate_start = candidate.replace(hour=window.start_hour, minute=0, ...)
    day_name = candidate_start.strftime("%A").lower()
    if day_name in window.days and candidate_start > local_now:
        return candidate_start.astimezone(timezone.utc)
```

Key edge cases:
- **Before window today**: returns today at `start_hour` (e.g., Monday 00:00 → returns Monday 02:00)
- **Inside window (start_hour already passed)**: returns the same window NEXT week (e.g., Monday 03:00 with Mon 02:00-06:00 → next Monday 02:00)
- **At exactly start_hour**: `candidate_start > local_now` is `False` (equal, not greater) → skipped, returns next occurrence

### `window_start_cron(window) → str`

Converts a `MaintenanceWindow` to a cron expression for APScheduler:

```python
# days=["tuesday", "thursday"], start_hour=23
# → "0 23 * * tue,thu"

# All 7 days → "0 4 * * *"
```

### Approval gate deferred logic

```python
# Only triggers when ALL of these are true:
if approved and dry_run and window is not None:
    now = datetime.now(tz=timezone.utc)
    if not check_window_from_config(now, window):
        # Outside window — defer
        next_open = next_window_open(now, window)
        await deferred_store.save(batch_id, env_name, approver, next_open)
        await audit_store.log_event(AuditEvent(EventType.EXECUTION_DEFERRED, ...))
        await slack_client.post_alert(f"Scheduled for {next_open.strftime(...)}")
        return {"approved": True, "deferred": True}

return {"approved": approved, "deferred": False}
```

If `dry_run=False` (live run) or no window is configured, deferral is skipped even if approved outside hours. `--force` runs bypass the window check in `validate_window_node` and never reach deferral.

---

## Code Walkthrough — New Files

### `errander/safety/deferred.py`

```python
_UPSERT_SQL = """
INSERT INTO deferred_executions
    (batch_id, env_name, approved_at, approved_by, window_start, expiry_at, status, created_at)
VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
ON CONFLICT(batch_id) DO UPDATE SET
    approved_at  = excluded.approved_at,
    approved_by  = excluded.approved_by,
    window_start = excluded.window_start,
    expiry_at    = excluded.expiry_at,
    status       = 'pending',
    executed_at  = NULL
"""
```

The upsert pattern means re-approving the same batch (unlikely but possible) resets it to `pending` and updates the window time.

`expire_old()` is called at the start of every `_window_opener()` invocation to clean up stale records before querying pending ones.

---

## Gotchas

**`get_pending()` uses the real clock.**
`get_pending()` filters `WHERE expiry_at > datetime.now(tz=UTC)`. In tests, if you mock `now` to a past date (e.g., "Monday 2026-04-06"), the `window_start + 7 days` expiry will be in the past relative to the test run date, and `get_pending()` returns nothing. Fix: use future mock dates far enough ahead that the expiry is still in the future (e.g., `2030-01-07`).

**`daily` in YAML maintenance_days is not a valid `MaintenanceWindow` day.**
`MaintenanceWindow.__post_init__` validates against the 7 weekday names. The inventory validator doesn't check this — but `_build_maintenance_window()` catches the `ValueError` and returns `None`. So envs with `maintenance_days: [daily]` effectively have no window, meaning approval is never deferred.

**Window-opener fires at `start_hour:00`, not at an offset.**
The cron string `"0 2 * * mon"` fires at 02:00 UTC Monday. The batch itself may start a minute or two later. This is intentional — the window is defined as open at `start_hour`, so firing at that exact time is correct.

**`_window_opener` always calls `mark_done` even if `run_env_batch` raises.**
The `finally:` block around `run_env_batch` ensures the record is marked done regardless of the batch outcome. A failed batch is still "done" from the deferred store's perspective — the audit trail captures what happened inside the batch.

---

## Questions to Test Understanding

1. What happens if a user approves a dry-run batch and the maintenance window starts 1 minute later?
   - `check_window_from_config(now, window)` returns False (window opens in 1 minute), so execution is deferred to the next window (next week). The window-opener fires at 02:00 and picks it up.

2. If the operator clicks Approve twice on the same batch, what happens?
   - The upsert replaces the existing record — the second save updates `approved_at`, resets `status` to `pending`, and clears `executed_at`. Net effect: the record is refreshed with the new approval timestamp but the window_start is unchanged.

3. What prevents a deferred record from sitting forever if `_window_opener` crashes?
   - `expire_old()` at the start of each window-opener call expires records older than 7 days. If the agent crashes before `mark_done`, the record stays in `executing` state. On the next run, `get_pending()` won't return it (it's `executing`, not `pending`). This is an edge case — a future enhancement could detect stuck `executing` records and reset them.

4. Why does `deferred=False` when `dry_run=False` even if outside the window?
   - Live runs already have explicit intent to execute immediately (operator explicitly used `--live`). Deferring a live run would be surprising and counterproductive. The deferral logic is only for the "scan now, execute later" workflow.
