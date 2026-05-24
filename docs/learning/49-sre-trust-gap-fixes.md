# 49 — SRE Trust Gap Fixes: Centralized Redaction, OA Audit, Evidence Validation

## What was built and why

An SRE review of the AI Trust Layer (Phases 1–6a) found four trust gaps where new safety controls weren't fully wired in:

1. **Redaction not applied to all LLM paths** — `decisions.py` had three LLM call sites (prioritize, analyze_failure, generate_report) that sent raw prompts to the model. The `ContextRedactor` from Phase 3 was only wired into `OperatorAssistant`.
2. **OperatorAssistant decisions not audited** — `investigate()` made LLM calls with no `AIDecisionStore` logging. Operators could see batch decisions via `--ai-decisions` but not ask-query decisions.
3. **LLM-returned evidence IDs not validated** — the model returns source IDs in `finding.evidence`. Without validation, hallucinated IDs mislead operators about what data a finding is based on.
4. **Pre-existing ruff/mypy errors in the codebase** — not introduced by AI Trust Layer work, tracked as debt.

---

## Key concepts

### Belt-and-suspenders redaction pattern

```
decisions.py call site:
  prompt = _build_prompt(...)
  prompt, _rc = _REDACTOR.redact(prompt)      ← strip at call site
  prompt_full = prompt                         ← store already-redacted
  await llm.complete(prompt, ...)

LLMClient.complete():
  prompt, _rc = _REDACTOR.redact(prompt)      ← final backstop
  if _rc: logger.warning("...")
```

Two distinct risks require two independent guards:
- **Secret leaks to the model** — caught by either layer
- **Secret leaks to the audit DB** — caught at the call site (since `complete()` only sees the prompt string, not the audit record)

### Optional audit store pattern

```python
async def investigate(
    self,
    context: FleetContext,
    question: str,
    llm_client: LLMClient | None = None,
    ai_decision_store: AIDecisionStore | None = None,  ← optional
) -> AssistantResponse:
    t0 = time.monotonic()
    result = await llm_client.complete(prompt, AssistantResponse)
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    outcome = "success" if result is not None else "fallback"

    if ai_decision_store is not None:
        await ai_decision_store.log(AIDecision(
            decision_type="operator_assistant",
            batch_id="ask",
            outcome=outcome,
            latency_ms=latency_ms,
            prompt_full=prompt,
            ...
        ))
```

Keeping the store optional means the function works in tests and CLIs that don't open a DB. The `run_ask_query()` handler in `main.py` opens the store and passes it.

### Evidence ID validation

```python
valid_sources = set(context.sources_used)
if valid_sources:
    for finding in result.findings:
        invalid = [e for e in finding.evidence if e not in valid_sources]
        if invalid:
            logger.warning("LLM cited unknown source(s) %s — removing", invalid)
            finding.evidence = [e for e in finding.evidence if e in valid_sources]
```

`context.sources_used` is populated by `FleetContext.build()` from the actual data sources that were queried. It is the ground truth of what the model is allowed to cite.

---

## Files changed

| File | Change |
|---|---|
| `errander/integrations/llm.py` | `_REDACTOR = ContextRedactor()` at module level; redaction in `complete()` |
| `errander/agent/decisions.py` | `_REDACTOR` at module level; redact in `prioritize_actions`, `analyze_failure`, `generate_report` |
| `errander/agent/operator_assistant.py` | `ai_decision_store` param; timing; audit log; evidence validation |
| `errander/main.py` | Open `AIDecisionStore` for `--ask` queries; pass to `investigate()`; `finding.text` print fix |
| `tests/agent/test_decisions.py` | `TestRedactionInDecisionPaths` (4 tests) |
| `tests/agent/test_operator_assistant.py` | 5 audit + evidence validation tests |

---

## Gotchas

- **`AIDecisionStore` in `TYPE_CHECKING` only** — importing it at runtime in `operator_assistant.py` would create a circular import. Use `from __future__ import annotations` + `if TYPE_CHECKING:` and annotate as `"AIDecisionStore | None"`.
- **`prompt_full` must store the already-redacted prompt** — if you redact after storing, you've already leaked to the DB. Redact first, then pass the same string to both `complete()` and the audit store.
- **`context.sources_used` may be empty** — if no data sources were queried (e.g., offline mode), skip evidence validation entirely rather than stripping all evidence IDs.

---

## Tests

```
tests/agent/test_decisions.py::TestRedactionInDecisionPaths
  test_prioritize_actions_redacts_prompt_before_llm
  test_prioritize_actions_prompt_full_is_redacted
  test_analyze_failure_redacts_error_context
  test_generate_report_redacts_secrets_in_results

tests/agent/test_operator_assistant.py
  test_investigate_logs_decision_when_store_provided
  test_investigate_logs_fallback_outcome_when_llm_fails
  test_investigate_no_log_when_no_store
  test_investigate_strips_unknown_evidence_ids
  test_investigate_preserves_valid_evidence_ids
```

---

## Quiz

1. Why do we need redaction both at the call site AND inside `LLMClient.complete()`?
2. What is the difference between `prompt_full` leaking to the audit DB vs leaking to the LLM?
3. Why is `ai_decision_store` optional rather than required?
4. What happens when `context.sources_used` is empty? Why?
