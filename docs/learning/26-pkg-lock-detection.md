---
title: "26 — Package Manager Lock Detection (PR-1.1)"
---

# Package Manager Lock Detection

## What was built and why

The most common cause of silent patching failure on Linux VMs is `dpkg`/`apt` lock contention — another process holds the lock while the agent tries to run `apt-get upgrade`, which either blocks indefinitely or fails with an opaque error. PR-1.1 adds a pre-flight gate that detects this condition before any upgrade is attempted, surfaces the holder's PID and command name, emits a structured audit event, and sets `ActionStatus.BLOCKED` so the operator knows exactly why patching was skipped rather than seeing a generic failure.

## Key concepts

### detect_lock() — command generation, not execution

`PackageManager.detect_lock()` returns a shell command string. It follows the same pattern as every other method on `PackageManager` — generate a command, let the SSH execution layer run it. This keeps the package manager abstraction pure and testable without SSH.

```python
# AptManager: check dpkg/apt lock files via fuser
"for lock in /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/lib/dpkg/lock; do ..."

# DnfManager: check DNF/YUM pid files
"for pidfile in /var/run/dnf.pid /var/run/yum.pid; do ..."
```

Both produce the same output format on stdout: `pid=<N> cmd=<name>` when locked, empty when clear. Both always exit 0 — the caller's SSH success check is never triggered by the probe itself.

**Why fuser for APT?** Lock files don't have a PID file — `fuser` asks the kernel which process has the file open. On minimal images where fuser (psmisc) isn't installed, the command exits 0 with no output → treated as no lock.

**Why pid files for DNF?** DNF writes `/var/run/dnf.pid` while running. `kill -0 $pid` verifies the process is still alive (the file may be stale after a crash). No fuser dependency needed.

### parse_lock_output() — pure parsing

```python
def parse_lock_output(output: str) -> LockHolder | None:
    stripped = output.strip()
    if not stripped:
        return None           # empty = no lock
    ...
    return LockHolder(pid=pid, cmd=cmd)
```

Returns `None` for empty output (no lock or tool unavailable) and a `LockHolder` for any non-empty output. Parsing is token-based (`pid=N cmd=X`) — robust to extra fields the shell adds.

**Rule**: a missing tool is the same as a clear lock. Never block patching because `fuser` isn't installed.

### validate_no_pkg_lock() — SSH probe, best-effort

```python
async def validate_no_pkg_lock(executor, vm_id, hostname, username, key_path, pm) -> tuple[bool, LockHolder | None]:
    result = await executor.execute(..., command=pm.detect_lock(), dry_run=False)
    if not result.success:
        logger.warning("Lock probe failed ... (treating as clear)")
        return True, None     # best-effort: don't block patching on a probe error
    return parse_lock_output(result.stdout) is None, parse_lock_output(result.stdout)
```

`dry_run=False` is intentional — the probe reads real VM state regardless of whether the patching run is a dry-run. You always want to know if the lock is held.

SSH failure (timeout, key error) is treated as clear. This is a deliberate trade-off: a probe error is much rarer than a genuine lock, and blocking the entire patching run on a probe failure would cause more noise than it prevents.

### preflight_lock_node — first node in the patching subgraph

The node runs before the existing kernel-exclusion `validate_node`. If blocked:

```
preflight_lock_node (BLOCKED) → END
```

If clear:

```
preflight_lock_node → validate → assess → snapshot → execute → verify
```

The `build_patching_subgraph` builder conditionally wires the node based on `sre_preflight_lock_check`:

```python
if sre_preflight_lock_check:
    builder.add_node("preflight_lock", _preflight_lock)
    builder.set_entry_point("preflight_lock")
    builder.add_conditional_edges("preflight_lock", route_after_preflight_lock, ["validate", END])
else:
    builder.set_entry_point("validate")  # original behaviour
```

This compile-time conditional means existing tests can pass `sre_preflight_lock_check=False` and test the rest of the graph without mocking the lock probe SSH call.

### Audit events

When `audit_store` is provided:
- Lock held → `PREFLIGHT_LOCK_DETECTED` with `metadata={"holder_pid": ..., "holder_cmd": ...}`
- Lock clear → `PREFLIGHT_LOCK_CLEAR`

When `audit_store` is None (tests, legacy callers): no events emitted, no error raised.

## Gotchas encountered

- **SIM105**: ruff wants `with contextlib.suppress(ValueError)` instead of `try/except ValueError: pass` in `parse_lock_output`. It's a clean idiom once you know it.
- **TC001**: `PackageManager` and `SandboxExecutor` are only used as type annotations in `validators.py`. With `from __future__ import annotations`, they can live in `TYPE_CHECKING` — ruff enforces this.
- **Existing integration tests broke**: adding `preflight_lock_node` as the entry point adds one SSH call before every integration test that runs the full subgraph. Fix: pass `sre_preflight_lock_check=False` in tests that are testing something other than the lock check.

## Quiz yourself

1. Why does `detect_lock()` always exit 0? What would happen if it exited non-zero when fuser is missing?
2. Why is `dry_run=False` used for the lock probe even in dry-run patching runs?
3. If `audit_store=None`, what happens when the lock is detected?
4. How does the graph structure differ when `sre_preflight_lock_check=False`?
5. What does `kill -0 $pid` do, and why does DnfManager use it?
