# 60 — Layer A Investigation Agent (Plan A)

## What was built and why

`OperatorAssistant.investigate()` (the engine behind `--ask`) makes exactly
**one** LLM call: Python pre-gathers a fixed set of signals (audit events,
disk trend, drift, optional Prometheus/ELK), stuffs them into a prompt, and
asks the LLM to synthesize findings. That's reproducible and cheap, but it
can't chase a *novel* question — if answering "is app X spewing errors?"
needs a query nobody pre-wrote, the data simply isn't there (see
`docs/OBSERVABILITY.md` → "the fixed signal menu").

This feature adds a second, **opt-in** investigation path: `--ask --agentic`
gives the LLM a small set of read-only tools and a budget, and lets it decide
which queries to run, observe results, and iterate (a bounded ReAct loop)
before answering. The scheduled maintenance batch (`prioritize_actions`) and
the *default* `--ask` are completely untouched — this is new, parallel code,
not a rewrite.

Both paths are Layer A: read-only, recommendations only, never executes.
Default is **off** (`ERRANDER_INVESTIGATION_AGENT_ENABLED=false`); when off,
or when anything goes wrong, behavior is identical to today.

## Key concepts

### Two engines, one contract

```python
# errander/agent/operator_assistant.py — unchanged, one LLM call
async def investigate(self, question: str, *, audit_store, ..., llm_client=None, ...) -> AssistantResponse

# errander/agent/investigation_agent.py — new, bounded tool-calling loop
async def investigate_agentic(self, question: str, *, audit_store, ..., llm_client=None, ...,
                                max_tool_calls: int = 8, timeout_seconds: int = 180) -> AssistantResponse
```

`investigate_agentic` takes the **same** store/client kwargs as `investigate`,
plus two budget params. `errander/main.py::run_ask_query()` constructs the
identical dependency set either way and just picks which method to call
based on `--agentic` + `ERRANDER_INVESTIGATION_AGENT_ENABLED`. Both return
the same `AssistantResponse` (`errander/models/analysis.py`) — summary,
cited findings, recommendations, risk level — so callers (the CLI today, the
planned dashboard chat later) don't care which engine answered.

### The loop, and the timeout-shrinking detail that actually matters

```python
while True:
    elapsed = time.monotonic() - start_time
    if elapsed >= timeout_seconds:
        return await _fallback("budget_exhausted")
    remaining = max(int(timeout_seconds - elapsed), _MIN_CALL_TIMEOUT_SECONDS)

    call_tools = tools if total_tool_calls_made < max_tool_calls else []
    result = await llm_client.complete_with_tools(messages, call_tools, timeout_seconds=remaining)
    ...
```

`timeout_seconds` (default 180) is the **overall loop deadline** —
deliberately bigger than `LLMClient`'s own 60s **per-call** timeout. If both
were 60s, the loop would get roughly one hop on the self-hosted vLLM/T4 path
before its budget vanished, defeating the entire feature. Each hop gets a
**shrinking** per-call timeout (`remaining = deadline - elapsed`, floored at
10s) so a slow early hop can't itself blow the whole budget before the
loop-level check ever fires — and that `elapsed` clock runs across both LLM
calls *and* tool HTTP calls (Prometheus/ELK have their own 5s timeouts), so
tool latency counts against the budget too.

### Forcing a final answer without tripping a provider 400

Once `total_tool_calls_made >= max_tool_calls`, `call_tools` becomes `[]` and
a "tool budget exhausted, answer now" message is appended. The naive version
of this — `complete_with_tools(messages, [])` — sounds right but isn't: most
OpenAI-compatible endpoints **reject a literal empty `tools` array with a
400**. `LLMClient.complete_with_tools()` handles this by omitting
`tools=`/`tool_choice=` from the request entirely when the list is empty:

```python
extra: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
response = await self._client.chat.completions.create(
    model=self._model, messages=messages, temperature=self._temperature,
    timeout=effective_timeout, **extra,
)
```

This was a real bug caught during implementation, not a hypothetical: the
first draft of the loop computed `call_tools` correctly but then called
`complete_with_tools(messages, call_tools or tools, ...)` — and `[] or tools`
evaluates to `tools`, silently re-offering the *full* tool list right after
"exhausting" the budget. `tests/agent/test_investigation_agent.py::test_max_tool_calls_cap_forces_empty_tools_final_answer`
asserts `llm.calls[1]["tools"] == []` specifically to pin this down.

### Citation: the model can only cite what it actually saw

`operator_assistant.py` validates `finding.evidence` against
`context.sources_used` — IDs from a context object built *before* the LLM
runs. The agentic loop has no such pre-built object; sources are discovered
live. The naive fix — validate against an internally-tracked `sources_used`
list the model never sees — silently strips **every** citation, because the
model has no way to guess a synthetic ID like `"query_prometheus#3"`. The
fix is to make the ID visible:

```python
sources_used.append(source_id)
messages.append({
    "role": "tool", "tool_call_id": tc.id,
    "content": f"[source_id={source_id}]\n{redacted}",
})
```

The system prompt explicitly tells the model to cite findings using the
exact bracketed `source_id` from a tool result it received. Validation then
strips anything *not* in `sources_used` — catching genuine hallucination
without erasing every real citation.
`test_multihop_success_keeps_real_citation_strips_hallucinated` locks in
both halves: a finding citing a real, emitted source_id keeps its evidence;
one citing a made-up source_id gets stripped.

### Capability detection: a quiet wrong answer is worse than no answer

Two distinct failure shapes both have to trigger fallback, not just one:

1. **`APIStatusError`** on the very first call — the endpoint rejected
   `tools=` outright (often a 400). Reason: `"unsupported"`.
2. **Zero `tool_calls` on hop 0**, even with a parseable final answer. An
   endpoint that silently *ignores* `tools=` returns a normal completion
   with no tool calls — and because the agentic path starts with **no
   pre-gathered context** (unlike the deterministic path), that "answer" is
   content-free and *worse* than the deterministic fallback would have been.
   Reason: `"empty_turn1"`.

```python
if result is None:
    return await _fallback("unsupported" if hop == 0 else "llm_down")
if hop == 0 and not result.tool_calls:
    return await _fallback("empty_turn1")
```

`test_empty_turn1_falls_back_even_with_a_parseable_answer` is the regression
test for case 2 — it deliberately gives the fake LLM a well-formed,
parseable `AssistantResponse` on hop 0 with no tool calls, and asserts the
loop **discards** it in favor of the deterministic fallback rather than
returning the tempting-looking shortcut.

### Per-hop audit: log the delta, not the transcript

```python
delta_text = json.dumps({"hop": hop, "tool": tool_name, "arguments": arguments_json, "source_id": source_id})
await ai_decision_store.log(AIDecision(..., prompt_full=delta_text, ...))
```

`AIDecision.prompt_full` stores **one hop's new tool call + its result**,
never the growing message history. Logging the cumulative transcript on
every hop would make `prompt_full` grow quadratically with loop length (hop
8 would contain all of hops 1–7's tool results too) — `prompt_full` stays a
small, flat record regardless of how many hops preceded it. One audit row
per tool call dispatched, not one per LLM turn.

### Never raise — three layers of safety net

1. Per-tool: `_dispatch_tool()` catches any exception from a tool handler
   and returns it as error *text* (`f"Error: tool '{name}' failed: {exc}"`),
   never lets it propagate.
2. Per-loop: the whole `while True:` body is wrapped in
   `try/except Exception` → `_fallback("budget_exhausted")`.
3. Terminal: `_fallback()` itself calls the deterministic
   `OperatorAssistant.investigate()`, which has its own long-standing
   "LLM unavailable → deterministic summary" fallback. So even a
   double failure (agentic loop *and* the fallback's own LLM call) bottoms
   out in a template-rendered response, never an exception to the CLI.

## Tools (all read-only, all capped)

| Tool | Wraps | New caller-facing surface |
|---|---|---|
| `query_prometheus` | `PrometheusClient.query()` (new) | Arbitrary PromQL — alongside the existing fixed `fetch_vm_metrics()`, untouched |
| `search_logs` | `ElkClient.search()` (new) | Arbitrary terms — alongside the existing fixed `fetch_vm_errors()`, untouched |
| `get_audit_events` | `AuditStore.get_events()` (existing) | `limit` hard-capped at 200 regardless of request |
| `get_disk_trend` | `VMDiskHistoryStore` (existing) | `window_days` capped at 30 |
| `get_vm_facts` | `VMFactsStore` (existing) | — |
| `list_inventory` | `InventoryConfig` (existing) | Hostnames/names only — never `ssh_user`/`ssh_key_path` |

`query()` and `search()` are new sibling methods on the existing clients —
the fixed methods the batch path uses (`fetch_vm_metrics`, `fetch_vm_errors`)
are byte-for-byte unchanged. Both new methods enforce the same rule: caller
input may only shape *query/filter content*, never the URL path or index
(`search()` always posts to the configured index pattern's `_search`
endpoint; `query()` always hits `/api/v1/query[_range]`).

## Gotchas

- **`call_tools or tools` looks harmless and isn't.** Any time a "use X
  unless exhausted, else use empty" pattern is written as
  `empty_value or fallback`, double-check whether `empty_value` itself is
  the intentional terminal state — `[]`, `0`, and `""` are all falsy in
  Python, so `or` silently discards them.
- **`asyncio.Semaphore(1)`** wraps `complete_with_tools()` at module scope in
  `llm.py` (CLAUDE.md: "sequential LLM calls preferred" on self-hosted
  endpoints) — concurrent dashboard-chat threads (Plan B) will serialize
  through this, by design, not a bug to "fix" later.
- **mypy + the OpenAI SDK's tool-call union type.** `message.tool_calls` is
  typed as a union of function-type and custom-type calls; only the
  function-type variant has `.function`. Since this codebase only registers
  function tools, the fix is a `tc.type == "function"` filter in the list
  comprehension — not a `# type: ignore`.
- **`MagicMock(name=...)` does not set `.name`.** It's a reserved Mock
  constructor kwarg for the mock's own repr. Tests building fake inventory
  targets must set `target.name = "..."` as a separate statement after
  construction — this exact gotcha exists in the pre-existing
  `test_operator_assistant.py` fixtures too; don't copy it forward.

## Code map

| Piece | Where |
|---|---|
| Agentic loop + tool registry | `errander/agent/investigation_agent.py` |
| Tool-calling primitive | `errander/integrations/llm.py::LLMClient.complete_with_tools` |
| Arbitrary Prometheus query | `errander/integrations/prometheus.py::PrometheusClient.query` |
| Arbitrary ELK search | `errander/integrations/elk.py::ElkClient.search` |
| Settings | `errander/config/settings.py` (`investigation_agent_*`, 3 fields) |
| CLI flag + dispatch | `errander/main.py` (`--agentic`, `run_ask_query`) |
| Metrics | `errander/observability/metrics.py` (`INVESTIGATION_TOOL_CALLS_TOTAL`, `INVESTIGATION_FALLBACK_TOTAL`) |
| Layer-A isolation test | `tests/agent/test_investigation_agent_isolation.py` |
| Loop tests | `tests/agent/test_investigation_agent.py` |
| Tool-handler tests | `tests/agent/test_investigation_tools.py` |

## Quiz yourself

1. Why must `timeout_seconds` (the overall loop deadline) be *larger* than
   `LLMClient`'s per-call timeout, not equal to or smaller than it?
2. `complete_with_tools(messages, [])` omits `tools=` from the request
   instead of sending `tools=[]`. What would happen against a real
   OpenAI-compatible endpoint if it sent the literal empty array instead?
3. Why does embedding `[source_id=...]` in the tool result message — not
   just tracking `sources_used` internally — matter for the citation
   validation to work at all?
4. `test_empty_turn1_falls_back_even_with_a_parseable_answer` deliberately
   gives the fake LLM a *valid*, parseable answer on hop 0 with zero tool
   calls — and asserts the loop throws it away. Why is discarding a
   perfectly well-formed answer the correct behavior here?
5. The per-hop `AIDecision.prompt_full` stores `{hop, tool, arguments,
   source_id}` — never the message list. What audit-DB problem would
   appear, and at what hop count, if it logged the cumulative `messages`
   instead?
6. `investigate_agentic` and `investigate` take an almost-identical kwarg
   list. What does that similarity buy `run_ask_query()` — and, looking
   ahead, what does it buy the not-yet-built dashboard chat (Plan B)?
