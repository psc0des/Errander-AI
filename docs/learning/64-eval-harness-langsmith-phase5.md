# 64 — Detect-and-Propose, Phase 5: the eval harness and LangSmith

## What was built and why

Phases 1-4 built a genuinely agentic system with real safety guardrails. Phase 5 answers
the question a skeptic asks next: *how do you know any of that actually works?* Two
independent, offline-by-default eval surfaces, plus opt-in tracing for when you want a
richer view of a real LLM's behavior.

1. **`errander/evals/golden_scenarios.py`** — does the deterministic detector propose
   the *right* thing for a *known* root cause, and only the right thing? Scored as
   set-based precision/recall over `(vm_id, action_type)` pairs.
2. **`errander/evals/agentic_guardrails.py`** — do the agentic loop's safety guardrails
   (built in Phase 2, exercised by Phase 3/4) actually hold when a model tries to cheat?
   Scripted adversarial scenarios run through the *real* `InvestigationAgent`.
3. **LangSmith wiring** — opt-in tracing for `LLMClient`, off by default, corrected
   from the original design doc's premise (see below).

As of shipping: **8/8 golden scenarios pass (100% precision, 100% recall)**, **4/4
guardrail scenarios pass**. Honest numbers, reproducible with one command:
`errander --eval-golden-scenarios`.

## Key concepts

### Two different kinds of "eval" — don't conflate them

The project already had `errander/evals/replay.py` (built earlier, AI Trust Layer
Phase 2): it re-sends a *stored, real* prompt to a *candidate* model and checks the
response's shape (schema, injection, unknown actions). That's **prompt regression** —
"does a new model still produce valid output for a prompt we've actually sent before?"

Phase 5's golden scenarios ask a different question entirely: **decision correctness**
— "given synthetic input with a *known correct answer*, does our code produce that
answer?" No model swap is involved; `detect_proposals()` is 100% deterministic Python.
Keeping these as separate modules (not merging into `replay.py`) matters because they
answer different questions with different failure modes — a replay-eval failure means
"the model's output format drifted"; a golden-scenario failure means "our own logic
regressed."

### Store-less by design — safe to run in production

`run_golden_scenarios()` takes an *optional* `proposal_store`. Without one, it only
exercises the pure `detect_proposals()` path — zero database writes, zero risk of
polluting real `agent_proposals` rows with fake scenario VMs. The one scenario that
needs to prove suppression works (`suppressed_pair_not_reproposed`) is correctly
*skipped* in that mode and only runs with a store the pytest suite supplies (bound to
the test database). This is why the CLI (`--eval-golden-scenarios`) never passes a
store: it must be safe to run against a live deployment's config without asking "wait,
did that just write test data into my real proposal queue?"

### Guardrail scenarios test the guardrail, not a reimplementation of it

The tempting-but-wrong design would be to write a *separate* function that "checks the
same rules" the guardrails enforce — that just doubles the surface area for the two
implementations to drift apart. Instead, `run_agentic_guardrail_scenarios()` drives the
*actual* `InvestigationAgent.investigate_agentic()` with a scripted fake LLM
(`_ScriptedLLM`, replaying a fixed turn sequence) and inspects the *real* output. If
someone ever weakens the citation-stripping logic in `_parse_final`, these scenarios
fail immediately — a true regression test, not a parallel opinion.

### A scorer that can't fail is worse than no scorer

Both new test files include a category the others didn't emphasize as explicitly:
tests that construct a *deliberately wrong* scenario and assert the harness correctly
flags it as failing. A precision/recall calculator that always reports 100% regardless
of input would pass every "the real scenarios are green" test forever while catching
nothing. `TestHarnessCatchesRegressions` (both files) proves the scoring math itself
works — set intersection/difference computed correctly, `passed` derived correctly from
non-empty false-positive/negative sets.

### LangSmith: the doc's original premise was wrong, and Phase 5 had to say so

`docs/OBSERVABILITY.md` §4 originally said LangSmith "attaches with no code changes via
env vars... because the decision engine is LangGraph." That's true for LangGraph
*nodes* — but Errander's actual Layer A reasoning (`OperatorAssistant`,
`InvestigationAgent`, the advisory planning-note/report calls) is hand-rolled OpenAI SDK
calls through `LLMClient`, never LangGraph nodes. LangGraph is used *only* for Layer B's
deterministic batch graph — which the design constraints explicitly forbid tracing.

So literally following the doc's stated mechanism ("just set the env vars") would trace
nothing useful for Layer A, or would require tracing Layer B, which is disallowed. The
real, correct mechanism — `langsmith.wrappers.wrap_openai()` — patches an OpenAI client
instance directly, no LangGraph required:

```python
def _maybe_wrap_for_tracing(client: AsyncOpenAI) -> AsyncOpenAI:
    try:
        from langsmith.utils import tracing_is_enabled
        if not tracing_is_enabled():
            return client
        from langsmith.wrappers import wrap_openai
        return wrap_openai(client, chat_name="errander-llm")
    except Exception as exc:
        logger.debug("LangSmith tracing wrap skipped: %s", exc)
        return client
```

`tracing_is_enabled()` is LangSmith's own canonical check — it reads the standard
`LANGSMITH_TRACING`/legacy `LANGCHAIN_TRACING_V2` env vars, so **the env-var-only
activation experience the doc promised is preserved** even though the underlying
mechanism needed correcting. `langsmith` itself ships as a transitive dependency of
`langchain-core`/`langgraph` (both already required for Layer B) — no new dependency,
and the whole thing degrades to an untraced client on any failure, because tracing is
observability, never load-bearing.

### Why wrapping `LLMClient` doesn't cross the Layer-A/B boundary

`LLMClient` is used by `agent/decisions.py` (the advisory planning note / report
generator — text-only, R1: never gates plan membership), `operator_assistant.py`,
`investigation_agent.py`, and `investigation_trigger.py`. It is **never** imported by
any execution sub-graph or the SSH executor — verified with a grep across
`agent/subgraphs/` and `execution/` before wiring anything (zero matches). Even though
the planning note happens to be *called from* a node inside the Layer B batch graph, the
call itself only produces text — it never decides what executes or how. Wrapping
`LLMClient` uniformly traces every Layer A reasoning call regardless of which entry
point invoked it, without ever putting a tracer in the execution path itself.

## Code walkthrough

- `golden_scenarios.py` — `GoldenScenario` (synthetic report + expected outcome sets +
  optional `pre_rejected` pairs), `ScenarioResult`/`GoldenEvalSummary` (set-based scoring,
  `skipped` is a first-class outcome distinct from pass/fail), `default_scenarios()` (8
  fixtures), `run_golden_scenarios()` (branches on whether a store was supplied).
- `agentic_guardrails.py` — `_ScriptedLLM` (replays a fixed `AssistantTurn` sequence),
  `_NoOpFallback` (same pattern as Phase 3's trigger fallback — zero-cost, never a real
  investigation), `AgenticGuardrailScenario` (turns + forbidden/expected sets),
  `run_agentic_guardrail_scenarios()` (drives the real loop, diffs the output against
  the scenario's assertions).
- `integrations/llm.py` — `_maybe_wrap_for_tracing()`, called once in `__init__`.
- `main.py` — `run_eval_golden_scenarios()` mirrors `run_ai_eval_replay()`'s structure
  (print header, run, report, exit code reflects pass/fail); `--live-llm` reuses Phase
  3's `NoOpFallback` for a cheap, unscored connectivity smoke test.

## Gotchas encountered

**A Windows WMI subsystem stall, not a code bug, blocked verification mid-session.**
While confirming the CLI worked, repeated invocations started hanging. Bisection (`--help`
also hung; the hang predated any `print()`; `-X importtime` showed the stall right after
`platform`/`_wmi`; `ruff`/`mypy` — neither of which imports `errander.*` at runtime —
worked fine) isolated it to Python's own `platform.win32_ver()` call (triggered by
`sqlalchemy`'s import-time OS detection) blocking on a WMI query that had nothing to do
with anything written this phase. Restarting the `Winmgmt` service resolved it. The
lesson generalizes: when something that touched zero of your changes (`--help`) also
breaks, stop looking in your diff — profile the failure at the interpreter/OS level
before assuming a logic bug (`docs.pytest_import_time`/`-X importtime` for Python;
compare a binary tool in the same toolchain, like `ruff`, that *doesn't* hit the
suspect code path, as a control).

## Quiz yourself

1. Why does `run_golden_scenarios()` accept an *optional* store rather than always
   constructing one internally?
2. The `uncited_tool_evidence_stripped` scenario scripts the model as *actually calling*
   `get_audit_events` before answering. Why does that matter — what would change if the
   scenario never called any tool at all?
3. Why do `TestHarnessCatchesRegressions` tests exist in both new test files, and what
   specifically would slip through if they didn't?
4. Trace exactly what env var(s) an operator needs to set to get LangSmith traces for
   the deterministic `--ask` path (not `--ask --agentic`). Does the mechanism differ
   between the two?
5. Why is `replay.py`'s existing eval infrastructure *not* the right place to add
   decision-correctness scoring, even though both live under `errander/evals/`?

## References

- Plan: `tasks/fable-plan.md` §3 Phase 5 (includes the LangSmith mechanism-correction note).
- Corrected design doc: `docs/OBSERVABILITY.md` §4.
- Prior phases: `docs/learning/60-63-*.md`.
- Existing prompt-regression harness (distinct purpose): `errander/evals/replay.py`,
  `docs/learning/45-replay-evals.md`.
