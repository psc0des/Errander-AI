# 05 — Per-VM Maintenance Graph

## What Was Built and Why

The per-VM graph (`errander/agent/vm_graph.py`) is the Level 2 unit of the Option C architecture. Each VM in the fleet gets its own independent instance of this graph, dispatched via `Send()` from the batch orchestrator. It manages one VM's full maintenance lifecycle: acquire lock → discover state → plan actions → dispatch sub-graphs → audit results → release lock.

**Why isolate per VM?** Failure of one VM must never block others. Each VM has its own lock, its own results, its own error path. This is classic fan-out parallelism — the batch orchestrator fans out N copies, collects results via a reducer.

---

## Key Concepts

### 1. TypedDict State (not Dataclass)

The graph state is a `TypedDict` with `total=False`:

```python
class VMGraphState(TypedDict, total=False):
    vm_id: str
    batch_id: str
    dry_run: bool
    hostname: str
    ssh_user: str
    ssh_key_path: str
    os_family: str
    vm_info: dict[str, object]
    planned_actions: list[dict[str, object]]
    current_action_index: int
    results: list[dict[str, object]]
    locked: bool
    error: str | None
```

`total=False` means all fields are optional — LangGraph merges partial updates from each node into the running state. This is the same pattern as `DiskCleanupGraphState` from Phase 1.3.

**Why not dataclasses?** LangGraph works with TypedDict because it does shallow-merge of dict returns from nodes. Dataclasses require full serialisation.

### 2. Connection Params Flattened into State

The VM's SSH connection details (`hostname`, `ssh_user`, `ssh_key_path`) are flattened into the graph state rather than nesting a `VMTarget` object. This is deliberate: sub-graphs (like disk_cleanup) also need these params, and they receive a slice of the parent state. Flat is easier to inject.

### 3. Action Loop Pattern

The action dispatch loop is implemented as a graph cycle:

```
plan_actions → dispatch_action → check_more_actions
                    ↑                     |
                    |_____________________| (if more actions)
                                          |
                                     audit_results (if done)
```

`dispatch_action` increments `current_action_index` on each call. `check_more_actions` is a routing-only node (returns `{}`) — the logic lives in `route_check_more`:

```python
def route_check_more(state: VMGraphState) -> str:
    index = state.get("current_action_index", 0)
    planned = state.get("planned_actions", [])
    if index < len(planned):
        return "dispatch_action"
    return "audit_results"
```

This loop pattern is one of LangGraph's core idioms — conditional edges create cycles.

### 4. Always-Release Lock Pattern

The lock must be released whether the graph succeeds or fails. This is enforced by the graph topology:

```
acquire_lock  ──► discover ──► plan ──► dispatch ──► audit ──► release_lock
     |                                                              ↑
     └─────────────────── (lock failed) ───────► audit ────────────┘
```

Both the happy path and the error path converge at `audit_results → release_lock → END`. There is no path to END that bypasses `release_lock`.

### 5. Dependency Injection via Closures

Async nodes that need infrastructure (executor, locker, audit_store, ssh_manager) receive them as keyword-only arguments. At graph build time, closures wrap each node:

```python
async def _acquire(state: VMGraphState) -> dict[str, Any]:
    return await acquire_lock_node(state, locker=locker)
```

This pattern was established in Phase 1.3. It keeps node functions testable (injectable dependencies), while the graph builder wires them up.

### 6. Sub-Graph Invocation

When `dispatch_action` routes to `disk_cleanup`, it builds a fresh sub-graph, compiles it, and invokes it with `ainvoke`:

```python
compiled = build_disk_cleanup_subgraph(executor).compile()
final_state = await compiled.ainvoke(sub_state)
```

The sub-graph state (`DiskCleanupGraphState`) is populated from the VM graph state — connection params, os_family, dry_run are all forwarded. The sub-graph's final state is then serialised into an `ActionResult`-like dict and appended to `results`.

---

## Code Walkthrough

### acquire_lock_node

```python
async def acquire_lock_node(state, *, locker):
    acquired = await locker.acquire(state["vm_id"], state.get("batch_id", "unknown"))
    if not acquired:
        return {"locked": False, "error": f"VM {vm_id} is already locked..."}
    return {"locked": True}
```

Simple: tries to acquire, returns the outcome. If it fails, the error path routes to audit → release (which no-ops since `locked=False`).

### discover_node

Calls `detect_os()` which runs 5 SSH commands: `cat /etc/os-release`, `df -h`, `docker info`, package count, `cat /proc/uptime`. Returns a `VMInfo` which is serialised into `vm_info` dict. Also sets `os_family` at the top level (needed by sub-graphs).

### plan_actions_node

Deserialises `vm_info` back to a `VMInfo` object, calls `prioritize_actions()` (hardcoded Phase 1.4, LLM Phase 1.6), serialises resulting `Action` objects to dicts. Sets `current_action_index = 0`.

### dispatch_action_node

Reads `planned_actions[current_action_index]`, dispatches based on `action_type`. Currently only `disk_cleanup` runs a real sub-graph; other action types return `SKIPPED`. Appends to `results`, increments `current_action_index`.

---

## Gotchas

1. **Routing-only nodes**: `check_more_actions` returns `{}` — it exists only so the conditional edge has a named source node. LangGraph conditional edges must be attached to a node, not directly from another conditional edge.

2. **State serialisation**: Sub-graphs use TypedDict; the VM graph stores results as `list[dict]`, not `list[ActionResult]`. Serialise at the boundary, deserialise when needed (e.g., in the report generator).

3. **Lock release no-op**: `release_lock_node` checks `state.get("locked")` before releasing — safe to call even when lock was never acquired.

---

## Quiz Yourself

1. Why must `release_lock` be reachable from every exit path?
2. What happens to the graph if `discover` raises `ConnectionError`?
3. Why is `check_more_actions` a separate node rather than a direct conditional edge from `dispatch_action`?
4. What does `total=False` on a TypedDict mean and why does LangGraph need it?
5. Why are action results stored as `list[dict]` instead of `list[ActionResult]`?
