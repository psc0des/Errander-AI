# 03 — Core Infrastructure (Phase 1.2)

## What Was Built

Five core infrastructure pieces that all other features depend on:

1. **Settings loader** — env vars + YAML config loading
2. **Audit logging** — async SQLite event store
3. **SSH execution** — connection pooling + retry + timeout
4. **Sandbox/dry-run** — command interception + simulation
5. **File-based VM locking** — TTL-based mutual exclusion

## Key Patterns

### 1. Async Context Managers Everywhere

Both `AuditStore` and `SSHConnectionManager` use `async with` for lifecycle:

```python
class AuditStore:
    async def __aenter__(self) -> AuditStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
```

This guarantees cleanup (DB close, SSH disconnect) even on exceptions. The pattern is: `__aenter__` opens resources, `__aexit__` closes them. Always prefer this over manual `try/finally`.

### 2. Connection Pooling with Lazy Init

SSH connections are expensive. `SSHConnectionManager` opens them lazily (on first command to a VM) and reuses them:

```python
async def get_connection(self, vm_id, hostname, username, key_path):
    existing = self._connections.get(vm_id)
    if existing is not None:
        return existing
    return await self._connect_with_retry(vm_id, hostname, username, key_path)
```

The key insight: `vm_id` is the pool key, not `hostname`. This lets multiple VMs on the same host have independent connections.

### 3. Retry with Exponential Backoff

SSH connections can fail transiently. The retry logic:
- 3 attempts by default
- Backoff: [5s, 15s, 45s]
- Only retries on `OSError` and `asyncssh.Error` (network issues)
- Does NOT retry on auth failures (those are permanent)

```python
for attempt in range(self._reconnect_attempts):
    try:
        conn = await self._connect(hostname, username, key_path)
        return conn
    except (OSError, asyncssh.Error) as e:
        if attempt < self._reconnect_attempts - 1:
            await asyncio.sleep(backoff)
```

### 4. Graceful Degradation in OS Detection

`detect_os` makes 5 SSH calls. Only the first (`/etc/os-release`) is required. The rest (df, docker, pkg count, uptime) degrade gracefully:

```python
df_result = await ssh_manager.execute(...)
disk_usage = parse_disk_usage(df_result.stdout) if df_result.success else {}
```

If `df` fails, we get empty `disk_usage` — not a crash. This matters because some target VMs may have restricted permissions.

### 5. Sandbox Pattern (Strategy)

`SandboxExecutor` wraps `SSHConnectionManager` and intercepts based on `dry_run`:

```
dry_run=True + simulate_command   → execute simulate_command via SSH
dry_run=True + no simulate_command → return synthetic "[DRY-RUN]" result
dry_run=False                     → execute real command via SSH
```

The caller provides the simulate_command (e.g., `apt-get --simulate upgrade`) because only the action-specific code knows the right simulation strategy.

### 6. File Locking with TTL

Lock files are JSON with metadata. TTL prevents deadlocks from crashed agents:

```json
{"vm_id": "dev/web-01", "batch_id": "batch-001",
 "acquired_at": "2026-03-21T14:30:00+00:00", "ttl_seconds": 7200}
```

Every operation that reads a lock checks TTL. Expired locks are auto-deleted. Corrupt JSON files are also auto-deleted (self-healing).

## Testing Patterns

### In-Memory SQLite for Audit Tests
```python
async with AuditStore(":memory:") as store:
    await store.log_event(event)
```
No temp files, no cleanup, fast execution.

### Fake SSH Connections
```python
class FakeSSHConnection:
    async def run(self, command, check=True):
        return FakeSSHProcess(exit_status=0, stdout=f"output of: {command}")
```
Mock the `_connect` method, not the entire `asyncssh` module. This tests the real retry and pooling logic while faking the network layer.

### `monkeypatch` for Env Vars
```python
monkeypatch.setenv("AUTOMAINT_SLACK_BOT_TOKEN", "xoxb-test")
settings = load_settings()
assert settings.slack_bot_token == "xoxb-test"
```
`monkeypatch` auto-restores the original environment after each test.

## Gotchas

1. **asyncssh.connect is a coroutine** — must mock with `AsyncMock`, not `MagicMock`. Using `MagicMock` gives `TypeError: object MagicMock can't be used in 'await' expression`.

2. **`asyncio.wait_for` wraps TimeoutError** — when a command times out, `asyncio.wait_for` raises `asyncio.TimeoutError` (not `TimeoutError`). We re-raise as `TimeoutError` with a descriptive message.

3. **`datetime.fromisoformat` and timezones** — SQLite stores timestamps as ISO strings. When comparing datetimes, both must be timezone-aware or both naive. We standardize on UTC everywhere.

4. **Frozen dataclass with `field(default_factory=...)`** — `SSHResult` is frozen but uses `field(default_factory=...)` for mutable defaults. This is fine because the factory creates a new value at init time, not a shared mutable default.

## Quiz Yourself

1. Why does the SSH connection pool key on `vm_id` instead of `hostname`?
2. What happens if an SSH command fails mid-execution (not connection failure)?
3. Why does `SandboxExecutor` take `simulate_command` as a parameter instead of computing it?
4. How does `FileLocker` handle a lock file that contains invalid JSON?
5. What's the difference between `force_release` and `release` on FileLocker?
