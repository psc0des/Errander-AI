# Post-Review Planning Session — Complete Flow

Date: 2026-05-18
Purpose: Session-recovery document. If you lose conversation context, read this first.
Paired with: `tasks/post-review-implementation-plan.md` (the master plan this session produced).

---

## TL;DR — If You Have 60 Seconds

You reviewed `ai_sre_langgraph_agentic_review.md` with Opus. Code claims in the doc were verified accurate. You and your SRE agreed on a **narrow checkpointing contract**: checkpointing protects workflow durability only, never blindly resumes side-effecting actions. The 10 review items were consolidated into **5 projects + 1 declined** in `tasks/post-review-implementation-plan.md`.

**What was implemented (2026-05-18):** Phase A1 + Project B Phases B1–B2 — DONE. Commit `feat: durability measurement (orphan-batch scan, --measure-durability CLI, VMFactsStore)`. 1953 tests. `--measure-durability` against current DB: 0 batches in window, BATCHES_INTERRUPTED_TOTAL=0.

**What to do next:** wait 1–2 weeks for production measurement data, then review against the §4 Project A decision gate to decide whether to build A2–A6.

**Why A1 only:** Project A's gate at §4 requires 1–2 weeks of measurement data before deciding whether to build A2–A6. Don't commit weeks of refactor work for problems the data may not show.

---

## What Triggered the Session

You showed me `ai_sre_langgraph_agentic_review.md` — a review doc that made specific claims about Errander-AI being LangGraph-based, what state management exists, and what improvements would strengthen it. You asked:
1. Which claims are true in the current code?
2. What effort/risk for LangGraph checkpointing and interrupt/resume?
3. Is this a value addition?
4. (Implicit) Do you have questions for the SRE?

---

## Step 1 — Verification of the Review Doc

Grep'd for `StateGraph`, `Send(`, `.compile(`, `checkpointer`, `interrupt`, `approval`, stores, OperatorAssistant. Read state-class definitions and the main.py runtime path.

**Claims verified TRUE:**
- LangGraph genuinely used: `StateGraph` + `Send()` fan-out at `errander/agent/graph.py:1564,1715`.
- `BatchGraphState`/`VMGraphState` exist as TypedDicts.
- All 6 action subgraphs present.
- All 6 stores exist (Audit, AIDecision, VMDiskHistory, Baseline, DeferredExecution, VMState).
- `OperatorAssistant` is a regular Python class, not a graph.
- **No checkpointer attached at `.compile()`** — confirmed in `errander/main.py:1155`. No `Checkpointer` import anywhere under `errander/agent/`.
- **Approval is a polling node**, not LangGraph interrupt/resume. `await_dual_approval` races Slack polling vs an asyncio.Event from the UI (`errander/safety/approval.py:280`).
- State is mostly serializable primitives — TypedDicts of str/dict/list/bool.
- Subsystems outside LangGraph (CLI, web, SSH, integrations, scripts) as listed.

**Claims partially off:** The doc said "appears to not attach a checkpointer." It's not "appears" — it's definitive. Otherwise the doc was conservative and accurate.

**Claims false:** None.

---

## Step 2 — Effort/Risk Analysis (Opus's initial read)

**Checkpointing:** Low effort (1–2 days), Medium risk. Trap is in state — subgraph states carry large blobs (full apt output, command stdout). Checkpointing serializes all of it at every super-step. Real fix is moving big artifacts out, which is a refactor touching every subgraph.

**Interrupt/resume for HITL:** Medium-High effort (1–2 weeks), High risk if done before checkpointing. Requires splitting `approval_gate_node`, moving `await_dual_approval` out of the graph, refactoring `main.py`'s invocation loop.

**Initial recommendation:**
1. Serialization tests
2. Move big artifacts out
3. SQLite checkpointing
4. Recovery commands
5. Interrupt/resume (later)
6. Skip OperatorAssistant-as-graph

---

## Step 3 — Six Questions Sent to SRE

1. What's the real failure mode you want checkpointing to solve?
2. How long do batches actually run? (Drives urgency)
3. Multi-worker plans? (Drives SQLite vs Postgres)
4. What's the contract for "resume after crash"?
5. Do operators want `runs list / inspect / resume`?
6. Deferred execution vs checkpointing — same DB or separate?

---

## Step 4 — SRE's Answers (key decisions baked into the plan)

**Narrow contract for checkpointing:**
> Checkpointing protects planning, approval wait, deferred/reapproval, and between-wave orchestration. It must NOT blindly resume inside a side-effecting SSH action. LangGraph checkpointing is workflow durability — not rollback, not idempotency, not VM state recovery.

**Primary failure mode to solve:** agent process dies while waiting for approval, before execution, or between waves. NOT mid-`apt upgrade`.

**Measurement first:** before building anything, ship metrics for p50/p95 batch duration, approval wait, longest action by type, agent restart frequency, interrupted batches.

**Multi-worker:** Assume single agent process for v1. SQLite acceptable. Postgres only if HA needed (v2 decision).

**Resume contract:**
> Errander never blindly resumes a side-effecting action after crash. On resume, it must re-verify state and either continue from a safe boundary or mark the batch `NEEDS_OPERATOR_REVIEW`.

Safe resume points: before execution, after approval but before dispatch, between waves, after a VM action with verification complete. Unsafe: inside an SSH command.

**Operator UX:** Start simple. Minimum: `runs inspect`, `runs resume`. (`runs abandon` deliberately skipped — dangerous without reconciliation.)

**Same DB, logical separation:** SQLite acceptable for v1. Keep tables / store wrappers / migrations separate.

**Phased implementation (SRE's order):** measure → checkpoint at safe boundaries → serialization tests → CLI → reconciliation (much later).

---

## Step 5 — Opus's Agreement / Pushback

**Agreed strongly:**
- Checkpointing is workflow durability, not rollback/idempotency/VM recovery.
- Measure first.
- Safe-vs-unsafe resume point taxonomy is correct.
- Same SQLite file with logical separation.
- "Don't pretend SQLite is HA" — if two agents ever ran against the same DB, code should refuse to start.
- Don't claim resume-safety until reconciliation exists.

**Pushed back / added:**
- Approval-wait-survives-restart is sneakily hard. Slack ts is persisted; asyncio.Event is not. The narrow "v1 checkpoint contract for approval" actually needs an `ApprovalManager` refactor — flag it explicitly so it doesn't get discovered mid-implementation.
- `NEEDS_OPERATOR_REVIEW` needs a home before reconciliation logic — add a `batch_status` taxonomy first.
- `runs abandon` is more dangerous than it sounds (operator declares batch dead without verifying VMs). Either require `--confirm-vms-checked --reason "..."` flags or skip in v1.
- **Flip phase ordering:** serialization tests BEFORE checkpointing, not after. Locks in contract before checkpointer exists.
- Deferred execution already half-solves this and isn't called out. New checkpoint design must converge with existing `is_deferred_replay` / `preloaded_plan_json` code into one shared helper.
- Reconciliation deserves its own design doc, not a checklist item — per-action semantics differ wildly (disk_cleanup trivial, patching hard).

---

## Step 6 — Scoping Decision

You asked: "the review doc has 10 items, why just checkpointing?"

Answer: the plan covers **5 of 10 items** as one tightly-coupled cluster (workflow durability):
- #1 checkpointing, #2 serialization tests, #3 artifact extraction, #6 keep Layer B deterministic, #7 recovery commands.

The other 5 items are not in the same plan because they have **different blast radius / dependency chain**:
- #4 interrupt/resume — joint decision to defer (1–2 week refactor, needs checkpointing first, narrow contract handles approval-wait without it).
- #5 OperatorAssistant as LangGraph — declined on principle ("agentic for marketing, not safety").
- #8 operational learning memory — Layer A, no overlap with checkpointing, independent project.
- #9 runbook/postmortem memory — Layer A, has a missing prerequisite (no runbook store exists), content/UX project before code.
- #10 historical replay / AI evals — parallel to durability, ships when AI quality becomes a priority.

**Grouping principle:** share a blast radius and a dependency chain → one project; otherwise → separate plans.

You then said: "consolidate everything into one master plan." That produced `tasks/post-review-implementation-plan.md` with 5 projects + 1 declined.

---

## Step 7 — Master Plan Structure

`tasks/post-review-implementation-plan.md` contents:

| § | Section | What's in it |
|---|---|---|
| 1 | How to use this plan | Status legend (`DO NOW`, `DESIGN ONLY`, `DEFERRED`, `DECLINED`), stopping points |
| 2 | Review item status matrix | All 10 items mapped to project/phase/status |
| 3 | Cross-cutting boundaries | Layer A/B invariant, permanently out-of-scope items |
| 4 | **Project A — Workflow Durability** (items 1,2,3,6,7) | Phases A1–A6. A1 is `DO NOW`, rest gated |
| 5 | **Project B — Operational Learning Memory** (item 8) | Phases B1–B4. Pure SQL aggregation, no new table |
| 6 | **Project C — Runbook & Postmortem Memory** (item 9) | Phases C1–C4. **Open question: storage location** |
| 7 | **Project D — Historical Replay & AI Evals** (item 10) | Phases D1–D4. **Prereq: D1 must add full prompt capture** (today `AIDecisionStore` stores only 16-char hash) |
| 8 | **Project E — HITL Interrupt/Resume** (item 4) | DEFERRED with explicit triggers + design sketch |
| 9 | **Declined — OperatorAssistant as LangGraph** (item 5) | With reversal criteria |
| 10 | Execution order | A1 first → A-gate → A2–A6 sequential; B parallel after A1; C/D by operator pull; E on trigger |
| 11 | Open items Sonnet must flag | Six "don't silently decide" points |
| 12 | Reference pointers | File:line locations per project |
| 13 | Doc sync | CLAUDE.md checklist for every commit |

---

## Step 8 — Handoff Strategy

You asked: "why hand A1 only? why not the whole thing?"

**Handoff (chosen 2026-05-18):**
> "Implement Phase A1 plus Project B Phases B1–B2 in parallel. Stop at end of §4 Phase A1.7 and §5 Phase B2 (whichever ships last). Report back per A1.7 deliverable plus a sample OperatorAssistant response showing facts in context."

**Reasoning:**
1. The plan's own §4 gate requires 1–2 weeks of measurement before A2–A6 — A1 alone is the durability-cluster work.
2. Project B is read-only Layer A, zero infrastructure risk, doesn't depend on A-gate. Safe to ship alongside measurement.
3. Shipping A1 + B1+B2 in one round gives OperatorAssistant evidence-based facts immediately while measurement runs.
4. A4 is a multi-week refactor touching every subgraph — gated behind A-gate, not in this handoff.
5. Project E is explicitly deferred — don't risk Sonnet reading `DEFERRED` as "design now."

**Never hand Sonnet without gates:**
- A2–A6 before A-gate clears.
- C until you're ready to invest in runbook authoring (storage is decided: filesystem).
- D until you're ready to invest in AI eval infrastructure (prompt storage decided: full).
- E at all (only on trigger conditions in §8 of master plan).

---

## Decision Log (compact)

| Decision | Rationale | Alternatives considered |
|---|---|---|
| Narrow checkpointing contract | Per SRE: prevents false confidence in "magic resume" | Full resume semantics (rejected — needs reconciliation) |
| Measure first (Phase A1) | Avoid building durability for problems data doesn't show | Build checkpointing immediately (rejected by SRE) |
| Serialization tests BEFORE checkpointing | Lock in state contract before checkpointer can break silently | Tests after (SRE's original order — Opus flipped) |
| `batch_status` table before checkpointing | `NEEDS_OPERATOR_REVIEW` needs a home before reconciliation | Add status later (rejected — too late) |
| Skip `runs abandon` | Dangerous without reconciliation | Add with `--confirm-vms-checked` flag (deferred to v2) |
| Defer HITL interrupt/resume (Project E) | 1–2 week refactor, narrow contract handles v1 needs | Build alongside checkpointing (rejected — premature) |
| Decline OperatorAssistant as graph | Read-only, no resume need, "agentic for marketing" | Build it (rejected — violates own safety principle) |
| Project B starts after A1 (not gated) | Pure Layer A, no infrastructure risk | Gate B behind A (rejected — independent) |
| Filesystem for runbooks (recommended) | No authoring tooling needed, git versioning | SQLite (v2), external wiki (per-customer integration) |
| Same SQLite DB, separate tables | Per SRE: operationally simpler, logical separation preserved | Separate DB files (rejected — coupling cost low) |
| Single-agent enforcement via lease table | Prevent two agents corrupting checkpoint DB | None (must have — SQLite isn't HA-safe) |

---

## Open Questions — Resolved 2026-05-18

All four open questions were answered. Decisions:

1. **Project C runbook storage:** filesystem (`./runbooks/*.md`). SQLite + external wiki rejected for v1.
2. **Project D prompt storage:** store full prompts + context (~10–50 KB per call, ~150 MB/year). Enables Phase D2 replay and all D3 assertions. Add retention CLI in D4.
3. **A1 measurement window:** 14 days (default). Captures weekly patterns without delaying the A-gate decision.
4. **Project B timing:** start B1–B2 in parallel with Phase A1. Layer A only, zero infrastructure risk, OperatorAssistant gets facts immediately.

These decisions are reflected in `tasks/post-review-implementation-plan.md` (§2, §6, §7, §10, §11).

---

## Files Created This Session

- `tasks/post-review-implementation-plan.md` (master plan, ~600 lines)
- `tasks/post-review-session-flow.md` (this file)

## Files Reviewed (not modified)

- `ai_sre_langgraph_agentic_review.md` (source review)
- `errander/agent/graph.py`, `vm_graph.py`, `subgraphs/*.py` (LangGraph layer)
- `errander/main.py` (runtime path)
- `errander/safety/{audit,ai_audit,migrations,approval}.py` (stores + approval)
- `errander/observability/metrics.py` (existing metrics)
- `errander/agent/operator_assistant.py` (Layer A)
- `errander/models/events.py` (EventType enum)

## Files NOT Updated (deliberately)

- No code changes — this session was planning only.
- `STATUS.md`, `tasks/todo.md`, `tasks/lessons.md`, `docs/learning/` — none updated since no code shipped. Will update when Phase A1 lands.

---

## How To Resume This Session (if context is lost)

1. Read this file end-to-end (~5 min).
2. Read `tasks/post-review-implementation-plan.md` §1–3 (~10 min) for legend and boundaries.
3. Read the project section(s) you're working on (~5 min each).
4. Check answers to the four open questions above.
5. Pick a path:
   - **Default:** hand Phase A1 to Sonnet (handoff prompt in Step 8 above).
   - **Throughput:** hand A1 + B1–B2 to Sonnet (alternative prompt in Step 8).
   - **Re-decide:** if you want to reopen item #4 (interrupt/resume) or item #5 (OperatorAssistant as graph), revise §8/§9 of the master plan first.
6. After Phase A1 lands and you have 1–2 weeks of data: run `--measure-durability`, evaluate against §4 decision-gate thresholds, record decision + data in `docs/learning/XX-langgraph-checkpointing.md`, then decide whether to proceed to A2.

---

## Key References

- Source review: `ai_sre_langgraph_agentic_review.md`
- Master plan: `tasks/post-review-implementation-plan.md`
- Project rules: `CLAUDE.md`
- AI architecture (Layer A vs B): `docs/AI-ARCHITECTURE.md`
- Auto memory: `C:\Users\THISPC\.claude\projects\E--AI-Errander-AI\memory\`
