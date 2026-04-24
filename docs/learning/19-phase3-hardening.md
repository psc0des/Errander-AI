# 19 — Phase 3 Hardening: Rolling Updates, Canary Logic, Drift Detection

## What Was Built and Why

Phase 2 left the agent doing a full fan-out: all healthy VMs processed simultaneously. That's fine for small fleets, but dangerous at scale — if a bad patch breaks VMs, it breaks *all* of them at once.

Phase 3 adds three opt-in safety mechanisms:

1. **Rolling updates** — process VMs in configurable waves (e.g., 25% at a time)
2. **Canary logic** — run on exactly 1 VM first, health-check it, then proceed to the fleet
3. **Drift detection** — compare each VM's current state against a stored baseline before executing

All three are **off by default** — `rolling_update_percentage=100`, `canary_enabled=False`, `drift_detection_enabled=False` — so existing behavior is completely preserved.

---

## Rolling Updates

### The Problem

Full fan-out means a bad change hits 100% of the fleet simultaneously. With 20 VMs, you have zero recovery window.

### The Solution: Wave-Based Dispatch

Instead of one fan-out, the batch graph now loops through waves:

```
validate_targets → prepare_waves → dispatch_wave → run_vm → check_wave_health
                                       ↑                           |
                                       └─────── (next wave) ───────┘
                                                                    |
                                                              collect_results
```

The key function is `_partition_into_waves()`:

```python
def _partition_into_waves(
    targets: list[dict[str, object]],
    percentage: int,
) -> list[list[dict[str, object]]]:
    if percentage >= 100 or len(targets) == 0:
        return [targets] if targets else []

    wave_size = max(1, math.ceil(len(targets) * percentage / 100))
    return [targets[i:i + wave_size] for i in range(0, len(targets), wave_size)]
```

Examples:
- 8 VMs, 25% → wave_size=2 → 4 waves of 2
- 3 VMs, 50% → `ceil(1.5)=2` → wave(2) + wave(1) — always rounds **up**, never drops VMs
- Any count, 100% → single wave (backward-compatible default)

### The dispatch_wave No-Op Pattern

In LangGraph, conditional edges must be attached to a *node* (not directly to another edge). The `dispatch_wave` node is a pure pass-through:

```python
builder.add_node("dispatch_wave", lambda state: {})   # no-op
builder.add_conditional_edges(
    "dispatch_wave", _dispatch_wave_fn, ["run_vm", "check_wave_health"],
)
```

The routing function `_dispatch_wave_fn` is where all the logic lives — it either emits `list[Send]` (one per target in the current wave) or falls through to `"check_wave_health"` if the wave is empty. This is the same pattern used by `check_more_actions` in the VM graph.

### Wave Health Checks

After each wave, `check_wave_health_node` SSH-executes a configurable command on every VM in the wave:

```python
health_check_command: str = "echo ok"   # default — always passes
wave_failure_threshold: float = 0.5    # abort if >50% fail
```

If `failure_rate > threshold`, `wave_aborted=True` and remaining waves are skipped. The routing function `route_after_wave_health` handles the loop:

```python
def route_after_wave_health(state: BatchGraphState) -> str:
    if state.get("wave_aborted"):
        return "collect_results"

    current_wave = state.get("current_wave", 0)
    total_waves = state.get("total_waves", 0)

    if current_wave < total_waves:
        return "dispatch_wave"    # loop back
    return "collect_results"      # done
```

---

## Canary Logic

### The Problem

Even a 25% wave is 25% of the fleet. For high-confidence validation, you want exactly 1 VM to succeed before touching anyone else.

### The Solution: Canary = Wave 0 with 1 VM

Canary is not a separate mechanism. It's just a constraint applied during wave preparation:

```python
if canary_enabled and len(healthy) > 1:
    canary_target = healthy[0]
    remaining = healthy[1:]
    remaining_waves = _partition_into_waves(remaining, percentage)
    waves = [[canary_target]] + remaining_waves
```

Wave 0 always has exactly 1 VM when canary is enabled. The rest of the fleet is partitioned normally.

### Stricter Health Check for Wave 0

The canary VM uses a different (stricter) health check command:

```python
canary_health_check_command: str = "systemctl is-system-running"
```

`systemctl is-system-running` returns non-zero if the system is in a degraded state — much stricter than `echo ok`. And unlike other waves where `failure_rate > threshold` triggers abort, the canary uses a zero-tolerance rule: **any** failure aborts:

```python
if is_canary_wave:
    if health_failures > 0:
        return {"wave_aborted": True, "canary_passed": False, "current_wave": current_wave + 1}
    return {"canary_passed": True, "current_wave": current_wave + 1}
```

---

## Drift Detection

### The Problem

A VM might have changed since the last maintenance run — OS upgraded by someone else, disk filled up, Docker removed, VM rebooted. Running the same maintenance plan on a drifted VM could cause unexpected results.

### The Solution: Baseline + Compare

Before executing any actions, the agent compares the just-discovered VM state against a stored baseline. The comparison checks 5 things:

| Drift Type | Threshold |
|---|---|
| OS version changed | Any change |
| Disk usage delta | > 20 percentage points |
| Docker availability changed | Any change |
| Uptime reset (rebooted) | `current < baseline` |
| Pending packages delta | > 5 packages |

### Where Baselines Live

Baselines are stored as `DRIFT_BASELINE_SAVED` events in the *existing* SQLite audit trail:

```python
await audit_store.log_event(
    AuditEvent(
        event_type=EventType.DRIFT_BASELINE_SAVED,
        vm_id=vm_id,
        metadata={"baseline": json.dumps(vm_info, default=str)},
    )
)
```

No new table. No schema migration. The baseline is just a JSON blob in the existing `metadata` column. `load_baseline()` queries for the most recent `DRIFT_BASELINE_SAVED` event for the VM and deserialises it.

### Graph Integration: New Node Between Discover and Plan

The drift check inserts a new node between `discover` and `plan_actions`:

```
discover → drift_check → plan_actions
              ↓ (if abort_on_detection)
         audit_results
```

`route_after_discover` was changed from `"plan_actions"` to `"drift_check"`. When detection is disabled, `drift_check_node` is a fast no-op that returns `{}`. When no baseline exists (first run), it logs and passes through.

```python
async def drift_check_node(state, *, audit_store):
    if not state.get("drift_detection_enabled", False):
        return {}   # fast path — most runs never touch this

    baseline = await load_baseline(audit_store, vm_id)
    if baseline is None:
        return {"drift_result": {"has_drift": False, "baseline_found": False}}

    result = compare_states(baseline, dict(vm_info))
    if result.has_drift and state.get("drift_abort_on_detection", False):
        return {"drift_result": ..., "error": "Drift detected, aborting: ..."}

    return {"drift_result": ...}
```

### Baseline Saving

After a successful run, `audit_results_node` saves the current VM state as the new baseline:

```python
if state.get("drift_detection_enabled", False) and not error:
    if has_success:
        await save_baseline(audit_store, vm_id, dict(vm_info))
```

This means the baseline is always "what the VM looked like after the last successful maintenance run" — the right reference point for detecting unexpected changes.

---

## Settings Wiring

All three features are controlled via `settings.yaml` and env var overrides:

```yaml
agent:
  rolling_update_percentage: 100      # ERRANDER_ROLLING_UPDATE_PCT
  wave_failure_threshold: 0.5
  health_check_command: "echo ok"
  canary_enabled: false               # ERRANDER_CANARY_ENABLED
  canary_health_check_command: "systemctl is-system-running"
  drift_detection_enabled: false      # ERRANDER_DRIFT_DETECTION
  drift_abort_on_detection: false     # ERRANDER_DRIFT_ABORT
```

`init_batch_node` now accepts a `settings` parameter and injects all 7 values into `BatchGraphState` at batch start. Each per-VM `VMGraphState` receives `drift_detection_enabled` and `drift_abort_on_detection` via `Send()` in the wave dispatcher.

---

## Metrics

A new Prometheus counter tracks health check outcomes:

```python
WAVE_HEALTH_CHECKS = Counter(
    "errander_wave_health_checks_total",
    "Wave health check outcomes",
    ["wave", "outcome"],   # wave=0/1/2, outcome=passed/failed
)
```

---

## Key Gotchas

**1. `route_after_discover` regression** — Changing the route from `"plan_actions"` to `"drift_check"` broke an existing test that asserted `route_after_discover(state) == "plan_actions"`. Updating the test is correct — the test was asserting the old routing, not a desired behavior.

**2. Canary with 1 target** — When there's only 1 target, canary mode can't split. `if len(healthy) > 1` guards this — the single VM simply runs as wave 0 without forcing a 1-VM split of itself.

**3. Wave counter advances even on abort** — `check_wave_health_node` always increments `current_wave`, even when it returns `wave_aborted=True`. This is intentional: the routing function uses `wave_aborted` to decide whether to loop or collect, not `current_wave`.

**4. `dispatch_wave` can fall through to health check** — If a wave is somehow empty (`not waves[current_wave]`), the dispatcher returns `"check_wave_health"` instead of emitting `Send()`. This prevents the graph from hanging on an empty wave.

---

## Quiz

1. What does `_partition_into_waves(targets, 33)` return for 10 targets? (Hint: `ceil(10 * 0.33) = ?`)
2. Why is canary implemented as "wave 0 = 1 VM" rather than a separate pre-check node?
3. What happens if `drift_detection_enabled=True` but there's no baseline yet (first run)?
4. Where are drift baselines stored, and why is that a good design choice?
5. Why does `dispatch_wave` return `{}` (empty dict) from its node function?
6. What's the difference between `wave_failure_threshold=0.5` for a normal wave vs. the canary wave?
