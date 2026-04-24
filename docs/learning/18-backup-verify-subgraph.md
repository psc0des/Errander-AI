# 18 — Backup Verify Sub-Graph (Phase 2)

## What Was Built

The backup verification sub-graph — a read-only action that checks whether backups exist, are recent, and are non-zero size. This is the only sub-graph with **no execute node**. It has 3 nodes: validate → assess → verify → END.

## Why It's Unique

Backup verify breaks the pattern established by the other four sub-graphs in several ways:

1. **No execute node** — verification is read-only, no state changes
2. **High risk tier** — despite being read-only, it requires human approval (because if backups are broken, someone needs to know immediately)
3. **Uses `NEEDS_MANUAL` status** — not just SUCCESS/FAILED, but "issues found, human must investigate"
4. **No idempotency flag** — it's inherently idempotent because it's read-only

## Key Concepts

### 1. Three-Node Graph (Unique Pattern)

Every other sub-graph has 4 nodes (validate → assess → execute → verify). Backup verify has 3:

```python
builder.add_node("validate", validate_node)
builder.add_node("assess", _assess)
builder.add_node("verify", verify_node)

builder.set_entry_point("validate")
builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
builder.add_edge("assess", "verify")    # unconditional — always verify after assess
builder.add_edge("verify", END)
```

There's no conditional edge between assess and verify — if we collected metadata, we always check it. The only conditional edge is after validate (skip if no paths configured).

### 2. Validate Checks Configuration, Not Input

Unlike disk cleanup (checks whitelist) or patching (checks kernel exclusion), backup verify checks whether paths are configured at all:

```python
def validate_node(state: BackupVerifyGraphState) -> dict[str, Any]:
    paths = state.get("backup_paths", [])
    if not paths:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "No backup paths configured",
        }
    return {"status": ActionStatus.PENDING.value}
```

If no backup paths are provided, there's nothing to verify — it's not an error, just a skip.

### 3. Metadata Collection via `stat`

The assess node uses `stat -c '%s %Y %n'` to get file metadata in a single command:

```python
cmd = f"stat -c '%s %Y %n' {path} 2>/dev/null || echo 'MISSING {path}'"
```

- `%s` = file size in bytes
- `%Y` = last modification time as epoch timestamp
- `%n` = file name

The `|| echo 'MISSING {path}'` fallback ensures we get structured output even for missing files. The output is parsed into a list of metadata dicts:

```python
metadata.append({
    "path": parts[2],
    "size": parts[0],           # bytes as string
    "last_modified": parts[1],  # epoch as string
    "exists": "true",
})
```

### 4. Three Issue Types

The verify node checks three conditions and flags issues:

```python
# 1. MISSING — file doesn't exist at all
if not exists:
    issues.append(f"MISSING: {path}")

# 2. EMPTY — file exists but has zero bytes
if size == 0:
    issues.append(f"EMPTY: {path} (zero bytes)")

# 3. STALE — file is older than max_age_hours
age_seconds = now - mtime
if age_seconds > max_age_seconds:
    issues.append(f"STALE: {path} (last modified {age_hours:.1f}h ago, threshold {max_age_hours}h)")
```

The order matters — missing is checked first (can't check size/age on a missing file), then empty, then stale.

### 5. `NEEDS_MANUAL` Status

Backup verify introduces a status that no other sub-graph uses:

```python
status = ActionStatus.SUCCESS if not issues else ActionStatus.NEEDS_MANUAL
```

This isn't a failure — the sub-graph ran correctly. But the results require human attention. The Slack notification and report will highlight this differently from a SUCCESS or FAILED status.

### 6. Backup Paths Come from Action Params

In the VM graph dispatch, backup paths are extracted from the action's params (configured in inventory):

```python
async def _run_backup_verify(state: VMGraphState, compiled: Any) -> dict[str, object]:
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    backup_paths: list[str] = []
    if index < len(planned):
        params = planned[index].get("params", {})
        backup_paths = list(params.get("backup_paths", []))
```

This is different from other sub-graphs where params are either hardcoded (disk cleanup whitelist) or derived from discovery (docker availability). Backup paths are infrastructure configuration — they vary per VM and are defined in the inventory.

## Graph Flow

```
START → validate → assess → verify → END
           │
           │ (no paths configured)
           └──→ END
```

No conditional edges after assess — if paths exist, we always verify them.

## Connecting to the Bigger Picture

Backup verify is read-only but high risk. Why?

The risk tier reflects the **impact of NOT acting on the result**, not the risk of the action itself. If backups are missing or stale, the operator needs to know immediately — a failed backup could mean data loss during the next incident. The human approval gate ensures someone sees the plan before the check runs, and the `NEEDS_MANUAL` status ensures someone reviews the results.

This is the "brain in a jar" pattern at work: the agent discovers the problem, the LLM explains it in context, but a human decides what to do about it.

## Gotchas

1. **`stat -c` is Linux-only**: BSD/macOS uses `stat -f`. Since Errander-AI targets Linux VMs only, this is fine, but it won't work for local macOS testing without mocks.

2. **Epoch comparison for staleness**: `time.time()` returns the current time on the **agent VM**, not the target VM. If clocks are skewed between agent and target, staleness checks could be wrong. In practice, VMs in the same VPN use NTP, so clock skew is minimal.

3. **String storage for sizes**: Metadata stores sizes as strings (`"size": parts[0]`), then the verify node converts with `int()`. This is because the metadata dict uses `dict[str, str]` typing — a deliberate simplification over mixed-type dicts.

4. **No `nothing_to_do` flag**: Unlike other sub-graphs, backup verify doesn't set a `nothing_to_do` flag. The TypedDict doesn't even include it. The sub-graph always runs if paths are configured — it's always useful to verify backups.

## Quiz Yourself

1. Why doesn't backup verify have an execute node?
2. What's the difference between `ActionStatus.FAILED` and `ActionStatus.NEEDS_MANUAL`?
3. Why is backup verify classified as High risk when it's read-only?
4. What would happen if a backup file exists but `stat` fails with a permission error?
5. Why does the verify node check conditions in the order MISSING → EMPTY → STALE?
6. How do backup paths get from the inventory configuration to the sub-graph state?
