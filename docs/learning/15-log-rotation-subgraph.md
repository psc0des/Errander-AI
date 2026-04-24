# 15 — Log Rotation Sub-Graph (Phase 2)

## What Was Built

The log rotation sub-graph — finds oversized log files under `/var/log`, rotates them via `logrotate --force` or manual `gzip + truncate`, and verifies sizes shrunk. Follows the same 4-node pattern as disk cleanup: validate → assess → execute → verify.

## Why This Pattern

Log rotation is low risk (data is compressed, not deleted) and fits the standard sub-graph lifecycle perfectly. It was the first Phase 2 sub-graph implemented because it's the simplest — no rollback, no OS-specific commands, no special safety gates beyond path validation.

## Key Concepts

### 1. Path Validation as a Safety Gate

Like disk cleanup's whitelist, log rotation restricts which directories can be scanned:

```python
ALLOWED_LOG_PREFIXES: tuple[str, ...] = (
    "/var/log",
)

def is_valid_log_path(path: str) -> bool:
    normalised = path.rstrip("/")
    return any(
        normalised == prefix or normalised.startswith(prefix + "/")
        for prefix in ALLOWED_LOG_PREFIXES
    )
```

This is a prefix check rather than an exact match — `/var/log/nginx` passes, `/var/log` passes, but `/home/user/logs` is blocked. The validate node rejects any invalid paths immediately and short-circuits to END.

**Key difference from disk cleanup**: disk cleanup uses a frozenset of named categories (`"apt-cache"`, `"journal"`). Log rotation uses prefix matching on actual filesystem paths. Both are hardcoded, never LLM-decided.

### 2. Idempotency via Pre-Check Skipping

The assess node finds files exceeding `size_threshold_mb` using `find`:

```python
cmd = (
    f"find {log_dir} -type f -size +{threshold_mb}M "
    f"-exec ls -lh {{}} \\; 2>/dev/null"
)
```

If no large files are found, the node sets `nothing_to_do=True`, and the routing function skips execute entirely:

```python
def route_after_assess(state: LogRotationGraphState) -> str:
    if state.get("nothing_to_do"):
        return END      # skip execute + verify — nothing to do
    return "execute"
```

This is the idempotency pattern used across all Phase 2 sub-graphs: assess sets a flag, routing skips execution. The agent never makes unnecessary changes.

### 3. Fallback Strategy in Execute

The execute node tries system `logrotate` first, then falls back to manual rotation:

```python
# Try logrotate first
result = await executor.execute(
    ..., command="logrotate --force /etc/logrotate.conf 2>&1",
         simulate_command="logrotate --debug /etc/logrotate.conf 2>&1 | head -20",
)

if result.success:
    output["logrotate"] = result.stdout.strip()
else:
    # Manual rotation per file
    for filepath in large_files:
        live_cmd = (
            f"cp {filepath} {filepath}.1 && "
            f"gzip {filepath}.1 && truncate -s 0 {filepath}"
        )
```

Why `cp + gzip + truncate` instead of `mv + gzip`? Truncating preserves the original inode — processes that have the file open (like syslog) continue writing to the same file descriptor. Moving would leave them writing to a deleted file.

### 4. Verify Node Pattern

The verify node re-runs the same `find` command as assess to check if large files remain:

```python
cmd = f"find {log_dir} -type f -size +{threshold_mb}M -ls 2>/dev/null | wc -l"
```

If any files are still above threshold, it sets an error but doesn't fail the sub-graph — rotation may have partially succeeded.

## Graph Flow

```
START → validate → assess → execute → verify → END
           │                    │
           │ (invalid paths)    │ (dry-run)
           └──→ END             └──→ END
                    │
                    │ (nothing to do)
           assess ──→ END
```

## Gotchas

1. **`find -exec` escaping**: The `{}` in the find command needs escaping in the f-string: `{{}}`. Python sees `{{` as a literal `{`, so the shell receives `find ... -exec ls -lh {} \;`.

2. **`ls -lh` parsing**: The assess node parses `ls -lh` output to extract file sizes. Column positions vary by OS — the code takes `parts[4]` for size and `parts[-1]` for filepath. This works on Linux but could break on exotic configurations.

3. **Dry-run uses `simulate_command`**: Logrotate's simulate is `--debug` mode, which prints what it would do. Manual rotation's simulate is just `ls -lh` (show the file, don't touch it).

4. **SandboxExecutor mock level**: Tests must mock `executor.execute`, not `executor._ssh.execute`. In dry-run mode without `simulate_command`, the executor generates synthetic output without calling SSH at all.

## Quiz Yourself

1. Why does log rotation use prefix matching (`startswith`) while disk cleanup uses category matching (`frozenset`)?
2. What happens if `logrotate --force` succeeds but one large file is still above threshold? Is the sub-graph status SUCCESS or FAILED?
3. Why `cp + truncate` instead of `mv` for manual rotation?
4. How does the `nothing_to_do` flag flow from the assess node through routing to skip execution?
5. What's the difference between `logrotate --force` and `logrotate --debug`?
