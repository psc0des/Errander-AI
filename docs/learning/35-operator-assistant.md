# 35 -- Operator Assistant (Phase D)

## What was built and why

Phase D implements the Layer A "Operator Assistant" described in `docs/AI-ARCHITECTURE.md`. Before this phase, the agent could act on VMs (Layer B) and probe them for signals (Phase B), but it had no way to synthesize those signals into actionable recommendations for the operator.

The `--ask "question"` CLI lets an operator pose a natural-language question -- "Why is production disk growing so fast?" or "Should I run patching tonight?" -- and get an LLM-powered analysis of the fleet's stored signal data.

---

## The two-layer invariant

This is the most important design constraint in the entire codebase:

```
Layer A (OperatorAssistant)     Layer B (existing batch/probe)
  - reads stores                  - writes stores
  - calls LLM                     - no LLM in execution path
  - produces text                 - executes SSH commands
  - never executes                - never calls LLM to decide what to run
```

`OperatorAssistant` has zero imports from `SandboxExecutor`, `FileLocker`, or `ApprovalManager`. A future code review should grep for these in `operator_assistant.py` and treat any match as a Layer A violation.

---

## Architecture

```
--ask "question" [--env ENV]
  |
  run_ask_query()              (main.py, deferred imports, same pattern as run_env_probe_main)
  |
  OperatorAssistant.investigate()
    |
    _build_context()           read-only queries to existing stores
      - audit_store.get_recent_batches()
      - audit_store.get_events(vm_id, event_type=ACTION_FAILED)
      - audit_store.get_events(vm_id, event_type=DRIFT_KIND_CHANGED)
      - disk_history_store.get_distinct_mountpoints() + get_window()
      |
    FleetContext (dataclass)
      |
    _format_prompt(question, context)  -> str
      |
    llm_client.complete(prompt, AssistantResponse)
      |  None on failure
    _fallback_response(question, context)  (always works, no LLM needed)
      |
    AssistantResponse (Pydantic model)
      - summary: str
      - findings: list[str]
      - recommendations: list[str]
      - risk_level: "low"|"medium"|"high"|"unknown"
```

---

## Key design: no new stores

Phase D reads from stores already populated by Phase B probes and the maintenance batch graph:

| Signal | Source store | Written by |
|--------|-------------|------------|
| Action failures | `AuditStore` (`ACTION_FAILED` events) | Batch graph |
| Disk growth trends | `VMDiskHistoryStore` | `disk_snapshot_node` (probe + batch) |
| Config drift | `AuditStore` (`DRIFT_KIND_CHANGED` events) | `drift_baseline_node` (probe + batch) |
| Failed SSH logins | `AuditStore` (`FAILED_SSH_LOGINS_OBSERVED`) | `failed_logins_node` (probe + batch) |

Running `--probe-now` before `--ask` gives the assistant fresher data.

---

## The `isinstance` store checks

`_build_context` uses `isinstance` guards before calling store-specific methods:

```python
if isinstance(disk_history_store, _DiskStore):
    mountpoints = await disk_history_store.get_distinct_mountpoints(target.name)
```

This is the same pattern used in `vm_graph.py` nodes. It means:
1. Tests can pass a `MagicMock()` and the disk/baseline queries are safely skipped
2. If the store is `None` or a different type, the assistant degrades gracefully

The `BaselineStore` check doesn't actually query `baseline_store.latest()` in the current implementation — it queries `DRIFT_KIND_CHANGED` audit events instead. This is simpler and avoids iterating all 4 drift kinds per VM per `scope_key` permutation.

---

## The fallback design

`_fallback_response()` runs when `llm_client=None` or when the LLM returns `None` (timeout, parse failure, network error). It:
1. Classifies each VM by what signals it has (failures, disk alerts, drift, logins)
2. Produces direct findings and recommendations from the classification
3. Sets `risk_level` deterministically: `high` if failures or drift, `medium` if disk or logins, `low` otherwise

This means `--ask` is always useful, even with no LLM configured. On a fresh install with no probe data, it returns "No significant signals detected" and recommends running `--probe-now`.

---

## LLM prompt design

The prompt opens with an explicit Layer A instruction:

```
You NEVER suggest executing commands directly -- only what the human operator should consider.
```

This is enforced by the system prompt, not just policy. The structured JSON schema at the bottom (`findings`, `recommendations`) further constrains the output to analysis, not imperatives.

---

## Code walkthrough

### `errander/models/analysis.py`

Three types:
- `AssistantResponse` (Pydantic) — LLM output schema; also returned by fallback
- `VMSignalSummary` (dataclass) — per-VM aggregated signals
- `FleetContext` (dataclass) — whole-fleet context assembled before LLM call

`AssistantResponse` is Pydantic so it can be passed to `llm_client.complete()`. The context types are plain dataclasses — they're never serialized to JSON.

### `errander/agent/operator_assistant.py`

Four public entry points (module-level functions + one class method):
- `OperatorAssistant.investigate()` — the main entry point
- `OperatorAssistant._build_context()` — assembles FleetContext from stores
- `_format_prompt()` — converts FleetContext to LLM prompt string
- `_fallback_response()` — deterministic summary from FleetContext

### `errander/main.py` — `run_ask_query()`

Same deferred-import pattern as `run_env_probe_main`. Uses `strict_mode=False` for the audit store because investigation is read-only and shouldn't fail if the audit DB has a write error in another transaction.

---

## Gotchas

1. **`--env` is optional**: Without it, all environments are surveyed. For large fleets this can be slow (many `get_events` calls). Scope with `--env` for faster responses.

2. **LLM is not constructed when `llm_base_url` is empty**: The `llm_client=None` path is the fallback. A fresh install with no LLM configured gets deterministic output, not an error.

3. **`isinstance` guards must use the concrete class, not the TYPE_CHECKING alias**: The `TYPE_CHECKING` imports (`VMDiskHistoryStore`, `BaselineStore`) are strings at runtime due to `from __future__ import annotations`. The isinstance checks use deferred concrete imports inside the method body (`from errander.safety.disk_history import VMDiskHistoryStore as _DiskStore`).

4. **`_build_context` queries are per-VM**: For a fleet of N VMs, this makes ~5N async store queries. All queries are on the same SQLite connection so there's no connection overhead, but large fleets could be slow. Acceptable for MVP; can be batched later.

---

## Quiz

1. What is the Layer A invariant, and how would you verify `operator_assistant.py` doesn't violate it?
2. Why does `_build_context` use `isinstance` checks before querying stores?
3. What happens when `llm_base_url` is not configured?
4. Why is `AssistantResponse` a Pydantic model while `FleetContext` is a dataclass?
5. How does running `--probe-now` before `--ask` improve the assistant's answers?
6. What does the `strict_mode=False` on `AuditStore` in `run_ask_query` mean, and why is it correct here?
