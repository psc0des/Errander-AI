# 06 — Batch Orchestrator + LangGraph Fan-Out with Send()

## What Was Built and Why

The batch orchestrator (`errander/agent/graph.py`) is the Level 1 (top-level) graph. It coordinates a fleet-wide maintenance run: validates all targets, fans out to N per-VM graphs in parallel, aggregates results, and generates a report.

This is the "outer shell" of the agent. It doesn't do maintenance itself — it orchestrates who gets maintenance and in what order.

---

## Key Concepts

### 1. LangGraph Send() — Parallel Fan-Out

`Send()` is LangGraph's mechanism for dynamic parallel fan-out. Instead of routing to a fixed next node, a conditional edge function returns a **list of `Send` objects** — each one spawns an independent invocation of the target node with its own state.

```python
from langgraph.types import Send

def route_after_validate(state):
    healthy = state.get("healthy_targets", [])
    if not healthy:
        return "generate_report"  # single string — no fan-out

    return [
        Send("run_vm", VMGraphState(vm_id=t["vm_id"], ...))
        for t in healthy
    ]
```

Each `Send("run_vm", state)` creates an independent `run_vm` invocation with its own state. All `run_vm` instances run in parallel. Their results are merged via the **reducer** on `vm_results`.

**Critical rule**: `Send()` objects must come from **conditional edge functions**, not from nodes. Nodes must return dicts. This caused the one bug during implementation (see Gotchas).

### 2. The Append-Only Reducer

When multiple `run_vm` nodes write to `vm_results` simultaneously, LangGraph uses a **reducer** to merge the writes:

```python
def _merge_vm_results(
    existing: list[dict],
    incoming: list[dict],
) -> list[dict]:
    return [*existing, *incoming]

class BatchGraphState(TypedDict, total=False):
    vm_results: Annotated[list[dict], _merge_vm_results]
```

The `Annotated` type tells LangGraph to call `_merge_vm_results(existing, new)` instead of overwriting. Without this, concurrent writes from multiple `run_vm` nodes would race and lose data.

### 3. Pre-Compiled VM Graph via Closure

The per-VM compiled graph is built once and shared across all fan-out invocations via a closure:

```python
def make_fan_out_router(None, executor, locker, audit_store, ssh_manager):
    vm_compiled = build_vm_graph(executor, locker, audit_store, ssh_manager).compile()

    def route_after_validate(state):
        ...
        return [Send("run_vm", vm_state) for t in healthy]

    return route_after_validate, vm_compiled
```

Then `run_vm` uses the captured `vm_compiled`:

```python
async def _run_vm(state: VMGraphState) -> dict[str, Any]:
    return await run_vm_node(state, vm_compiled=vm_compiled)
```

Building and compiling a `StateGraph` is expensive. You only want to do it once, not once per VM.

### 4. Graph Flow

```
init_batch
    │
validate_window ──► (error) ──► generate_report ──► END
    │
validate_targets ──► (no healthy) ──► generate_report ──► END
    │
    ├─ Send("run_vm", vm1_state) ─┐
    ├─ Send("run_vm", vm2_state) ─┤──► collect_results ──► generate_report ──► END
    └─ Send("run_vm", vmN_state) ─┘
```

`run_vm` nodes run in parallel. After all complete, `collect_results` runs (passthrough). Then `generate_report` produces the final report string.

### 5. Target Validation

`validate_targets_node` does a simple SSH connectivity check (`echo ok`) for each target. This catches:
- Unreachable hosts (SSH connection refused/timeout)
- Wrong credentials (non-zero exit from `echo ok`)

Targets are partitioned into `healthy_targets` and `failed_targets`. Only healthy targets are fanned out to.

---

## Code Walkthrough

### init_batch_node

```python
async def init_batch_node(state):
    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    return {"batch_id": batch_id}
```

Generates a unique 12-char hex batch ID. Everything downstream uses this for audit correlation.

### validate_window_node

Currently a stub (Phase 1.7 will integrate `scheduling/windows.py`). Force override is logged as a warning.

### validate_targets_node

Runs `echo ok` on each target via SSH. Partitions into healthy/failed. Failed targets get an `ACTION_FAILED` audit event logged.

### route_after_validate (inside make_fan_out_router)

The critical routing function. Returns `"generate_report"` (string) when no healthy targets, or `list[Send]` for fan-out. LangGraph handles both return types from conditional edge functions.

### run_vm_node

Receives a `VMGraphState` (the `Send` arg) and invokes the pre-compiled per-VM graph. Returns `{"vm_results": results}` — merged into the batch state via the reducer.

### generate_report_node

Deserialises `vm_results` dicts back to `ActionResult` objects, calls `generate_report()` (template-based, LLM in Phase 1.6), stores as `report` string.

---

## Gotchas

### Bug: Nodes cannot return Send() objects

**Error**: `InvalidUpdateError: Expected dict, got [Send(...)]`

**Cause**: The first implementation had `fan_out` as a regular graph node that returned `list[Send]`. LangGraph nodes **must** return dicts. `Send()` objects can only be returned by **conditional edge routing functions**.

**Fix**: Removed the `fan_out` node entirely. Moved the `Send()` logic into a routing function registered via `add_conditional_edges`. Specifically: `make_fan_out_router()` returns the routing closure, and the batch graph registers it as the conditional edge from `validate_targets`.

```python
# WRONG — node returning Send objects
builder.add_node("fan_out", lambda state: [Send("run_vm", {...})])

# CORRECT — conditional edge function returning Send objects
def _route(state):
    return [Send("run_vm", {...}) for t in state["healthy_targets"]]

builder.add_conditional_edges("validate_targets", _route, ["run_vm", "generate_report"])
```

### State type for run_vm

When `run_vm` is the target of a `Send`, the state it receives is the **`Send` arg**, not the full `BatchGraphState`. So `run_vm` receives a `VMGraphState`, not a `BatchGraphState`. This is intentional — each VM graph instance gets its own isolated state.

---

## Architecture Decisions

- **`make_fan_out_router`** returns both the routing function AND the compiled vm_graph. The caller stores the compiled graph and passes it to `run_vm` via closure. This avoids re-compiling the graph N times.
- **Stubs**: `validate_window` and `generate_report` are Phase 1.7/1.6 stubs respectively. The graph is wired correctly — replacing the stub with real logic requires no graph topology changes.
- **`collect_results`** is a passthrough node (returns `{}`). It exists purely as a sync point: LangGraph waits for all `run_vm` instances to complete before `collect_results` runs. Without it, `generate_report` might see partial results.

---

## Quiz Yourself

1. Why must Send() come from a conditional edge function, not a node?
2. What happens to `vm_results` if two `run_vm` nodes write to it simultaneously without a reducer?
3. Why is the VM graph compiled once rather than once per VM?
4. How does LangGraph know to wait for all `run_vm` instances before running `collect_results`?
5. What's the difference between `healthy_targets` → `fan_out` → `run_vm` vs. `healthy_targets` → `run_vm` (via conditional edge)?
