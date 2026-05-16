# 39 — LangGraph Signal Integration (Phase F)

## What was built and why

Phase F closed four gaps between the daily probe and the LangGraph batch graph — signals were being collected but not influencing decisions or safety gates.

| Commit | Feature |
|---|---|
| F1 | `StoredSignalContext` feeds disk/drift/patch history into `plan_vm_node` |
| F2 | Early sudo/wrapper readiness check in `validate_targets_node` |
| F3 | Probe escalation — critical signals trigger a Slack alert |
| F4 | `post_cleanup_disk_gate_node` re-checks disk before patching |

---

## F1 — Stored signals → LLM planning

### The gap

`plan_vm_node` called `prioritize_actions(vm_id, os_family, ...)` with no historical context. The LLM saw only what it could observe from the current SSH session — no sense of "this VM has had growing disk for 3 days" or "patching hasn't run in 10 days."

### The fix

```python
@dataclass
class StoredSignalContext:
    disk_trend_summary: str = ""
    drift_kinds_detected: list[str] = field(default_factory=list)
    recent_failure_count: int = 0
    last_patch_days_ago: int | None = None
    failed_login_count_24h: int = 0
```

`_load_stored_signals()` is a best-effort async helper that reads from three stores:
- `disk_history_store.get_window(vm_id, window_days=7)` — disk trend summary
- `audit_store.get_events(vm_id)` filtered for `DRIFT_KIND_CHANGED` and `ACTION_FAILED`
- Parses `ACTION_COMPLETED` events to find last patch date

Best-effort: any store read failure returns a partial context, never blocks planning.

### In the LLM prompt

```
## Historical signals from monitoring stores
- Disk trend (7 days): growing from 72% to 81% on /
- Drift kinds detected: authorized_keys, sudoers
- Recent action failures (30 days): 2
- Days since last patching: 14
- Failed SSH login attempts (24h): 45
```

This lets the LLM say "patching is overdue AND login anomalies are present — prioritize patching+drift remediation over docker_prune."

---

## F2 — Early readiness check

### The gap

`sudo_preflight_node` ran late in the graph: after `plan_actions`, which means the LLM had already spent time planning for a VM that would fail at the last moment. The `check_target()` function existed in `target_validation.py` but was only called from test scaffolding.

### The fix

After OS detection succeeds in `validate_targets_node`, run `check_target()` immediately:

```python
from errander.execution.target_validation import check_target
readiness = await check_target(vm_id, hostname, ssh_user, ssh_key_path, ssh_manager)
if readiness.verdict == "blocked":
    # log TARGET_READINESS_BLOCKED, move to failed_targets
    continue
```

The `TARGET_READINESS_BLOCKED` event type was added to `EventType` to make this auditable.

### Why a local import

`check_target` is imported inside the function body rather than at the module top. This keeps the import scoped to where it's used and makes mocking simpler in tests (`patch("errander.execution.target_validation.check_target")`). The alternative — module-level import — would require patching a reference in `errander.agent.graph`, not the original.

---

## F3 — Probe escalation

### The gap

`DigestReport` had no way to signal urgency. A fleet with a VM at 95% disk and 3 failed services looked identical to a healthy fleet in the Slack digest — operators had to read the whole message.

### The fix

```python
def _check_escalation(results: list[ProbeVMResult]) -> tuple[bool, list[str]]:
    # disk ≥ 90% or delta ≥ 15% in window
    # 2+ failed services
    # drift changes AND > 20 failed login attempts together
```

These thresholds are conservative — they're a **HITL signal**, not an autonomous trigger. The Slack alert suggests running an emergency batch, it doesn't schedule one.

`render_digest_report()` adds an escalation header at the top:

```
:rotating_light: *ESCALATION: Critical signals require attention*
  • vm-01: disk / at 91%
```

`main.py` posts a separate `post_alert()` after the digest when escalation is needed.

---

## F4 — Post-cleanup disk gate

### The gap

A disk_cleanup action might free only 2 GB on a 95%-full 100 GB disk. The next planned action (patching) downloads package files — which could fill the remaining 5 GB and fail mid-patch, leaving packages in a broken state.

### The fix

`post_cleanup_disk_gate_node` is inserted between `dispatch_action` and `check_more_actions`:

```
dispatch_action → post_cleanup_disk_gate → check_more_actions → (loop | audit_results)
```

It only fires when:
- Last completed action was `disk_cleanup` or `log_rotation`
- Next planned action is `patching`

If it fires and disk is ≥ 95%: inject a skipped result for patching and advance the index. Patching is bypassed but other actions (docker_prune, etc.) still run.

```python
results.append({
    "action_type": "patching",
    "status": ActionStatus.SKIPPED.value,
    "detail": f"post_cleanup_disk_gate: / still at {disk_pct}% after disk_cleanup — patching skipped",
})
return {"results": results, "current_action_index": index + 1}
```

The `DISK_GATE_BLOCKED` audit event records `disk_pct` for post-hoc analysis.

### Why not in dispatch_action

The gate was implemented as a separate node (not inside `dispatch_action`) to:
1. Keep `dispatch_action` single-responsibility (run one action)
2. Make the gate independently testable
3. Allow future routing changes (e.g., conditional skip vs. abort)

---

## Lessons

### Threshold boundary semantics — use `>`, not `>=`

The fleet abort uses `> threshold` not `>= threshold`. This is intentional: at exactly the threshold, you don't abort. The escalation disk check uses `>= 90` because "at or above 90%" should warn. Document which boundary applies where.

### Audit events need to exist before they're referenced

Adding `TARGET_READINESS_BLOCKED` to `EventType` was easy. The subtle bug was a typo (`EventType.PREFLIGHT_FAILED`) that raised `AttributeError` inside the `except Exception` block — so the exception was silently swallowed and blocked VMs still landed in `healthy_targets`. Pattern: always define EventType values before using them, and test that blocked VMs appear in `failed_targets`, not `healthy_targets`.

---

## Quiz

1. Why is `_load_stored_signals` best-effort rather than fail-fast?
2. What's the difference between `sudo_preflight_node` (F2's predecessor) and `check_target()` (F2)?
3. At what disk percentage does the post-cleanup gate warn-only vs. block?
4. If `disk_cleanup` frees space but disk is still at 94%, what does `post_cleanup_disk_gate_node` return?
5. Why does the escalation Slack alert suggest a command rather than running it autonomously?
