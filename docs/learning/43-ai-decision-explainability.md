# 43 — AI Decision Explainability (Phase 1 of AI Trust Layer)

## What was built and why

Every call to the LLM that influences a maintenance decision was already persisted in `ai_decisions` (SQLite). But operators had no way to inspect those records — no CLI flags, no Web UI page. This feature surfaces the audit trail so operators can answer: "What did the LLM recommend for this batch? With what confidence? What prompt was used?"

Phase 1 adds three surfaces:
1. **CLI** — `--ai-decisions` (list) and `--ai-decision-show <ID>` (detail)
2. **Web UI** — `/ui/ai-decisions` and `/ui/ai-decisions/{id}`
3. **Adversarial tests** — 25 tests proving injection cannot reach Layer B

## Key concepts

### `AIDecision` and `AIDecisionStore`

`AIDecisionStore` (`errander/safety/ai_audit.py`) is an async SQLite store. Every `prioritize_actions()` call logs one row: model, base URL, prompt template, SHA-256 hash of the full rendered prompt, raw JSON response, outcome (`success`/`fallback`/`no_llm`/`error`/`timeout`), and latency.

Before this change `_SELECT_SQL` didn't include `id` (the PK column). Adding it as the 17th column exposed the row's PK to Python as `decision_id`, enabling lookup by ID and deep-linking from Web UI.

### `get_decision_by_id()` — `Iterable` vs `list` gotcha

`aiosqlite.execute_fetchall()` is typed as returning `Iterable[Row]`. Indexing `rows[0]` fails mypy strict. The fix:

```python
rows = list(await db.execute_fetchall(f"{_SELECT_SQL} WHERE id = ? LIMIT 1", (decision_id,)))
return _row_to_decision(rows[0]) if rows else None
```

### Two-layer injection defence

The `_INJECTION_RE` regex catches payloads that contain shell metacharacters:

```
[;&|`$(){}\\\n]|\.\.\/
```

It does NOT catch arbitrary out-of-scope commands like `kubectl delete pod --all` (no metacharacters). Those are caught by `_parse_action_types()`, which rejects any string that doesn't match a known `ActionType` enum value. The two filters are complementary, not redundant — tests must cover both.

### Web UI — `web.AppKey` pattern

The Web UI uses aiohttp's `web.AppKey` to inject the store into route handlers without global state:

```python
_AI_DECISION_STORE_KEY: web.AppKey[AIDecisionStore | None] = web.AppKey("ai_decision_store")

# In start_metrics_server():
app[_AI_DECISION_STORE_KEY] = ai_decision_store

# In a route handler:
store: AIDecisionStore | None = request.app.get(_AI_DECISION_STORE_KEY)
```

The store is created at `async_main()` scope (not per-batch), so the Web UI can query it while a batch is running. SQLite allows concurrent readers.

### `base_url` redaction

The LLM endpoint base URL is redacted to host-only in both CLI and Web UI output:

```python
def _redact_base_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url
```

This avoids leaking path segments or query strings that might contain credentials.

## Code walkthrough

**`ai_audit.py`** — `_SELECT_SQL` appends `, id` as the 17th column. `_row_to_decision()` maps `row[16]` to `decision_id`. `get_decision_by_id()` is a one-row lookup.

**`main.py`** — Three new flags: `--ai-decisions` (list), `--ai-decision-show ID` (detail), `--decision-type` (filter). The dispatch block in `async_main()` checks `args.ai_decisions or args.ai_decision_show is not None` and calls `run_ai_decisions_query()`, which re-uses the existing `--last`, `--batch-id`, `--vm-id` flags. `base_url` is redacted via `urlparse().netloc` for display.

**`metrics.py`** — `_ui_ai_decisions()` queries `store.get_decisions(limit=100)` and renders a table with an ID column deep-linked to the detail page. `_ui_ai_decision_detail()` fetches by ID, shows metadata + context_snapshot + prompt_full + response_raw in scrollable `<pre>` blocks. `model_params` JSON is parsed to extract `temperature` for display.

**`tests/ai_evals/test_adversarial.py`** — three test classes:
- `TestSREAdversarialPayloads` — 9 tests: 6 shell-metacharacter payloads that must trigger `_INJECTION_RE`, 3 out-of-scope commands that must be rejected by `_parse_action_types()`, 6 plain-text strings that must NOT trigger the regex
- `TestLLMExceptionFallback` — 4 tests: `APITimeoutError`, `APIConnectionError`, no client, tier ordering
- `TestAuditOutcomesOnErrors` — 3 tests: `no_llm`, `fallback`, injection outcomes

## Gotchas

- `APIConnectionError` takes `message=` and `request=`, NOT `body=`. `body=None` is for `APIStatusError`.
- The `_vm()` factory in `test_adversarial.py` doesn't accept kwargs — call it bare: `_vm()`.
- Long inline CSS strings in `<pre>` blocks easily exceed the 120-char ruff limit. Extract the shared CSS to a variable and append `max-height` per block.

## Quiz yourself

1. Why does `_INJECTION_RE` not catch `kubectl delete pod --all`? Where is it caught instead?
2. Why must `execute_fetchall()` be wrapped with `list()` before indexing?
3. Why is `ai_decision_store_ui` created at `async_main()` scope rather than inside `run_env_batch()`?
4. What is the difference between `outcome="success"` and `outcome="fallback"` in `ai_decisions`?
