# 58 — Advisory-LLM Batch Planning (§8d Step 5, R1)

## What was built and why

Before R1, `prioritize_actions()` sent the VM's discovered state and a list of
candidate `ActionType`s to the LLM and used **the LLM's returned list as the
plan** — order *and* membership. The hardcoded `DEFAULT_PRIORITY` ordering was
only a fallback. Two problems followed from this (fable.md §8, findings F2/F6):

- **F2 — silent plan shrinkage.** `_parse_action_types` kept only the action
  types the model returned. If the LLM omitted `disk_cleanup` from its
  response, it silently vanished from the plan — no error, no audit trail
  entry explaining why. There was even a "3.2b policy enforcement" block that
  computed a filtered list... and then discarded it, reverting to the
  unfiltered list anyway. Dead code that *looked* like a safety gate.
- **F6 — AI risk without AI value.** In practice the LLM almost always just
  reproduced `DEFAULT_PRIORITY` order. So the agent was paying for prompt
  injection surface and non-determinism, for a result the hardcoded fallback
  would have produced for free.

R1's fix: split "what gets done" from "what the operator is told about it".

1. `prioritize_actions()` is now **100% deterministic** — always
   `_hardcoded_priority(available_actions, vm_info)`. No LLM, no exceptions.
2. A new, separate function, `generate_planning_note()`, is the **only**
   LLM call left in the batch-planning path. Its output — a short text
   note — is informational only. It is stored as `ai_note` on the per-VM
   plan dict, rendered on the approval surfaces under a clearly-labeled
   "AI analysis — informational only" section, and can never change which
   actions run, in what order, or with what parameters.

In the same change (the F4 sweep): deleted `analyze_failure()`,
`_FailureAnalysis`, `_build_failure_prompt()`, `_check_failure_analysis`,
`_VALID_RECOMMENDATIONS`, `_PrioritizedActions`, and the dead 3.2b
policy-filter block — all either fully dead code or made moot by point 1.

## Key concepts

### Deterministic plan, advisory note — two functions, two purposes

```python
# errander/agent/decisions.py
async def prioritize_actions(
    vm_info: VMInfo,
    available_actions: list[ActionType] | None = None,
) -> list[Action]:
    """Order maintenance actions by priority — deterministic, hardcoded (R1)."""
    if available_actions is None:
        available_actions = list(DEFAULT_PRIORITY)
    return _hardcoded_priority(available_actions, vm_info)
```

`prioritize_actions` is still `async def` purely for call-site compatibility
(~15 `await prioritize_actions(...)` sites across `graph.py`, `vm_graph.py`,
and tests) — there's nothing to await inside it anymore. This is a deliberate
"keep the seam, drop the implementation" move: every caller that previously
passed `llm_client=...`/`policy=...`/`ai_decision_store=...` now calls it with
just `vm_info` (and optionally `available_actions`), and mypy strict still
passes because the signature shrank in a backward-compatible way (fewer
required positional args, not more).

```python
async def generate_planning_note(
    vm_info: VMInfo,
    plan: list[Action],
    llm_client: LLMClient | None = None,
    batch_id: str = "unknown",
    vm_id: str | None = None,
    ai_store: AIDecisionStore | None = None,
    stored_signals: StoredSignalContext | None = None,
) -> str | None:
    """Generate an advisory note about the already-finalized plan.

    Layer A, informational only — the note can never change plan membership
    or ordering. Returns None when the LLM is unavailable or returns an
    empty/unparseable response; never raises, never blocks planning.
    """
```

Notice what `generate_planning_note` does **not** take: it has no `policy`
parameter and no `available_actions` parameter. It receives `plan` — the
*already-finalized* list of `Action` objects — as a read-only fact about the
world. There is structurally no way for its return value to feed back into
`plan`, because nothing downstream of `generate_planning_note` ever calls
`prioritize_actions` again with the note as input. The new
`TestGoldenPlanSafety::test_planning_note_llm_output_never_changes_plan` in
`tests/ai_evals/test_golden_plans.py` makes this an enforced invariant, not
just an argument: it calls `prioritize_actions()` once to get `plan_a`, runs
`generate_planning_note(vm, plan_a, llm_client=<mock with an arbitrary note>,
...)`, then calls `prioritize_actions()` again to get `plan_b`, and asserts
`plan_a == plan_b`. No matter what the mock LLM returns, the two plans are
identical — because they were never connected.

### Defense in depth on the note text: `_sanitize_note`

The note is LLM-generated free text that ends up inside a Slack code block
*and* an HTML page. Two independent defenses apply:

```python
_PLANNING_NOTE_MAX_CHARS = 700

def _sanitize_note(note: str, max_chars: int = _PLANNING_NOTE_MAX_CHARS) -> str:
    """Strip backticks and cap length — defense-in-depth for AI-generated text."""
    cleaned = note.replace("`", "").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned
```

- **Backtick stripping** happens here, at generation time, because the note
  is rendered inside a Markdown code span in the Slack message
  (`` `vm-01` ``-style formatting elsewhere in `_format_plan_for_approval`) —
  a stray backtick in the note could break out of that span.
- **HTML escaping** happens later, at *render* time, in `_render_approval_plan`
  via the existing `_esc()` helper — the same pattern every other
  user/AI-controlled string in the approval page already follows. Sanitizing
  twice (strip-at-generation, escape-at-render) means a bug in either layer
  doesn't expose the other.
- **Length cap** (700 chars) bounds how much of the approval artifact (and
  the plan-hash input — see below) one note can occupy, and guarantees the
  Slack message's plan/coverage/approval-instruction lines are never pushed
  out by the note.

### `ai_note` lives inside the hashed `vm_plans` — on purpose

```python
# errander/agent/graph.py — plan_vm_node
actions = await prioritize_actions(vm_info, available_actions=available_for_planning)

ai_note = await generate_planning_note(
    vm_info, actions,
    llm_client=llm_client, batch_id=batch_id, vm_id=vm_id,
    ai_store=ai_decision_store, stored_signals=stored_signals,
)

vm_plan: dict[str, Any] = {
    "vm_id": vm_id,
    "planned_actions": [...],
    "os_family": vm_info.os_family.value,
}
if ai_note:
    vm_plan["ai_note"] = ai_note

return {"vm_plans": [vm_plan]}
```

`ai_note` is just another key in the per-VM dict that goes into `vm_plans`,
and `generate_plan_artifact_node` hashes `{"vm_plans": vm_plans, ...}` with
`sha256(json.dumps(..., sort_keys=True, default=str))`. So the note is
automatically part of `plan_hash`, the saved plan snapshot, and the deferred
re-approval replay. **This is deliberate**, not an oversight: the audit record
should preserve exactly what the operator saw when they approved, note
included. If you ever find yourself wanting to move `ai_note` out of
`vm_plans` "so it doesn't affect the hash" — don't. That would let the note
shown at approval time silently diverge from the note in the audit trail.

### Rendering: web page primary, Slack secondary, same data

**Web** (`errander/web/ui.py::_render_approval_plan`):

```python
ai_note = plan.get("ai_note")
ai_note_html = ""
if isinstance(ai_note, str) and ai_note.strip():
    ai_note_html = (
        '<div class="apv-ai-note">'
        '<div class="apv-ai-note-hdr">'
        '<span class="badge bk-neu">AI analysis — informational only; '
        'plan content is deterministic</span>'
        '</div>'
        f'<div class="apv-ai-note-body">{_esc(ai_note)}</div>'
        '</div>'
    )

parts.append(f'<div class="apv-vm">{vm_hdr}{"".join(actions_html)}{ai_note_html}</div>')
```

**Slack** (`errander/agent/graph.py::_format_plan_for_approval`) — appended
*after* the approval instructions, deliberately:

```python
# A note here is intentionally placed LAST: Slack's ~2800-char truncation
# (~2800 chars, from the end of the string) costs this section first and
# never the plan/coverage/approval-instruction lines above.
ai_note_lines: list[str] = []
for plan in vm_plans:
    ai_note = plan.get("ai_note")
    if isinstance(ai_note, str) and ai_note.strip():
        ai_note_lines.append(f"\n  *`{plan.get('vm_id', '?')}`*: {ai_note}")
if ai_note_lines:
    lines.extend([
        "",
        "_AI analysis — informational only; plan content is deterministic_:",
        *ai_note_lines,
    ])
```

Both renderers do the exact same `isinstance(..., str) and .strip()` guard —
absent or blank `ai_note` means no section at all, not an empty header.
`tests/web/test_approval_ai_note.py` and the two new tests in
`tests/agent/test_approval_message_p01.py` lock in "present → section shown",
"absent → no section, no crash", and "HTML-escaped".

### Prompt: same VM-state context, different ask

`_build_planning_note_prompt` (renamed from `_build_prioritize_prompt`) keeps
the entire `VMInfo` + `StoredSignalContext` formatting block **verbatim** —
disk usage, docker availability, pending packages, uptime, disk trend, drift
kinds, recent failure count, last-patch age, failed SSH logins. What changed
is the *ask* at the end:

```python
lines += [
    f"\nPlanned actions (in execution order): {[a.action_type.value for a in plan]}",
    "\nWrite a 1-4 sentence note for the human operator highlighting anything"
    " noteworthy about this plan given the state above (e.g. risk context,"
    " trends, why an action matters now). Do not propose changes to the"
    " plan — it is fixed.",
    'Respond with JSON: {"note": "<1-4 sentences>"}',
]
```

Old prompt: "here's the state and candidate actions — *decide* the plan."
New prompt: "here's the state *and the plan, already decided* — comment on
it." The plan is now an input fact to the prompt, not an output to extract
from the response.

## Audit trail: `decision_type="planning_note"`

`generate_planning_note` logs to `AIDecisionStore` with
`decision_type="planning_note"` and `prompt_template_id="planning_note_v1"`,
mirroring the old `prioritize_actions` rows 1:1 — same three outcomes:

| Outcome | When | `prompt_full` / `response_raw` |
|---|---|---|
| `no_llm` | `llm_client is None` | both empty — logged before any prompt is built |
| `fallback` | LLM returned `None`, or `result.note.strip() == ""` | `prompt_full` set, `response_raw` set if a response object existed |
| `success` | LLM returned a non-empty note | both set; `ai_note` = `_sanitize_note(result.note)` |

`prompt_full` goes through `_REDACTOR.redact(prompt)` before storage, same as
every other LLM call site — `ContextRedactor` strips anything that looks like
a secret before it's persisted.

## Gotchas

- **`Edit` tool needs a same-session `Read`, not just a grep/Bash view.** A
  file that was only ever inspected via `Grep`/`Bash` in this session will
  fail `Edit` with "file has not been read yet" — even if you've seen its
  contents. Call `Read` on the target range first, *then* `Edit`.
- **The 171 `RuntimeError: Runner...` errors and the 8
  `tests/ui/test_approval_ui.py` failures are pre-existing, not R1
  regressions.** Verified by `git stash` + re-running the same selection on
  clean `main` — identical counts both times. See
  `tasks/lessons.md` (2026-06-14 entries) for the technique and
  `tasks/todo.md` §8d Step 5 for the numbers. Don't try to "fix" these as
  part of an R1-shaped change; they're a separate pytest-asyncio
  runner-state-pollution issue between `tests/ui/*` and `tests/web/*`, and a
  missing-seed-user issue in `test_approval_ui.py`.
- **`_parse_action_types` and `_INJECTION_RE` are still in `decisions.py`** —
  they're general-purpose utilities used directly by
  `tests/ai_evals/test_adversarial.py` and `test_golden_plans.py` to test
  injection detection and unknown-action-type filtering. R1's acceptance
  criterion #2 ("no path where LLM output influences plan content") is
  satisfied because `prioritize_actions`'s *return value* no longer depends
  on them — not because they were deleted. Don't remove them as "now unused";
  grep their call sites in `tests/` first.

## Code map

| Piece | Where |
|---|---|
| Deterministic plan | `errander/agent/decisions.py::prioritize_actions` / `_hardcoded_priority` |
| Advisory note generation | `errander/agent/decisions.py::generate_planning_note` |
| Note schema / sanitization | `errander/agent/decisions.py::_PlanningNote`, `_sanitize_note`, `_PLANNING_NOTE_MAX_CHARS` |
| Note prompt | `errander/agent/decisions.py::_build_planning_note_prompt` |
| Plan node wiring | `errander/agent/graph.py::plan_vm_node` |
| Web rendering | `errander/web/ui.py::_render_approval_plan` (`.apv-ai-note*` CSS) |
| Slack rendering | `errander/agent/graph.py::_format_plan_for_approval` |
| Replay assertion | `errander/evals/replay.py::_check_planning_note` |
| F2 regression test | `tests/ai_evals/test_golden_plans.py::TestGoldenPlanSafety::test_planning_note_llm_output_never_changes_plan` |
| Audit outcome tests | `tests/ai_evals/test_golden_plans.py::TestPlanningNoteAudit` |

## Quiz yourself

1. Why is `prioritize_actions` still `async def` if it no longer awaits
   anything?
2. `generate_planning_note` takes `plan: list[Action]` as an argument. Why
   does that parameter — by itself — make F2 (silent plan shrinkage)
   structurally impossible, regardless of what the LLM returns?
3. `_sanitize_note` strips backticks at generation time, but `_render_approval_plan`
   also calls `_esc()` at render time. Why both? What would go wrong if you
   removed either one?
4. `ai_note` is stored inside `vm_plans`, which is hashed into `plan_hash`.
   What audit guarantee would break if `ai_note` were instead stored
   alongside `vm_plans` but outside the hashed structure?
5. In `_format_plan_for_approval`, the AI-analysis section is appended *last*,
   after the approval instructions. If Slack truncates the message at ~2800
   chars, what's the worst that can be cut off — and what's protected from
   ever being cut off?
6. `tests/ai_evals/test_adversarial.py` still imports `_INJECTION_RE` and
   `_parse_action_types` from `decisions.py` even though `prioritize_actions`
   no longer calls either. Why weren't they deleted?
