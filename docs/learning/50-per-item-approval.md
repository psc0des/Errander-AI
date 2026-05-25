# 50 — Per-Item Approval UI + Decision Reasoning

## What was built and why

Before this feature, the Web UI approval page had a single "Approve All / Reject All" button per batch. This meant an operator who wanted to approve 14 of 16 package upgrades had no way to do it from the UI — they'd have to approve all or reject all.

The SRE audit requested two things:
1. **Per-item granularity** — each package upgrade and each service restart should have its own approve/reject checkbox
2. **Decision Reasoning** — a tab/section showing whether the AI recommendation came from the LLM or a deterministic fallback, and why

## Key Concepts

### vm_plans threading through the stack

The per-item approval requires the structured plan to travel from the dry-run planner all the way to the UI render, back through the POST handler, and then back into the graph execution as a filter.

```
await_dual_approval(vm_plans=[...])
    → manager.register(vm_plans=[...])          # stored on PendingApproval
        → _render_approval_plan(vm_plans, ...)   # renders checkboxes in UI
    ← POST handler collects checked fields
        → manager.decide(approved_items=[...])   # stored on PendingApproval
    ← await_dual_approval returns (bool, user, approved_items)
        → approval_gate_node builds operator_approved_packages
            → dispatch_current_wave calls _filter_patching_packages()
```

### PendingApproval dataclass

Two new fields:
```python
vm_plans: list[dict[str, object]] | None = None          # set at register()
approved_items: list[dict[str, object]] | None = field(default=None, init=False)  # set at decide()
```

`vm_plans` carries the plan structure for rendering. `approved_items` carries the operator's selection back out. Both `None` on the Slack path — Slack remains binary (approve all / reject all).

### BatchApprovalResult type alias

```python
BatchApprovalResult = tuple[bool, str | None, list[dict[str, object]] | None]
```

`await_dual_approval()` previously returned a 2-tuple. Extending to a 3-tuple required updating every unpack site and every mock `.return_value` in the test suite (15 sites across 4 files).

### Form field naming scheme

The POST form uses structured field names so the handler can reconstruct which items were checked:

- Patching packages: `pkg_{vm_idx}_{act_idx}_{pkg_idx}` (checkbox, only present when checked)
- Service restart: `svc_{vm_idx}_{act_idx}` (checkbox)
- Categorical actions (disk_cleanup, log_rotation, backup_verify): auto-included — no checkbox

The handler iterates `pending.vm_plans` to know what indices exist, then checks which fields are present in the POST data.

### _filter_patching_packages helper

After the operator selects a subset of packages, the graph must filter the planned actions before fanning out to per-VM execution:

```python
def _filter_patching_packages(
    actions: list[dict[str, object]],
    approved_pkgs: list[dict[str, str]],
) -> list[dict[str, object]]:
    approved_names = {p["name"] for p in approved_pkgs if p.get("name")}
    ...
```

This produces a copy of `planned_actions` with the `preview.packages` list trimmed to only the operator-approved packages. `_run_patching` in `vm_graph.py` sees the filtered list and only installs those packages — no changes needed there.

### Decision Reasoning section

`_render_approval_reasoning(ai_decisions)` queries `AIDecisionStore.get_decisions(batch_id=...)` and renders a collapsible `<details>` block with one row per AI decision:

- Decision type (e.g. `plan_prioritization`, `report_generation`)
- LLM badge or FALLBACK badge
- Model name and latency (ms)
- Link to `/ui/ai-decisions/{id}` for full prompt inspection

This gives operators full explainability: they can see whether the plan they're approving was shaped by LLM reasoning or a deterministic fallback.

## Gotchas

### UnboundLocalError from partial initialization

`approved_items` was set inside an `elif` branch but not in the `if dry_run:` path. Python raises `UnboundLocalError` at the `return` site, not at the assignment. Fix: always initialize before the if/elif/else chain:

```python
approved_items: list[dict[str, object]] | None = None  # default before branches
```

### mypy rejects dict() on object-typed values

`action.get("preview")` returns `object` in strict mode. `dict(action.get("preview") or {})` triggers `No overload variant of "dict"`. Fix:

```python
raw_preview = action.get("preview") or {}
new_preview = dict(raw_preview) if isinstance(raw_preview, dict) else {}
```

### Slack path remains binary

`approved_items=None` from `await_dual_approval` means "all items approved" — this is the Slack path. Per-item granularity is Web UI only in v1. The code handles this correctly: `None` approved_items → no filtering in `dispatch_current_wave`.

## Quiz yourself

1. What does `approved_items=None` mean vs `approved_items=[]`?
2. If the operator approves 3 of 5 packages via the Web UI, what does `_filter_patching_packages` return for the VM's `planned_actions`?
3. Why does `_render_approval_reasoning` use `contextlib.suppress(Exception)` instead of a try/except?
4. What field name does the POST handler look for when the operator approves a service restart on VM index 1, action index 0?
