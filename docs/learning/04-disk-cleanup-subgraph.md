# 04 — Disk Cleanup Sub-Graph (Phase 1.3)

## What Was Built

The first complete LangGraph sub-graph: disk cleanup. This is the lowest-risk action type — it only removes files from a hardcoded whitelist of safe paths. It demonstrates the full action lifecycle: validate → assess → execute → verify.

## Why Disk Cleanup First

The spec says to start with the lowest-risk action. Disk cleanup targets only ephemeral paths (`/tmp`, package cache, journal logs, orphaned deps). There's no rollback needed — the data is disposable by definition. This makes it the safest action to build and test the sub-graph pattern with.

## Key Concepts

### 1. LangGraph Sub-Graph Structure

A sub-graph is a `StateGraph` with typed state, node functions, and edges:

```python
builder = StateGraph(DiskCleanupGraphState)
builder.add_node("validate", validate_node)
builder.add_node("assess", _assess)
builder.set_entry_point("validate")
builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
compiled = builder.compile()
result = await compiled.ainvoke(initial_state)
```

The state flows through nodes sequentially. Each node returns a partial dict update — LangGraph merges it into the full state automatically.

### 2. TypedDict vs Dataclass for State

LangGraph works with both, but TypedDict is more natural:

```python
class DiskCleanupGraphState(TypedDict, total=False):
    vm_id: str
    os_family: str
    status: str
    space_by_path: dict[str, str]
```

`total=False` makes all keys optional — nodes only return the keys they update. With dataclasses, every field needs a default value, which is more verbose.

### 3. Dependency Injection via Wrappers

LangGraph node functions receive only `(state)` or `(state, config)`. To inject the `SandboxExecutor`, we wrap the node:

```python
async def _assess(state: DiskCleanupGraphState) -> dict[str, Any]:
    return await assess_node(state, executor=executor)

builder.add_node("assess", _assess)
```

**Gotcha**: You CANNOT use `lambda state: assess_node(state, executor=executor)` for async functions. Lambda returns a coroutine object, not an awaited result. LangGraph sees a coroutine object and raises `InvalidUpdateError: Expected dict, got <coroutine>`. You must use `async def`.

### 4. Whitelist Enforcement (Security Critical)

The whitelist is a frozen set in Python code — never loaded from config, never decided by the LLM:

```python
ALLOWED_CLEANUP_PATHS: frozenset[str] = frozenset({
    "/tmp", "apt-cache", "yum-cache", "journal", "orphaned-deps",
})
```

The validate node checks every requested path against this set BEFORE any SSH commands run. If any path is not whitelisted, the sub-graph returns `FAILED` immediately. This is a hardcoded safety gate.

### 5. Dry-Run via SandboxExecutor

Each cleanup command has a real command and a simulate command:

```python
result = await executor.execute(
    vm_id, hostname, username, key_path,
    command="apt-get clean",                    # live mode
    simulate_command="du -sh /var/cache/apt",   # dry-run mode
)
```

The SandboxExecutor handles the routing:
- `dry_run=True` + `simulate_command` → executes simulate via SSH
- `dry_run=True` + no simulate → synthetic `[DRY-RUN] Would execute: ...`
- `dry_run=False` → executes the real command

### 6. Conditional Routing

LangGraph conditional edges route based on state:

```python
def route_after_execute(state):
    if state.get("status") == "dry_run_ok":
        return END       # dry-run complete, skip verification
    return "verify"      # live mode, check disk usage

builder.add_conditional_edges("execute", route_after_execute, ["verify", END])
```

The third argument (`["verify", END]`) tells LangGraph which nodes are possible targets — required for graph validation.

### 7. OS-Aware Command Generation

The `PackageManager` strategy pattern generates OS-specific commands:

```python
# AptManager (Ubuntu/Debian)
def clean_cache(self) -> str:
    return "apt-get clean"

def autoremove(self) -> str:
    return "apt-get autoremove -y"

# DnfManager (RHEL)
def clean_cache(self) -> str:
    return "dnf clean all"

def autoremove(self) -> str:
    return "dnf autoremove -y"
```

The sub-graph selects the right manager based on `os_family` from state.

## Graph Flow

```
START → validate → assess → execute → verify → END
           │                    │
           │ (blocked path)     │ (dry-run)
           └──→ END             └──→ END
```

## Gotchas

1. **Lambda + async = broken**: LangGraph lambdas wrapping async functions don't await them. Use `async def` wrappers instead.

2. **Mocking at the right level**: When testing assess/execute nodes with `SandboxExecutor` in dry-run mode, mock at `executor.execute` level, not `executor._ssh.execute`. The SandboxExecutor intercepts and modifies results in dry-run mode — mocking the SSH layer bypasses this logic.

3. **`total=False` on TypedDict**: Without this, LangGraph expects ALL keys in every state update. Nodes that only return `{"status": "failed"}` would error. `total=False` makes partial updates work.

4. **Connection params in state**: The sub-graph needs SSH connection info (hostname, username, key_path) but these aren't part of `DiskCleanupGraphState`. They're injected as extra keys — TypedDict allows this with `# type: ignore[typeddict-item]`. In production, the per-VM graph will inject these.

## Testing Patterns

- **Node-level tests**: Test each node function independently with mock executors
- **Routing tests**: Test conditional edge functions with crafted state dicts
- **Integration tests**: `compiled.ainvoke(state)` runs the full graph flow with mocks
- **Whitelist tests**: Ensure the security-critical path check works for known-good and known-bad paths

## Quiz Yourself

1. Why is the whitelist a `frozenset` in Python code instead of loaded from YAML config?
2. What happens if you wrap an async node function with a lambda instead of `async def`?
3. Why does the verify step skip in dry-run mode?
4. How does the sub-graph handle a path like `/home/user/data` that isn't whitelisted?
5. What's the difference between the `command` and `simulate_command` parameters to `executor.execute`?
