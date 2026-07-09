# 63 — Detect-and-Propose, Phase 4: the memory loop and re-proposal suppression

## What was built and why

Phases 1-3 gave Errander origination (deterministic detector), agency (bounded
investigation loop), and initiative (probe-triggered investigations). What was still
missing: **memory**. Without it, an operator who rejects "clean up `/var` on `db-03`"
twice would see the exact same proposal again the next morning, forever — a spam
generator, not an assistant. Phase 4 closes that loop using infrastructure that was
already sitting there waiting: `VMFactsStore` (built in an earlier phase, deriving
learned facts from `audit_events`) and `ProposalStore.count_rejections()` (built in
Phase 1, with a docstring that literally said *"suppression input (Phase 4)"*).

Two things shipped together, because they're two views of the same data:

1. **Facts** — `ProposalOutcomeFact` / `VMFactsStore.proposal_outcomes()`: how many
   times has (vm, action) been proposed, approved, rejected, executed? Surfaced to both
   investigation paths (deterministic `--ask` and agentic `--ask --agentic`).
2. **Suppression** — a policy layer on top of those same facts: reject a pair twice,
   and further auto-proposals for it pause for two weeks. Not a recommendation the LLM
   can ignore — a hard gate before anything gets written to the proposal queue.

## Key concepts

### Suppression only blocks CREATE, never REFRESH

The one subtlety that shapes the whole design: an *open* (pending) proposal must always
stay refreshable — a human hasn't decided on it yet, so there's nothing to "suppress."
Suppression only intercepts the moment a detector or agent wants to originate a **new**
proposal for a pair that has no open row and a track record of rejection.

```python
async def create_or_refresh_unless_suppressed(self, proposal, *, suppression_threshold, ...):
    existing = await self.get_open(proposal.vm_id, proposal.action_key)
    if existing is None:                    # nothing open — this WOULD be a fresh create
        if await self.is_suppressed(...):
            return None, False              # refuse
    return await self.create_or_refresh(proposal, ...)  # refresh, or an allowed create
```

`get_open()` is the check that keeps the whole thing honest — without it, suppression
would also block operators from seeing evidence updates on a proposal they haven't
decided yet, which isn't the goal at all.

### The suppression marker: count + timestamp, not just count

"Rejected ≥2×" alone would suppress a pair *forever* the moment it hit two rejections —
wrong, because state changes (disk fills up again six months later, the same action may
be warranted). The policy is threshold **and** a rolling window: `rejection_window_state`
returns `(count, latest_rejection_at)`, and `is_suppressed` only returns true when the
count clears the threshold **and** the latest rejection is still inside the cooldown
window. Past the window, a fresh proposal is allowed again — the suppression naturally
expires without any cleanup job.

### One filing helper, three callers — closing a real bypass

By Phase 3, there were three places that could write a new `AgentProposal`: the Phase 1
detector (`file_proposals`), the Phase 3 trigger's `proposed_work` filing, and the
`--ask --agentic` filer in `main.py`. Bolting suppression onto only the first would have
left the other two as bypasses — an agent that gets rejected via `--ask --agentic` could
just recommend the same thing again through the probe-triggered path. So Phase 4 pulled
the write path into one function, `file_or_suppress_one()`, that all three now call:

```python
async def file_or_suppress_one(proposal, *, store, audit_store, suppression_threshold, ...):
    if proposal.kind == ProposalKind.ACTION:
        stored, created = await store.create_or_refresh_unless_suppressed(...)
        if stored is None:
            await audit_store.log_event(AuditEvent(event_type=PROPOSAL_SUPPRESSED, ...))
            return None, "suppressed"
    else:
        stored, created = await store.create_or_refresh(proposal, ...)  # REVIEW: never suppressed
    await audit_store.log_event(...)  # CREATED / REFRESHED
    return stored, ("created" if created else "refreshed")
```

One function, one audit contract, three callers that can no longer drift apart.

### Suppression scope: ACTION-kind only (a plan-literal reading, not an add-on)

The plan's own checklist said *"rejected ≥2× for **(vm_id, action_type)**"* — and only
ACTION-kind proposals carry a real `action_type` (REVIEW proposals' `action_type` is
`""`). Rather than inventing a suppression story for review-only proposals (drift,
failed-login spikes), Phase 4 takes the plan at its word: suppression is scoped to
ACTION proposals. This also happens to be the right behavior — re-surfacing "config
drift detected" every day the drift persists is desired, not spam; it's evidence, not a
request for permission.

### Facts feed both investigation paths, without adding an untrusted free-text surface

`FleetContext.proposal_history` (deterministic `--ask`) and the `get_vm_facts` tool
(agentic path) both surface the SAME `ProposalOutcomeFact` — counts and one timestamp,
deliberately no free-text rejection reasons. This keeps the addition inside the existing
budget/redaction story: there's nothing here for `ContextBudgeter` to truncate and
nothing for `ContextRedactor` to scrub, because there's no untrusted text in the fact at
all — a deliberate scope narrowing versus, say, `ActionRejectionFact` (an older,
different fact type) which does carry free-text rejection reasons.

## Code walkthrough

- `safety/vm_facts.py` — `ProposalOutcomeFact` + `proposal_outcomes()`, same
  audit-events-only derivation pattern as every other fact in this file (no new tables,
  no dependency beyond `AsyncDatabase`). Confidence reuses `_sample_confidence` on
  `proposed_count`, consistent with `ActionOutcomeFact`.
- `safety/proposal_store.py` — `rejection_window_state`, `is_suppressed`, `get_open`,
  `create_or_refresh_unless_suppressed`. All additive; nothing about Phase 1-3's dedup
  or execution-claim behavior changed.
- `agent/proposal_detector.py` — `file_or_suppress_one` (new) + `file_proposals`
  (refactored to loop over it, return type grew to `(created, refreshed, suppressed,
  stored)`).
- `agent/investigation_trigger.py`, `main.py::_file_agent_proposals` — both swapped
  their direct `store.create_or_refresh(...)` call for `file_or_suppress_one(...)`.
- `models/analysis.py`, `agent/operator_assistant.py` — `FleetContext.proposal_history`
  field, populated in `_build_context`, rendered in `_format_prompt`'s "Operational
  history facts" section (a new subsection, same pattern as `action_outcomes` etc.).
- `agent/investigation_tools.py` — `get_vm_facts` tool's output gained a third clause
  reporting proposal history alongside action outcomes and reboot patterns.
- `commands/vm_facts.py` — new `_print_proposal_history`, a table matching the file's
  existing `_print_outcomes`/`_print_rejections` style, annotated with `SUPPRESSED until
  <date>` computed via `ProposalStore.is_suppressed` (loaded fresh, not cached).

## Gotchas encountered

**Reused `AgentProposal` object across a reject-twice test loop caused a real
`IntegrityError`.** The natural way to write "reject this pair twice" is:

```python
candidate = AgentProposal(vm_id="web-01", action_type="disk_cleanup", ...)
for _ in range(2):
    stored, _ = await store.create_or_refresh(candidate)
    await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
```

This fails on the second iteration with a primary-key collision. Why: `AgentProposal`'s
`proposal_id` is assigned once, at construction, via `default_factory=uuid4`. The first
`create_or_refresh` inserts a row with that id and status `pending`. `decide()` flips it
to `rejected` — no longer matched by the partial unique index (`WHERE status='pending'`).
The second `create_or_refresh` call, given the *same* `candidate` object, tries to
`INSERT` a row with the *same* `proposal_id` — but that id is already a primary key from
the first insert, and the `ON CONFLICT` clause only targets the partial index, not the
primary key, so Postgres raises `IntegrityError` instead of silently upserting.

The fix is to construct a fresh `AgentProposal` (or call `detect_proposals()` again) on
each iteration — which is also what the real system always does; a detector never reuses
a stale candidate object across probe runs. The bug was 100% a test-construction mistake,
never reachable from production code, but it was a useful forcing function: it's the
kind of subtle store-identity behavior worth knowing before writing more suppression
tests, so it's captured here and in `tasks/lessons.md`.

## Quiz yourself

1. An operator rejects `disk_cleanup` on `web-01` once, then a completely unrelated
   `log_rotation` proposal on the same VM gets rejected once too. Is either suppressed?
   Why or why not?
2. Why does `create_or_refresh_unless_suppressed` check `get_open()` BEFORE checking
   `is_suppressed()`, rather than the other way around?
3. A REVIEW-kind proposal (drift signal) has been rejected five times. Does Phase 4
   suppress it? Where in the code is that decided?
4. Trace what happens if `--ask --agentic` recommends an action for a VM that the Phase 3
   trigger already had suppressed the night before. What stops it from being filed twice?
5. Why does `ProposalOutcomeFact` deliberately NOT include free-text rejection reasons,
   unlike the older `ActionRejectionFact`?

## References

- Plan: `tasks/fable-plan.md` §3 Phase 4 (includes the ACTION-only scope delta).
- Diagram: `docs/diagrams/detect-and-propose.md` — Phase 4's facts/suppression loop
  feeding back into both origination paths.
- Prior phases: `docs/learning/60-*.md` (detector), `61-*.md` (agentic loop),
  `62-*.md` (trigger).
