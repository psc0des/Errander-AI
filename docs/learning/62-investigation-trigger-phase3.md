# 62 — Detect-and-Propose, Phase 3: probe-triggered investigations

## What was built and why

Phase 1 gave deterministic origination. Phase 2 gave an agent that investigates *when
asked* (`--ask --agentic`). Phase 3 closes the loop: the probe can now launch that
investigation **on its own**, overnight, for VMs it just flagged — no operator has to
type `--ask` first. This is the concrete answer to "did you build something agentic":
a system that notices, investigates, and has an evidenced proposal waiting by morning.

Default OFF (`ERRANDER_INVESTIGATION_TRIGGER_ENABLED`). When on, after the Phase 1
detector files its template proposals for a probe run, the trigger picks up to
`investigation_max_investigations_per_probe` affected VMs and runs one bounded
investigation each, *enriching* — never replacing — what the detector already filed.

## Key concepts

### VM-level dedup, not per-signal-kind (a documented simplification)

The plan's checklist said "per-signal-kind dedup window." In practice, one VM can carry
multiple signals in a single probe (e.g. disk growth *and* drift), and running a separate
investigation per signal per VM multiplies LLM calls for no real benefit — one combined
investigation, covering everything flagged on that VM, is both cheaper and matches "an
investigation run per affected VM" literally. So the dedup marker is keyed on **vm_id
only**: `probe-trigger:{vm_id}`. The trade-off — a VM investigated successfully within the
window is skipped even if a *new*, different signal appears on it before the window
elapses — is accepted and written down here and in the code, not silently smoothed over.

### The dedup marker only counts genuine success

```python
decisions = await ai_decision_store.get_decisions(
    batch_id=f"probe-trigger:{vm_id}", decision_type="investigation_agent", limit=1,
)
if not decisions or decisions[0].outcome != "success":
    return False  # not deduped — a failure/fallback never blocks a retry
```

This reuses the *existing* `AIDecisionStore` — no new table. `InvestigationAgent` already
logs a `decision_type="investigation_agent"` row for every final answer (success or
fallback) under whatever `batch_id` the caller passes; the trigger just passes a
deterministic, vm-scoped `batch_id` so the next probe can query "was this VM
successfully investigated recently?" without any new infrastructure.

### `NoOpFallback` — D2 at zero cost

Phase 2's `investigate_agentic` requires a `fallback` for when the LLM/tools fail. The
`--ask` CLI path uses `OperatorAssistant` — a real second investigation attempt (built
fleet context, possibly its own LLM call). For the trigger, D2 says "the deterministic
proposal stands untouched" on failure — spending a *second* LLM call just to produce a
fallback nobody asked for would be wasteful. `NoOpFallback.investigate()` returns an
empty `AssistantResponse` **instantly**:

```python
class NoOpFallback:
    async def investigate(self, question: str = "", **kwargs: Any) -> AssistantResponse:
        return AssistantResponse(summary="", findings=[], recommendations=[], risk_level="unknown")
```

Empty findings + empty `proposed_work` means the trigger's own logic (`if not
response.findings and not response.proposed_work: continue`) skips enrichment entirely —
"stands untouched" falls straight out of the empty-response check, no special-casing.

### `InvestigationFallback` — a Protocol, not a concrete class

To let `NoOpFallback` stand in for `OperatorAssistant` in `investigate_agentic`'s
signature, the `fallback` parameter's type changed from the concrete `OperatorAssistant`
class to a structural `Protocol`:

```python
class InvestigationFallback(Protocol):
    async def investigate(self, question: str, **kwargs: Any) -> AssistantResponse: ...
```

Both `OperatorAssistant` and `NoOpFallback` satisfy it structurally — no inheritance
needed, no coupling `investigation_agent.py` to a concrete class it shouldn't care about.

### Enrichment reuses the Phase 1 write path — never a parallel one

"Never bypasses the detector's dedup" is enforced structurally, not by convention: the
trigger's `_enrich_proposal` calls `proposal_store.create_or_refresh(enriched)` — the
*exact same* method `file_proposals()` calls. Because the enriched proposal carries the
same `(vm_id, action_key)`, the store's partial-unique-index upsert lands on the same
open row. There is no second code path that could diverge from the dedup invariant.

## Code walkthrough

- `agent/investigation_trigger.py` — `group_candidates_by_vm` (pure), `_recently_
  investigated` + `select_investigation_targets` (dedup + cap, testable independent of
  the loop), `_build_question` (pure, summarizes a VM's flagged signals + evidence),
  `_enrich_proposal` (merges findings into evidence, raises confidence on medium/high
  risk, refreshes via the shared store path, logs an `AuditEvent`), `run_triggered_
  investigations` (the orchestrator — per-VM try/except so one VM's failure never kills
  the loop for the rest).
- `agent/proposal_detector.py` — `file_proposals()` now returns
  `(created, refreshed, stored_proposals)`. The third element is what Phase 3 needed and
  Phase 1 didn't: the caller must know exactly which rows (with real, persisted
  `proposal_id`s) were touched *this* probe, without a second `get_pending()` scan.
- `main.py` — `_maybe_run_triggered_investigations` is the wiring layer: checks the kill
  switch and LLM configuration first (cheap early-outs), builds the `LLMClient`,
  `VMFactsStore`, `ToolRegistry`, and calls the trigger. Both probe call sites
  (`run_env_probe_main` and the scheduled closure) needed their `try/finally` restructured
  so the Prometheus/ELK clients stay open through the trigger call instead of being closed
  right after `run_env_probe` returns (they're tools the investigation may need).

## Gotchas encountered

- **The Protocol's `**kwargs: Any` doesn't structurally match a fixed set of keyword-only
  parameters** in mypy's protocol conformance check — `OperatorAssistant.investigate`'s
  many named keyword-only args don't satisfy `Protocol.investigate(**kwargs: Any)`
  structurally, even though the runtime call (`fallback.investigate(**fallback_kwargs)`)
  is exact. This is a known mypy limitation, not a real bug. Fixed with one `cast()` at
  the single call site in `main.py`, with a comment explaining why.
- **`NoOpFallback.investigate(question: str, ...)` broke on a real call.** Phase 2's own
  tests mocked `fallback.investigate` entirely (`AsyncMock`), so they never caught that
  `_fallback()` calls `fallback.investigate(**fallback_kwargs)` — and the trigger passes
  `fallback_kwargs={}`, supplying no `question`. A real `NoOpFallback` class immediately
  surfaced the missing arg. Fixed by giving `question` a default (`= ""`) since the no-op
  ignores it anyway. Caught by a real integration test, not a mock — the lesson: prefer a
  real lightweight implementation over a mock when the mock would hide a genuine call-
  signature mismatch.
- **prom/elk lifecycle**: closing the clients in `finally` immediately after `run_env_probe`
  (the pre-Phase-3 structure) meant the trigger — which runs *after* detection — would get
  closed clients if it tried to reuse them. Both probe call sites needed restructuring to
  keep the `try` block open through detection *and* the trigger, closing only at the end.

## Quiz yourself

1. A VM has a disk-growth proposal filed at 9am and a *new* drift proposal filed at 3pm the
   same day. If the VM was investigated successfully at 9am, does the 3pm probe re-investigate
   it? Why or why not — and where would you change the code if you wanted the opposite?
2. Why does `NoOpFallback` exist instead of reusing `OperatorAssistant` with `llm_client=None`
   (which also produces a fallback-y response without calling the LLM)?
3. Trace what happens end-to-end when `_maybe_run_triggered_investigations` runs but
   `investigate_agentic` internally hits its own tool-call budget cap (not an LLM outage).
4. Why does `file_proposals()` need to return the stored proposals now, when Phase 1 was
   fine with just counts?
5. What specifically prevents the trigger from filing a proposal for a VM that doesn't
   exist in the environment's inventory?

## References

- Plan: `tasks/fable-plan.md` §3 Phase 3 (includes the VM-level-dedup design delta).
- Diagram: `docs/diagrams/detect-and-propose.md` — Phase 3's trigger gate (blue) feeding
  the same origination path as Phase 1's detector (green).
- Phase 2 foundation: `docs/learning/61-investigation-agent-phase2.md`.
