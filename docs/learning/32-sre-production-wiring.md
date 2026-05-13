# 32 — SRE Production Wiring: Making Signal Stores Active

## What was built and why

The SRE signal stores (`VMDiskHistoryStore`, `BaselineStore`, `VMStateStore`) and the `critical_services` feature were implemented and tested in isolation, but an independent SRE audit found they were never active in production. The stores existed as library code. The graph nodes checked `isinstance(store, X)` and silently returned early when `None` was passed — which was always, because nothing initialized or passed them.

This document explains the full wiring chain and how to add new dependencies to it correctly in future.

## The dependency injection chain

LangGraph's compiled graphs are immutable objects. Dependencies (stores, settings objects) must be captured in closures at build time:

```
async_main()
  → initializes: VMDiskHistoryStore, BaselineStore, VMStateStore
  → calls run_env_batch(..., disk_history_store=..., baseline_store=..., vm_state_store=...)
    → calls build_batch_graph(..., disk_history_store=..., vm_state_store=...)
      → calls make_wave_dispatcher(..., disk_history_store=..., vm_state_store=...)
        → calls build_vm_graph(..., disk_history_store=..., vm_state_store=...)
          → captures in closures: disk_snapshot_node, drift_baseline_node, failed_logins_node
          → calls build_patching_subgraph(executor, audit_store=..., vm_state_store=...)
            → captures in closures: reboot_check_node, service_health_post_node
```

Every layer must explicitly forward the dependency. Missing one layer = silent no-op at runtime, no error.

## Why silent no-ops are dangerous

The nodes are written defensively:

```python
async def disk_snapshot_node(state, *, executor, disk_history_store, ...):
    if not isinstance(disk_history_store, VMDiskHistoryStore):
        return {"disk_growth_alerts": []}   # silent early return
```

This is good defensive design for optional features. But it means tests that inject the store directly always pass, while production runs that never pass the store silently skip the entire feature. The failure mode is "works in tests, does nothing in prod" — the hardest kind to notice.

**Rule**: every `isinstance` guard on an optional store is a correctness invariant that must be covered by a wiring test, not just a unit test on the node itself.

## The `batch_id` in `PatchingGraphState` fix

`build_patching_subgraph` is compiled **once at agent startup**, not once per batch. The original code tried to capture `batch_id` in the closure at build time:

```python
def build_patching_subgraph(executor, *, batch_id="", ...):
    async def _reboot_check(state):
        return await reboot_check_node(state, ..., batch_id=batch_id)  # always ""
```

This meant every `REBOOT_REQUIRED_DETECTED` audit event had `batch_id=""`.

The fix: add `batch_id: str` to `PatchingGraphState` and read it from state at runtime:

```python
effective_batch_id = state.get("batch_id") or batch_id or ""
```

`_run_patching` in `vm_graph.py` passes `batch_id` from `VMGraphState` into the patching sub_state:

```python
sub_state: PatchingGraphState = {
    ...
    "batch_id": state.get("batch_id", ""),
    "critical_services": list(state.get("critical_services") or []),
}
```

**General rule**: when a subgraph is compiled once and reused across many invocations (the LangGraph pattern), per-run context (batch_id, vm_id, dry_run) must flow through state, not through build-time closures.

## `critical_services` flow

`critical_services` must travel from YAML inventory through five layers:

1. `TargetSchema.critical_services: list[str]` — parsed from YAML
2. `yaml_targets` dict in `run_env_batch` — `"critical_services": list(t.critical_services)`
3. `VMGraphState.critical_services: list[str]` — TypedDict field
4. `Send(VMGraphState(..., critical_services=list(t.get("critical_services") or [])))` — both `Send()` paths in `graph.py`
5. `PatchingGraphState.critical_services` — passed in `_run_patching` sub_state

Missing any layer means the service health regression check runs on an empty services list and produces no output. No error raised.

## How to wire a new dependency correctly

1. Add the store/settings field to `build_vm_graph` signature (typed `object` for TC001 compatibility).
2. Capture it in the relevant node closure inside `build_vm_graph`.
3. Add it to `make_wave_dispatcher` and forward to `build_vm_graph`.
4. Add it to `build_batch_graph` and forward to `make_wave_dispatcher`.
5. Initialize in `async_main`, pass to `run_env_batch`, pass to `build_batch_graph`.
6. Add to `_window_opener` and its `run_env_batch` call (deferred execution path).
7. Add to both scheduler closures (`_run`, `_open_window`) in the APScheduler loop.
8. Add a wiring test in `tests/agent/test_sre_wiring.py` that patches the leaf function and asserts the dependency arrives.

## Grep the right files

When adding a new dependency:

```bash
# Find all VMGraphState constructor calls — both Send() paths must be updated
grep -n "VMGraphState(" errander/agent/graph.py

# Find all run_env_batch call sites
grep -n "run_env_batch(" errander/main.py

# Find all build_batch_graph call sites
grep -n "build_batch_graph(" errander/main.py errander/agent/graph.py
```

## The `authentication failure` grep fix

`failed_logins_command` grepped for `'authentication failure'` but `_FAIL_RE` couldn't parse PAM-format lines (they have no `user X from Y` structure). The lines were fetched but discarded silently — the feature under-reported.

**Fix**: remove patterns from the grep that the parser can't consume. Honest absence (fewer lines fetched) is better than silent under-count (lines fetched but not counted).

## Gotchas

- **`db_additions` also need the field**: targets added via the web UI are built from `OverridesStore` rows, not `TargetSchema`. When adding a new per-VM field, also add a default (`[]`, `False`, etc.) to the `db_additions` dict literal.
- **Both `Send()` paths**: `make_fan_out_router` (legacy, used in unit tests) and `dispatch_current_wave` (production) both emit `Send(VMGraphState(...))`. Both must include every `VMGraphState` field you add.
- **`SRESignalSettings` in test mocks**: tests that use `MagicMock(spec=Settings)` will fail with `AttributeError` when `run_env_batch` accesses `settings.sre_signals`. Add `settings.sre_signals = SRESignalSettings()` to mock setup.

## Quiz

1. Why does a node's `isinstance(store, X)` guard not catch a wiring bug in CI?
2. If you add a new SRE store, which files must you update?
3. Why is `batch_id` in `PatchingGraphState` state (not in the `build_patching_subgraph` closure)?
4. What happens if you forget to add a new per-VM field to `db_additions`?
5. Name the two `Send()` call sites that both need updating when a new `VMGraphState` field is added.
