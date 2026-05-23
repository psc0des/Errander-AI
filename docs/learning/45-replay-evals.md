# 45 — AI Trust Layer Phase 2: Prompt Versioning & Replay Evals

## What was built and why

Phase 2 adds a replay evaluation harness for the AI trust layer. The idea: every LLM call already records `prompt_full` in `ai_decisions` (Phase D1). Now we can re-send those stored prompts to a candidate model and run deterministic assertions on the output — without needing a human to judge each response.

This matters for model upgrades: before switching from `qwen3-8b` to a newer model, run `--ai-eval-replay` against the last N stored decisions. Any response that fails schema checks, introduces injection strings, or recommends unknown action types is flagged as a `fail`.

The eval is **Layer A only** — it reads from stores and sends prompts, but never writes to target VMs.

## Key concepts

### Deterministic assertions (no LLM judge)

`check_assertions(decision_type, response_raw)` dispatches to one of four checkers:

| decision_type | Checker | What it validates |
|---|---|---|
| `prioritize_actions` | `_check_prioritize` | `action_types` list exists, all items are known, no injection chars, no legacy actions |
| `failure_analysis` / `analyze_failure` | `_check_failure_analysis` | `recommendation` in `{retry, rollback, escalate}`, `reason` present |
| `report` / `generate_report` | `_check_report` | `report` field exists and non-empty |
| anything else | `_check_operator_assistant` | `summary`, `findings`, `recommendations`, `risk_level` all present; risk_level valid |

Violations are returned as strings like `"unknown_action:'nuke_everything'"` or `"injection:'disk_cleanup; rm -rf /'"`— the format encodes both the violation type and the offending value.

### `_RawResponse` — accepting any JSON

The replay runner creates an ad-hoc Pydantic model to capture whatever the candidate model returns:

```python
class _RawResponse(BaseModel):
    model_config = {"extra": "allow"}
```

`extra = "allow"` means any JSON field is accepted without schema errors. The result is then serialized back to a JSON string via `model_dump_json()` and passed to `check_assertions()`. This round-trip (string → Pydantic → string) normalizes the response to a predictable format.

### EvalStore and migration 9

Two new tables in the same SQLite DB:

- `ai_eval_runs`: one row per `--ai-eval-replay` invocation. Stores model, decision_type filter, pass/fail/error counts, timestamp.
- `ai_eval_results`: one row per replayed decision. Stores the original `ai_decisions.id`, outcome, violations (JSON array), raw response, latency.

The `original_id` column is a soft FK — no enforced constraint — so the eval DB can be separate from the audit DB if needed.

## Code walkthrough

### `errander/evals/replay.py`

**`check_assertions()`** — the entry point. Returns `[]` on pass, a list of violation strings on fail.

`None` response → `["no_response"]` (treated as an error, not a violation, but `check_assertions` is symmetric — callers check the list regardless).

**`run_replay()`** — the orchestrator:
1. Query `ai_store.get_decisions(decision_type=..., limit=...)` for decisions with `prompt_full`.
2. For each: if `prompt_full` is None → `outcome="skipped"`.
3. Otherwise: call `candidate_client.complete(prompt_full, _RawResponse)`.
4. If result is None (LLM error) → `outcome="error"`.
5. Otherwise: serialize with `model_dump_json()`, run `check_assertions()`, set `outcome="pass"` or `"fail"`.
6. Collect `EvalResult` per decision, sum counters, build `EvalRun`, save to `eval_store`.

**`EvalStore`** follows the same `async with` / `initialize()` / `close()` pattern as `AIDecisionStore` and `AuditStore`. It shares the same migration framework.

### `errander/main.py` — CLI wiring

```bash
uv run python -m errander --ai-eval-replay
uv run python -m errander --ai-eval-replay --eval-model qwen3-8b --decision-type prioritize_actions
```

`run_ai_eval_replay()` constructs an `LLMClient` with `candidate_model` (from `--eval-model` or `settings.llm_model`), then opens both stores as context managers and calls `run_replay()`. Uses parenthesized `async with` syntax for combined contexts (avoids ruff `SIM117`).

## Tests

`tests/ai_evals/test_replay.py` — 28 tests in three classes:

**`TestCheckAssertions`** (18 tests): unit tests for every assertion branch — valid pass, missing field, injection detection, unknown action, legacy action, mixed violations, invalid recommendation, missing reason, missing report, empty report, invalid risk level, None response, bad JSON, JSON-not-object.

**`TestEvalStore`** (4 tests): save-and-retrieve roundtrip, no-results case, multiple runs ordered newest-first, unknown run ID returns empty.

**`TestRunReplay`** (6 tests): clean candidate all-pass, violation detected and surfaced in results, no-prompt_full skipped, LLM None → error, empty store → zero results, run persisted to EvalStore.

The `_mock_llm(response)` factory creates a `MagicMock` with `AsyncMock` for `complete()`. The mock returns a Pydantic `BaseModel(extra="allow")` instance pre-loaded with the test's JSON fields — mirroring exactly what `run_replay()` does at runtime.

## Gotchas

**`SIM117` — nested `async with`**: ruff flags directly nested `async with` blocks with no code between them. Use parenthesized form:
```python
async with (
    AIDecisionStore(":memory:") as ai_store,
    EvalStore(":memory:") as eval_store,
):
```
Or the single-line form if there's only one intermediate statement between them — ruff only flags the truly trivial nesting.

**`violation string format uses `!r`**: `f"unknown_action:{item!r}"` adds quotes around the item — `unknown_action:'kubectl_delete_all'`, not `unknown_action:kubectl_delete_all`. Test assertions must check for both the violation type and the item string using `and`, not concatenation.

**Migration count tests break**: `tests/safety/test_migrations.py` checks exact version counts and table lists. Adding migration 9 broke two tests. Pattern: always update this file when adding a migration.

## What comes next (Phase 4)

Operational Memory Confidence: add a `confidence` field to `ActionOutcomeFact`, `VMRebootPatternFact`, `ActionRejectionFact` in `vm_facts.py`. The operator assistant can then label findings with confidence levels in the prompt, giving the LLM better calibration signals.
