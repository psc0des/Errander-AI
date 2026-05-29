# Implementation Plan — Layer A Investigation Agent (agentic, read-only MCP/tool-calling)

> **Status:** proposed, not started. This is a build plan for a future implementation session (e.g. Sonnet). Read it top-to-bottom before writing code.
>
> **One-line goal:** upgrade the *open-ended* `--ask` investigation path so the LLM can **compose read-only queries on the fly** (Prometheus, ELK, audit, disk history, VM facts) instead of receiving a fixed, pre-gathered context — while staying strictly Layer A.

---

## 0. MANDATORY pre-flight (do this first, every session)

1. **Read these before touching code:**
   - `docs/AI-ARCHITECTURE.md` — the two-layer safety model. This whole feature lives in **Layer A**.
   - `docs/OBSERVABILITY.md` — esp. "What Errander can see — the fixed signal menu" (this feature loosens that menu for `--ask` only) and §4 (LangSmith).
   - `CLAUDE.md` → **AI Safety Invariant (MANDATORY)** and the Doc Sync Rule.
2. **Internalize the hard boundary (non-negotiable):**
   - This agent is **Layer A**: it investigates and recommends. It produces **text/recommendations only**.
   - It must **never** import or call `SandboxExecutor`, `FileLocker`, `ApprovalManager`, SSH execution, or any Layer B path.
   - Every tool it gets is **read-only**. No tool may mutate a target VM, a store, or any state.
   - Its output still flows to the human → deterministic Layer B for any actual change. The agent never executes.
3. **Confirm git identity** (`psc0des` / `sarathy.vass6@gmail.com`) per CLAUDE.md before the first commit.
4. **Doc-sync rule applies:** code + docs in one atomic commit. Update STATUS.md, docs/command-log.md, tasks/todo.md, tasks/lessons.md every session; plus README.md, OBSERVABILITY.md, and a `docs/learning/XX-investigation-agent.md` when the feature lands.

---

## 1. Why (the gap this closes)

Today, `OperatorAssistant.investigate()` (`errander/agent/operator_assistant.py`) gathers a **fixed** set of signals deterministically (Python runs pre-written queries), stuffs them into one prompt, and makes a single `LLMClient.complete()` call. Strengths: reproducible, auditable, cheap, fallback-safe. Limit: it **cannot chase a novel question** — if diagnosing "why is app X erroring?" needs a query no developer pre-wrote, the data simply isn't there (see OBSERVABILITY.md → fixed signal menu).

This feature adds an **agentic investigation mode**: the LLM is given read-only *tools* and a budget, and it decides which queries to run, observes results, and iterates — a ReAct-style loop. This is appropriate **only** for open-ended `--ask` investigation. The scheduled maintenance batch (`prioritize_actions`) **keeps** the deterministic fixed-menu gather — do not touch it.

---

## 2. Scope

**In scope**
- A new agentic investigation path for the `--ask` CLI flow (and the operator-assistant code path behind it).
- Read-only tools wrapping existing data sources.
- A bounded tool-calling loop with redaction, budget, audit, and graceful fallback.
- Opt-in via settings/flag; default OFF.

**Explicitly OUT of scope**
- Any change to Layer B (execution, approval, rollback, locking, audit-of-actions).
- Any change to the scheduled batch planner (`decisions.prioritize_actions`) — it stays deterministic.
- Any *write*-capable tool. No SSH-exec tool. No "apply fix" tool. Ever.
- Autonomous execution of any kind.

---

## 3. Architecture decision — the agent runtime

The repo standardizes on the **OpenAI SDK** (`errander/integrations/llm.py`, `LLMClient` → `AsyncOpenAI`) for provider-agnostic access ("any OpenAI-compatible endpoint"). Tool-calling must preserve that.

**Decision A — recommended: hand-rolled tool-calling loop on the existing OpenAI SDK.**
- Add a tool-calling method to `LLMClient` (or a sibling) that passes `tools=[...]` to `chat.completions.create`, reads `message.tool_calls`, dispatches to local Python tool fns, appends tool results as `role:"tool"` messages, and loops until the model returns a final answer or the budget is hit.
- **Why recommended:** keeps the single provider-agnostic client; gives us control to apply **redaction + budget + audit on every hop** (which is the entire safety value here); no new heavy dependency; works on self-hosted vLLM (CLAUDE.md vLLM serve already enables `--enable-auto-tool-choice --tool-call-parser hermes`).
- **Cost:** we write the loop (~100-150 lines) instead of getting it from a framework.

**Decision B — alternative: LangGraph `create_react_agent` + `langchain-openai` ChatOpenAI.**
- Pro: batteries-included ReAct loop, native LangSmith tracing, stays in the LangGraph family already used in Layer B.
- Con: adds `langchain-openai` dep; redaction/budget must be injected by **wrapping each tool's return value** and via a recursion/step limit; less direct control of per-hop audit; a second LLM-access abstraction alongside `LLMClient`.

**Recommendation:** start with **Decision A** (control + provider-agnostic + minimal deps). Revisit B only if the hand-rolled loop becomes unwieldy. Whichever is chosen, the guardrails in §5 are mandatory and identical.

> Implementer note: verify the target endpoint actually supports tool/function calling. If it does not (some endpoints/models don't), the feature must **fall back to the existing deterministic `investigate()`** — never error out. Detect via a capability flag in settings or a probe, and degrade gracefully.

---

## 4. Tools (all READ-ONLY)

Wrap existing read paths as tool functions with strict JSON schemas. Each tool: validates args, enforces caps, applies redaction to its return, and logs the call. Start with this set:

| Tool | Wraps | Args (validated) | Caps |
|---|---|---|---|
| `query_prometheus` | `integrations/prometheus.py` (generalize beyond the 3 fixed queries) | `promql: str`, optional `range` | only `GET /api/v1/query[_range]`; reject non-query paths; result rows capped; 5s timeout |
| `search_logs` | `integrations/elk.py` (generalize beyond `fetch_vm_errors`) | `host`, `query_terms`, `window_hours`, `level?` | only `_search` on the configured index pattern; size capped; 5s timeout |
| `get_audit_events` | `safety/audit.py` `AuditStore.get_events` | `batch_id?`, `vm_id?`, `event_type?`, `action_type?`, `limit` | `limit` hard-capped (e.g. ≤200) |
| `get_disk_trend` | `safety/disk_history.py` + `execution/disk_trend.compute_growth_alert` | `vm_id`, `window_days` | window capped |
| `get_vm_facts` | `safety/vm_facts.py` `VMFactsStore` | `vm_id` | — |
| `list_inventory` | inventory config | `env?` | returns hostnames/ids only |

**Future (separate PR, do not build now):** `github_recent_commits`, `cve_lookup`. Keep the tool registry extensible so adding a read-only source is "register a tool," not a rewrite.

**Tool safety rules (apply to every tool):**
- Read-only. No tool opens an SSH exec, writes a store, posts to Slack, or triggers Layer B.
- Arg validation: reject anything that smells like an endpoint-path injection (e.g. PromQL must be a query expression, not `/api/v1/admin/...`); restrict ELK to `_search`. Enforce allowlisted base URLs from settings.
- Tool **results are untrusted input** (ELK/Prometheus content can be attacker-influenced). Run every tool return through `ContextRedactor` before it re-enters the model, and cap size via `ContextBudgeter`. Because tools are read-only, worst case is a wrong textual answer — but cap sizes to prevent context blowup and bound cost.

---

## 5. Guardrails (MANDATORY — these are the reason the feature is safe)

1. **Layer-A isolation.** New module(s) must not import Layer B. Add a test asserting no forbidden imports (mirror the existing Layer-A contract in `operator_assistant.py` docstring/tests).
2. **Budget across hops.** Hard caps from settings: `max_tool_calls` (default e.g. 8), overall wall-clock `timeout_seconds`, per-tool result size, total context tokens. Loop **must** terminate at the cap and return the best answer so far (or fall back).
3. **Redaction every hop.** `ContextRedactor` on every prompt AND every tool result before it re-enters the model. (LLMClient already redacts prompts; tool results are the new surface.)
4. **Per-step audit.** Log every LLM hop and every tool call to `AIDecisionStore` (`safety/ai_audit.py`), e.g. `decision_type="investigation_agent_step"`, capturing tool name, validated args, result hash/summary, latency, outcome. N hops ⇒ N audit rows. This is the in-network record; LangSmith (if enabled) is the richer external view.
5. **Output contract unchanged.** The agent returns the existing `AssistantResponse` (`models/analysis.py`) — `summary` + `Finding`s with `evidence` citations, recommendations only, **no execution instructions**. Keep the existing source-citation validation (strip hallucinated evidence IDs; validate against sources actually used — here, the tools actually called).
6. **Graceful fallback (never block).** If tool-calling is unsupported, the LLM is unreachable, or the budget is exhausted with no answer → fall back to the deterministic `investigate()`. The agent must never raise to the operator.
7. **Egress note.** All tool calls are in-network (Prometheus/ELK/local stores). Only optional LangSmith tracing egresses — keep it off by default (see OBSERVABILITY.md §4).

---

## 6. Wiring

- **New module:** `errander/agent/investigation_agent.py` — the agentic loop + tool registry. (Alternatively an `investigate_agentic()` method on `OperatorAssistant`; prefer a separate module to keep the deterministic path clean.)
- **LLM tool-calling support:** extend `errander/integrations/llm.py` with a tool-calling method (Decision A) — keep `complete()` untouched for the batch path.
- **CLI:** `--ask` gains an opt-in `--agentic` flag (or honor a settings flag). Default = current deterministic behavior. `errander/main.py`.
- **Settings:** `errander/config/settings.py` + env loaders:
  - `ERRANDER_INVESTIGATION_AGENT_ENABLED` (default `false`)
  - `ERRANDER_INVESTIGATION_AGENT_MAX_TOOL_CALLS` (default 8)
  - `ERRANDER_INVESTIGATION_AGENT_TIMEOUT` (default 60)
- **Metrics (optional):** `errander_investigation_tool_calls_total{tool}` in `observability/metrics.py`.

---

## 7. Phasing (keep the tree green at every commit)

**Phase 1 — Read-only tools + tests (no agent yet).**
- Generalize `PrometheusClient` / `ElkClient` to accept arbitrary (validated, capped) read queries alongside the existing fixed methods (do not remove the fixed methods — the batch path uses them).
- Implement the tool registry with arg validation + redaction + caps.
- Tests: each tool is read-only, validates/rejects bad args, applies redaction, respects caps.

**Phase 2 — The agentic loop behind a flag + tests.**
- Tool-calling method on `LLMClient` (Decision A) or the LangGraph agent (Decision B).
- The loop with budget/timeout/per-hop redaction/per-step audit.
- Fallback to deterministic `investigate()` when unsupported/unavailable/over-budget.
- Tests: fake tool-calling LLM (emits tool_calls then a final answer); budget/timeout enforcement stops the loop; Layer-A contract (no Layer B imports; output has no execution instructions); fallback path; malicious tool-result is capped/redacted and cannot escalate.

**Phase 3 — CLI + settings + docs + observability.**
- `--ask --agentic`, settings, env loaders, graceful default-off.
- Docs: update `docs/OBSERVABILITY.md` (flip the "planned" note on the investigation agent to "available"; the LangSmith Tools/Run-Types panels now light up), `README.md` (mention agentic `--ask`), new `docs/learning/XX-investigation-agent.md`.
- Optional metric + LangSmith tracing notes.

---

## 8. Files to create / change (checklist for the implementer)

Create:
- [ ] `errander/agent/investigation_agent.py` — agentic loop + tool registry
- [ ] `tests/agent/test_investigation_agent.py` — loop, budget, fallback, Layer-A contract
- [ ] `tests/agent/test_investigation_tools.py` — per-tool read-only/validation/redaction/caps
- [ ] `docs/learning/XX-investigation-agent.md` — feature learning doc (Phase 3)

Change:
- [ ] `errander/integrations/llm.py` — add tool-calling method (leave `complete()` as-is)
- [ ] `errander/integrations/prometheus.py` — add validated arbitrary-query method (keep `fetch_vm_metrics`)
- [ ] `errander/integrations/elk.py` — add validated arbitrary-search method (keep `fetch_vm_errors`)
- [ ] `errander/config/settings.py` — 3 new settings + env loaders
- [ ] `errander/main.py` — `--ask --agentic` flag + dispatch + fallback
- [ ] `errander/safety/ai_audit.py` — (if needed) accommodate `investigation_agent_step` decision_type
- [ ] `errander/observability/metrics.py` — (optional) tool-call counter
- [ ] Docs: `docs/OBSERVABILITY.md`, `README.md`, + the always-update doc-sync set

---

## 9. Definition of done

- [ ] `--ask --agentic "<question>"` runs a bounded read-only tool-calling loop and returns an `AssistantResponse` (recommendations only).
- [ ] With the flag off (default), behavior is identical to today's deterministic `investigate()`.
- [ ] On an endpoint without tool support, or LLM down, or budget exhausted → clean fallback to deterministic path; no operator-facing error.
- [ ] Every hop + tool call is in the audit (`ai_decisions`); tool results are redacted and capped.
- [ ] No Layer B import anywhere in the new code (test-enforced). No write/exec tool exists.
- [ ] `uv run pytest`, `uv run ruff check .`, `uv run mypy .` all clean.
- [ ] Docs synced in the same commit(s); a `docs/learning/` doc exists.

---

## 10. Risks / watch-outs

- **vLLM/T4 latency:** a multi-hop loop can be slow on the 60s-timeout self-hosted path. The budget caps (max_tool_calls, timeout) are what keep it usable — tune defaults conservatively.
- **Prompt injection via tool results:** logs/labels can carry attacker-controlled text. Read-only tools bound the blast radius to "wrong answer," but still redact + cap, and never let a tool result widen tool permissions.
- **Scope creep into Layer B:** the moment someone proposes an "apply the fix" tool, stop — that violates the invariant. Recommendations flow to the human → deterministic Layer B only.
- **Don't regress the batch path:** `prioritize_actions` stays deterministic. Keep the fixed `PrometheusClient.fetch_vm_metrics` / `ElkClient.fetch_vm_errors` methods intact.
