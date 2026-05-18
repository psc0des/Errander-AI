# Errander-AI — Future Enhancements

Parking lot for features that are designed but not yet built, or blocked on a prerequisite. Each entry explains the blocker so future-you knows exactly what needs to happen before starting.

Last updated: 2026-05-18

---

## What's Done vs Pending

| Phase | Status | Notes |
|---|---|---|
| A1 — Durability measurement | **DONE 2026-05-18** | `--measure-durability`, orphan-batch scan, 2 new counters |
| B1 — VMFactsStore | **DONE 2026-05-18** | SQL aggregations over existing stores |
| B2 — OperatorAssistant facts integration | **DONE 2026-05-18** | Facts injected into LLM context |
| D1 — Full prompt capture in AIDecisionStore | **DONE 2026-05-18** | `prompt_full`, `context_snapshot`, `model_params` columns; idempotent ALTER TABLE |
| A2–A6 — Checkpointing + CLI | Gated — wait for A-gate data (1–2 weeks) | See thresholds below |
| B3/B4 — Trend detection | Ready to build — no blocker | Low risk, Layer A only |
| C1–C4 — Runbook memory | Blocked on runbook content | Write `./runbooks/*.md` files first |
| D2–D4 — Replay + AI evals | When AI quality is a priority | D1 data accumulating now |
| E — HITL interrupt/resume | Deferred | Trigger conditions listed below |

---

## Project A — Workflow Durability (Checkpointing)

**Status:** A1 DONE. A2–A6 gated behind measurement data.

**Gate:** Run `--measure-durability` after 1–2 weeks of production batches. Proceed to A2 only if any threshold is crossed:

| Signal | Threshold |
|---|---|
| p95 batch duration | > 10 minutes |
| p95 approval wait | > 5 minutes |
| Interrupted batches per week | ≥ 1 |
| Agent restarts during a live batch | ≥ 1 per month |
| Operator complaint "I lost a batch" | any |

If all signals stay below threshold: re-measure quarterly, don't build A2–A6.

**Phases when gate clears:**
- **A2** — `batch_status` taxonomy (`batches` table, migration 0004) — prerequisite for everything else
- **A3** — State serialization tests (run before wiring checkpointer; failures form A4 work list)
- **A4** — Move big artifacts out of graph state (new `artifacts` table, migration 0005; touches every subgraph)
- **A5** — SQLite checkpointing (`SqliteSaver`, safe-resume node allowlist, `agent_lease` table, migration 0006)
- **A6** — `runs inspect/resume` CLI (operator ergonomics on top of A5)

**Key design constraint (non-negotiable):** checkpointing is workflow durability only. Never blindly resumes inside a side-effecting SSH action. See `tasks/post-review-implementation-plan.md` §4 for safe-resume node allowlist.

**Full design:** `tasks/post-review-implementation-plan.md` §4.

---

## Project B — Operational Learning Memory (B3/B4)

**Status:** B1 + B2 DONE. B3/B4 are next.

- **B3** — Periodic background aggregation (pre-compute facts on a schedule rather than on each OperatorAssistant call)
- **B4** — Trend detection (surface deteriorating VMs: success rate dropping over time, rejection rate rising)

**Blocker:** none — can start any time. Low risk (Layer A only).

**Full design:** `tasks/post-review-implementation-plan.md` §5.

---

## Project C — Runbook & Postmortem Memory

**Status:** DESIGN ONLY. Blocked on content, not code.

**What it does:** OperatorAssistant searches `./runbooks/*.md` by keyword/tag and injects relevant runbook excerpts into its answers. Example: "what should I check before restarting nginx?" returns the nginx restart checklist from your runbooks.

**Blocker — content must exist before code has value.**
The code (C1: `RunbookStore`, C2: `OperatorAssistant` integration) is straightforward to build, but the feature is completely inert if `./runbooks/` is empty. There is no point shipping C1-C2 until runbook files exist.

**What runbooks look like:**
```
./runbooks/
  patching-dpkg-lock.md        # what to do when patching fails with dpkg lock
  nginx-restart-checklist.md   # what to verify before restarting nginx
  disk-cleanup-safe-paths.md   # guidance on what's safe to clean
  post-patch-reboot.md         # which VMs typically need reboot after patching
```

**How to start:** take 3–5 real incidents or recurring issues your team has dealt with and write them as markdown files in `./runbooks/`. Even rough bullet-point notes work — retrieval is keyword-based, not semantic.

**Once runbooks exist, C is a ~2-day build:**
- C1 — `RunbookStore` (scan `./runbooks/`, keyword+tag index, return matching excerpts)
- C2 — Wire into `OperatorAssistant.investigate()`
- C3 — Runbook authoring guide + example templates
- C4 — Postmortem capture (structured markdown format, auto-linked to `batch_id`)

**No embeddings in v1.** Keyword + tag match only. Vector search is a v2 extension.

**Full design:** `tasks/post-review-implementation-plan.md` §6.

---

## Project D — Historical Replay & AI Evals

**Status:** D1 DONE. D2–D4 blocked on AI quality becoming a priority.

**What it does:** use past LLM prompt+response pairs to evaluate whether the AI layer is consistent, safe, and improving over time.

**D1 is done** — `prompt_full`, `context_snapshot`, and `model_params` now captured in `ai_decisions` via idempotent ALTER TABLE (works on both fresh installs and existing DBs). Data is accumulating from this point forward.

**Remaining phases:**
- **D2** — Replay harness: given a saved `context_snapshot`, re-invoke the LLM and compare decisions
- **D3** — Assertions: did the model recommend out-of-policy actions? Did it cite evidence? Did fallback match expected priority?
- **D4** — Retention CLI: prune old prompt snapshots (expected ~150 MB/year at current batch frequency)

**Blocker:** none technical. Start D2 when AI quality measurement becomes a priority for the team.

**Full design:** `tasks/post-review-implementation-plan.md` §7.

---

## Project E — HITL Interrupt/Resume (LangGraph native)

**Status:** DEFERRED. Do not start without a trigger.

**What it does:** replaces the current polling-based approval (`await_dual_approval`) with true LangGraph interrupt/resume, so approval wait survives process restart without an asyncio.Event.

**Triggers to revisit (any one is sufficient):**
- A-gate data shows approval wait p95 > 10 minutes regularly
- Operators report losing approval state after an agent restart
- Checkpointing (A5) lands and the asyncio.Event gap becomes an obvious foot-gun in practice

**Prerequisite:** A5 (checkpointing) must land first. Interrupt/resume without durable checkpointing is not production-grade.

**Effort:** 1–2 week refactor. Touches `approval_gate_node`, `await_dual_approval`, `ApprovalManager`, and `errander/main.py` invocation loop.

**Full design:** `tasks/post-review-implementation-plan.md` §8.

---

## Declined — OperatorAssistant as a LangGraph Graph

**Decision:** declined on principle. OperatorAssistant is read-only; it has no resume need; making it a graph adds complexity for no safety benefit. "Agentic for marketing, not safety."

**What would change this decision:** if OperatorAssistant needs to take write actions (it shouldn't — that violates the Layer A/B boundary), or if there is a concrete multi-step investigation that requires graph-level state persistence that cannot be achieved with a plain async method chain.

---

## v2 Infrastructure Upgrades

Not feature work — infrastructure upgrades deferred from v1:

| Item | When |
|---|---|
| PostgreSQL (replace SQLite audit trail) | When HA or multi-worker is needed |
| Valkey / Redis (replace file-based VM locking) | When lock contention is measured |
| HashiCorp Vault (replace env-var secrets) | When compliance requires it |
| Postgres checkpointer (replace SQLite checkpointer) | Project A v2, after HA decision |
| Embeddings / vector search for runbooks | Project C v2, after keyword search proves value |

---

## Reference

Full implementation plans: `tasks/post-review-implementation-plan.md`
Session context: `tasks/post-review-session-flow.md`
