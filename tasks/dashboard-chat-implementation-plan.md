# Implementation Plan — Dashboard Chat (SRE ops-console assistant)

> **Status:** proposed, not started. **Depends on Plan A** (`tasks/investigation-agent-implementation-plan.md`). Build the investigation agent **first**; this plan is a surface on top of it.
>
> **One-line goal:** a chat box in the Errander dashboard that gives an SRE quick read-only answers about the fleet ("is the patch done on web-02?", "CPU/mem on web-02?", "any issue with app X?") — and can optionally *propose* an action that flows through the existing approval gate. It never executes anything itself.

---

## 0. MANDATORY pre-flight (do this first, every session)

1. **Read before coding:**
   - `tasks/investigation-agent-implementation-plan.md` (**Plan A**) — this chat reuses that engine. Do not re-implement reasoning/tools/guardrails here.
   - `docs/AI-ARCHITECTURE.md` — the two-layer model. Chat answers = Layer A; any action = Layer B via approval.
   - `docs/OBSERVABILITY.md` — the fixed signal menu + investigation-agent section; audit + AI-decision logging.
   - `CLAUDE.md` → AI Safety Invariant + Doc Sync Rule.
2. **Hard prerequisite:** **Plan A must be implemented before starting Plan B.** This plan calls the investigation engine; without it there is nothing to call.
3. **RECONCILE STEP (critical — neutralizes plan drift):** Plan B was written *before* the engine existed, so its integration references are **predictions against a contract**. Before writing chat code, skim the **as-built** investigation engine and fix any references here to match the real module/function/return shapes. Depend on the engine's *contract* (in → question + history, out → `AssistantResponse`), not its internals.
4. **The boundary (non-negotiable):**
   - The chat is a **surface**, not a new brain. It calls the Plan A engine for all reasoning.
   - **Read-only answers = Layer A.** The chat may investigate and recommend freely.
   - **Any actual change = Layer B, via the existing approval flow.** The chat may *propose/tee up* an action; a human approves; deterministic Layer B executes. **The chat never executes, never self-approves, never gets a write/exec tool.** (Mirror `AI-ARCHITECTURE.md` Example 3.)
5. Confirm git identity (`psc0des` / `sarathy.vass6@gmail.com`); doc-sync rule applies (code + docs one commit).

---

## 1. Why (the SRE quick-help console)

An SRE wants fast, conversational answers without running CLI commands or opening Grafana/Kibana:
- "Is the latest patch applied on `web-02`?" → audit trail.
- "What's CPU / memory on `web-02` right now?" → Prometheus.
- "Any issue with app X (docker container or systemd service)?" → service health + docker assessment + ELK errors.

All three are **read-only investigation** — exactly what the Plan A engine answers. The chat is a multi-turn front-end over that engine, living in the dashboard the operator already uses.

---

## 2. Scope

**In scope (v1)**
- A read-only **chat console** (Layer A) in the web dashboard, backed by the Plan A investigation engine.
- **Multi-turn conversation state** (the main net-new piece — Plan A is single-question).
- A `/ui/chat` web surface (auth + CSRF, reusing the existing UI framework).
- Per-turn audit + redaction (reusing Plan A's guardrails).

**In scope (v1.1, optional — gate behind its own flag)**
- **Action handoff:** when an answer implies a fix, the chat renders a "propose this action" affordance that launches the **existing approval flow** (Slack/Web UI). Approval → deterministic Layer B executes. The chat itself never executes.

**Explicitly OUT of scope**
- A new reasoning engine or new tools — **reuse Plan A**. If you find yourself writing tool/query logic here, stop: it belongs in Plan A.
- **Direct execution from chat.** No write/exec path. Ever.
- **Chat assignment / ownership** (routing conversations/investigations to specific operators, ticket-style) — separate team-workflow feature; revisit only with multiple operators.
- Any change to the scheduled maintenance batch or Layer B internals.

---

## 3. Dependency on Plan A (the contract)

The chat depends on the investigation engine through a narrow contract — **not** its internals:

- **Input:** a natural-language question (+ optional prior conversation turns for context).
- **Output:** an `AssistantResponse` (`errander/models/analysis.py`) — `summary` + `Finding`s with `evidence` citations; recommendations only, never execution instructions.
- **Reused wholesale from Plan A (do not duplicate):** the read-only tool set, the bounded loop, redaction, budget, per-step audit, graceful fallback, the Layer-A isolation guarantee.

> If the as-built engine doesn't yet accept conversation history, add that to the engine (Plan A) as a small contract extension — don't fork the reasoning into the chat module.

---

## 4. The net-new pieces (what this plan actually builds)

### 4a. Conversation state / multi-turn memory  *(the core new work)*
- A conversation store: thread id, ordered messages (role, content, timestamp), owner (the logged-in UI user).
- Suggested storage: a new SQLite table in the existing audit DB (PostgreSQL-compatible types, per the project's v2 path) — e.g. `chat_threads` / `chat_messages`. Follow the `AuditStore`/migrations pattern (`errander/safety/`).
- **Context-window management across turns:** cap history length fed to the engine; apply `ContextBudgeter` to the assembled history; summarize/elide old turns if needed. Long threads must not blow the budget or cost.

### 4b. Web UI surface
- A `/ui/chat` page in the existing dashboard (`errander/observability/metrics.py` already provides: session auth middleware, CSRF double-submit, nav, the `/ui/ai-decisions` page as a structural model).
- Routes: GET `/ui/chat` (render thread), POST `/ui/chat/message` (submit a question → engine → append answer). Render `Finding`s + citations.
- Reuse session auth + CSRF — no new auth scheme.

### 4c. Transport
- **v1: simple request/response** — POST question, return the full answer. Lowest complexity, fine for an internal console.
- **v1.1 (optional): streaming** (SSE) for token-by-token UX. In-network only — this is the existing `:9090` UI, so it does **not** introduce an inbound webhook (consistent with the no-inbound network model).

### 4d. Action handoff to approval  *(v1.1, optional, behind its own flag)*
- When an answer implies a fix, surface a "propose action" control that **constructs a proposed action and routes it to the existing approval flow** (`ApprovalManager` / the Slack + Web UI approval surface) — exactly the same artifact a normal batch produces.
- The human approves; deterministic **Layer B** executes; results return to the chat as a follow-up answer.
- **The chat code must not import or call execution/SSH/rollback.** Add a test asserting no Layer B execution imports in the chat module (mirror Plan A's Layer-A isolation test).

---

## 5. Guardrails (inherit Plan A's, plus chat-specific)

- **All Plan A guardrails apply** (Layer-A isolation, read-only tools, redaction, budget, per-step audit, graceful fallback).
- **Conversation history is also untrusted/sensitive:** redact it (ContextRedactor) before it re-enters the engine; budget it; never store unredacted secrets.
- **Auth + CSRF** on every chat route (reuse existing middleware). Threads are scoped to the logged-in user.
- **Per-turn audit:** log each chat turn to `AIDecisionStore` (e.g. `decision_type="dashboard_chat_turn"`) — question, engine outcome, latency. (The engine also logs its own steps per Plan A.)
- **Turn/rate caps** to bound cost (esp. self-hosted vLLM/T4).
- **The action handoff never executes.** It only constructs an approval request. Approval + execution stay in deterministic Layer B. No exceptions.

---

## 6. Wiring (files — reconcile against as-built engine first)

Create:
- [ ] `errander/safety/chat_store.py` (or similar) — conversation thread/message store + migration
- [ ] chat handlers — either a new `errander/web/chat.py` or new handlers in `errander/observability/metrics.py` (match where the rest of `/ui/*` lives at implementation time)
- [ ] `tests/ui/test_chat.py` — routes, auth/CSRF, render
- [ ] `tests/.../test_chat_store.py` — thread/message persistence, history budgeting
- [ ] `docs/learning/XX-dashboard-chat.md`

Change:
- [ ] the investigation engine (Plan A) — *if needed* — to accept conversation history (small contract extension)
- [ ] `errander/config/settings.py` — `ERRANDER_CHAT_ENABLED` (default off), history/turn caps, optional `ERRANDER_CHAT_ACTION_HANDOFF_ENABLED` (default off)
- [ ] `errander/safety/migrations.py` — new chat tables (update migration tests)
- [ ] nav/UI in `metrics.py` — a "Chat" link
- [ ] Docs: `docs/OBSERVABILITY.md`, `README.md`, + always-update doc-sync set

---

## 7. Phasing (green tree each commit)

- **Phase 1 — Read-only chat console.** Conversation store + `/ui/chat` + request/response + engine call + render. No actions. Tests + docs.
- **Phase 2 — Streaming UX (optional).** SSE token streaming.
- **Phase 3 — Action handoff (optional, own flag).** "Propose action → existing approval flow → Layer B executes." Layer-A isolation test on the chat module.
- **Later / separate plan — assignment & ownership** (multi-operator routing). Out of scope here.

---

## 8. Definition of done (v1)

- [ ] With `ERRANDER_CHAT_ENABLED=true`, `/ui/chat` answers read-only fleet questions (patch status, CPU/mem, app health) via the Plan A engine, multi-turn, with citations.
- [ ] Auth + CSRF enforced; threads scoped to the user; each turn audited.
- [ ] History is redacted + budget-capped; long threads don't blow cost/context.
- [ ] No Layer B execution import in the chat module (test-enforced). No direct execution path.
- [ ] With the flag off (default), no chat surface is exposed.
- [ ] `uv run pytest` / `ruff` / `mypy` clean; docs synced; a `docs/learning/` doc exists.

---

## 9. Risks / watch-outs

- **Plan drift vs. the as-built engine** — handled by the §0 reconcile step. Do it first.
- **Scope creep into execution** — the moment the chat gets a "run it" button that bypasses approval, the invariant is broken. Action = propose → approve → Layer B only.
- **Prompt injection via conversation history / tool results** — redact + cap; read-only tools bound the blast radius to a wrong answer.
- **Cost on self-hosted vLLM** — multi-turn + agentic loop compounds; turn caps + history budgeting are the controls.
- **Don't fork reasoning** — if chat logic starts deciding *what to query*, that's Plan A's job; extend the engine, don't duplicate it.
