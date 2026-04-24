# 20 — Phase 3 Edge-Case Hardening

## What Was Built and Why

After implementing rolling updates, canary logic, and drift detection in Phase 3 part 1, the agent's decision logic was solid — but there were several places where unhandled exceptions or silent failures could corrupt a live maintenance run without anyone noticing.

This doc covers the five hardening steps:

1. Sub-graph `ainvoke()` exception safety (vm_graph.py)
2. Batch orchestrator `ainvoke()` exception safety (graph.py)
3. Audit `log_event()` retry + swallow (audit.py)
4. Atomic file locking with `os.O_EXCL` + `os.replace()` (locking.py)
5. Settings bounds validation with Pydantic `@field_validator` (schema.py)

Plus two smaller fixes: SSH stale-connection-on-timeout clearing and empty-output guards in sub-graph assess nodes.

---

## Step 1 — Sub-graph Exception Safety

### The Problem

LangGraph sub-graphs run via `await compiled.ainvoke(state)`. If a sub-graph raises (e.g. SSH disconnects mid-command), the exception propagates up through the calling `_run_disk_cleanup()` helper — which means the `release_lock_node` **never runs**. The VM stays locked forever.

### The Fix

Every `_run_*` helper now wraps `ainvoke()` in a two-level except:

```python
try:
    final_state = await compiled.ainvoke(sub_state)
except (ConnectionError, OSError, TimeoutError) as exc:
    logger.error("Sub-graph disk_cleanup failed for %s: %s", vm_id, exc)
    return {
        "action_type": ActionType.DISK_CLEANUP.value,
        "status": ActionStatus.FAILED.value,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "sub-graph raised exception",
        "error": str(exc),
    }
except Exception as exc:  # noqa: BLE001
    logger.exception("Unexpected error in disk_cleanup for %s", vm_id)
    return { ... same shape ... }
```

The key insight: the except clause **returns a valid result dict** rather than re-raising. The caller (dispatch_action_node) always gets back a dict, so it always calls `release_lock_node`.

The `# noqa: BLE001` comment is required — Ruff's `BLE001` rule flags bare `except Exception` as a bad practice. Here it's intentional because we want the lock to release no matter what.

### Why `asyncio.TimeoutError` Was Removed

Python 3.12+ makes `asyncio.TimeoutError` an alias for the built-in `TimeoutError`. Ruff's `UP041` rule flags the redundant alias. The fix: just catch `TimeoutError` — it covers both the built-in and asyncio variant.

---

## Step 2 — Batch Orchestrator Exception Safety

Same pattern one level up: `run_vm_node` in `graph.py` calls `await vm_compiled.ainvoke(state)`. If the entire VM graph crashes (e.g. the discover node raises on a VM that disappears mid-batch), the batch should continue to the next VM.

```python
try:
    final: VMGraphState = await vm_compiled.ainvoke(state)
    return {"vm_results": final.get("results", [])}
except Exception as exc:  # noqa: BLE001
    logger.exception("VM graph crashed for %s", state.get("vm_id"))
    return {"vm_results": [{
        "action_type": "unknown",
        "status": ActionStatus.FAILED.value,
        "vm_id": state.get("vm_id", "unknown"),
        ...
    }]}
```

---

## Step 3 — Audit Resilience

### The Problem

`AuditStore.log_event()` uses SQLite. SQLite can return `OperationalError: database is locked` under concurrent access. A crash here would either:
- Raise into the caller, potentially aborting the batch, or
- Silently drop audit events without any notification

Neither is acceptable.

### The Fix

Retry once on `OperationalError` with 100ms backoff, then swallow persistent failures:

```python
for attempt in (1, 2):
    try:
        await db.execute(_INSERT_SQL, params)
        await db.commit()
        return
    except aiosqlite.OperationalError as exc:
        if attempt == 1:
            logger.warning("Audit write retry (%s)", exc)
            await asyncio.sleep(0.1)
            continue
        logger.error("Audit write failed after retry: %s", exc)
        return
    except aiosqlite.Error as exc:
        logger.error("Audit write failed: %s", exc)
        return
```

The design principle: **audit failures must never abort a live batch**. Losing an audit event is bad; killing a running maintenance job is worse.

---

## Step 4 — Atomic File Locking

### The Problem

The old `acquire()` wrote the lock file with `lock_path.write_text(...)` — a non-atomic operation. Two concurrent `acquire()` calls could both check "no lock exists" and both write, resulting in a lost-write race where one batch thinks it has the lock.

### The Fix (two mechanisms)

**For new lock creation (no existing file):** Use `os.O_CREAT | os.O_EXCL | os.O_WRONLY` — the OS guarantees at most one caller gets the file descriptor; all others get `FileExistsError`:

```python
try:
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
except FileExistsError:
    # Race: another process created the file just before us
    existing2 = self._read_lock(lock_path)
    if existing2 and not existing2.is_expired():
        return False
    # Stale lock in the file — overwrite atomically
    self._write_lock_atomic(lock_path, payload)
    return True
# Won the race — write and close
with os.fdopen(fd, "w") as f:
    f.write(json.dumps(payload))
return True
```

**For stale-lock overwrites:** Use `os.replace()` via a `.tmp` write-then-rename helper:

```python
def _write_lock_atomic(self, lock_path: Path, payload: dict[str, object]) -> None:
    tmp = lock_path.with_suffix(lock_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, lock_path)
```

`os.replace()` is atomic on POSIX. On Windows, it's atomic when source and destination are on the same filesystem — placing the `.tmp` file adjacent to the lock file guarantees this.

The `test_concurrent_acquire_only_one_wins` test verifies this with 10 concurrent `asyncio.gather()` calls — exactly 1 must win.

---

## Step 5 — Settings Bounds Validation

### The Problem

New Phase 3 settings (`rolling_update_percentage`, `wave_failure_threshold`, etc.) had no validation — you could set `rolling_update_percentage=0` and get an infinite wave loop.

### The Fix

Pydantic v2 `@field_validator` with `@classmethod`:

```python
@field_validator("rolling_update_percentage")
@classmethod
def validate_rolling_pct(cls, v: int) -> int:
    if not 1 <= v <= 100:
        msg = f"rolling_update_percentage must be in [1, 100], got {v}"
        raise ValueError(msg)
    return v
```

One validator can cover multiple fields:

```python
@field_validator("wave_failure_threshold", "fleet_failure_threshold")
@classmethod
def validate_failure_threshold(cls, v: float) -> float:
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"failure threshold must be in [0.0, 1.0], got {v}")
    return v
```

Pydantic uses the `field` names in the `ValidationError` message, so the test `pytest.raises(ValidationError, match="rolling_update_percentage")` works.

---

## Gotchas

### `asyncio.TimeoutError` vs `TimeoutError` (Python 3.12+)

In Python 3.12, `asyncio.TimeoutError` was made an alias for the built-in `TimeoutError`. Ruff's `UP041` flags `asyncio.TimeoutError` in except clauses. Remove the `asyncio.` prefix — the built-in catches both.

### `# noqa: BLE001` is a signal, not a shortcut

Ruff's `BLE001` ("blind exception") exists to prevent swallowing errors accidentally. Every `except Exception` in this codebase is annotated with a comment explaining *why* it's intentional — usually to keep the lock-release node reachable.

### Logger must be declared after all imports

Ruff's `E402` ("module level import not at top") fires if you insert `logger = logging.getLogger(__name__)` between stdlib and application imports. Declare it after all imports.

### Empty-output checks in assess nodes

`df -h` and `docker images | wc -l` should *always* produce output when successful. An empty stdout with `exit_code=0` means something went wrong at the terminal level (e.g. SSH pipe issue). Treat it as FAILED rather than silently continuing:

```python
if df_result.success and not df_result.stdout.strip():
    return {"status": ActionStatus.FAILED.value, "error": "command returned empty output", ...}
```

This is different from `find /var/log -size +100M` in log_rotation — that command legitimately returns empty (no files found = nothing to do), so the check there is `not result.success` (non-zero exit code) rather than empty output.

---

## Quiz Yourself

1. Why does the `_run_disk_cleanup()` helper need to return a FAILED dict instead of re-raising?
2. What is `os.O_EXCL` and why is it essential for atomic lock creation?
3. What's the difference between the two `except` blocks in the audit retry loop?
4. When is `asyncio.TimeoutError` the same as `TimeoutError`?
5. Why does `find` returning empty stdout indicate "nothing to do" but `df -h` returning empty stdout indicate "something is wrong"?
