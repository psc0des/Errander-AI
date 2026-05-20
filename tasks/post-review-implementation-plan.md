# Errander-AI Post-Review Implementation Plan

Single master plan covering every actionable item from `ai_sre_langgraph_agentic_review.md`. Five projects (A–E), one declined, status legend below.

Status (2026-05-20): All buildable work is complete.
- Project A (A1–A6): DONE
- Project B (B1–B3): DONE
- Project D1: DONE
- Project C: DEFERRED — blocked on operator-authored runbooks (./runbooks/*.md)
- Project D2–D4: DEFERRED — blocked on real ai_decisions data (agent must run live batches first)
- Project E: DEFERRED — blocked on crash/approval-loss evidence from A1 measurement
- See tasks/todo.md "Deferred — revisit when ready" for trigger conditions.

Last updated: 2026-05-20
Source review: `ai_sre_langgraph_agentic_review.md`
SRE-conversation decisions: see `tasks/` history and review summary
Plan owner (execution): Dev Sonnet agent
Plan owner (review): SRE before any phase past A1

---

## 1. How To Use This Plan

This is the consolidated handoff. Sonnet has no prior conversation context — read sections 1–3 in full before touching code.

**Status legend** (every phase carries one):

| Tag | Meaning |
|---|---|
| `DO NOW` | Implement immediately. One phase, then stop and report. |
| `DESIGN ONLY` | Architecture is written down for review. Do not implement yet. |
| `DEFERRED` | Out of scope this round but documented so we don't lose it. Has a trigger condition for revisit. |
| `DECLINED` | Intentionally not building this. Has criteria for what would change the decision. |

**Stopping points are real.** Each `DO NOW` phase has explicit acceptance criteria and a "what to deliver back" block. Stop there. Do not freelance into the next phase even if it looks small.

**Doc-sync rule (CLAUDE.md) applies to every commit.** Section 13 has the checklist.

---

## 2. Review Item Status Matrix

Maps the 10 items from `ai_sre_langgraph_agentic_review.md` to where each one lives in this plan.

| # | Review item | Project · Phase | Status |
|---|---|---|---|
| 1 | Add LangGraph checkpointing | A · A5 | DESIGN ONLY |
| 2 | State serialization tests before checkpointing | A · A3 | DESIGN ONLY |
| 3 | Store big artifacts outside graph state | A · A4 | DESIGN ONLY |
| 4 | Refactor HITL approval to LangGraph interrupt/resume | E | DEFERRED |
| 5 | Make Operator Assistant a read-only LangGraph | (declined) | DECLINED |
| 6 | Keep Layer B deterministic | A · cross-cutting | Already true; guardrail in §3 |
| 7 | Checkpoint-aware recovery commands | A · A6 | DESIGN ONLY |
| 8 | Long-term AI memory through operational learning | B · B1+B2 DONE | **B1+B2 DONE 2026-05-18** (B3/B4 DESIGN ONLY) |
| 9 | Runbook and postmortem memory for Layer A | C | DESIGN ONLY (storage decided: filesystem) |
| 10 | Historical replay and AI evals | D | DESIGN ONLY |

Plus two items the review did not list but the SRE conversation surfaced:

| Item | Project · Phase | Status |
|---|---|---|
| Measurement before building | A · A1 | **DONE 2026-05-18** |
| `batch_status` first-class taxonomy | A · A2 | DESIGN ONLY |

---

## 3. Cross-Cutting Boundaries

These apply to every project. Stop and ask if any work starts violating them.

**Layer A vs B (CLAUDE.md "AI Safety Invariant").**
- Layer A (read-only investigation/recommendation): may use LLM, may read any store, must never write infrastructure state.
- Layer B (deterministic execution): no LLM in the live execution path, no MCP/tool-calling, no AI-generated shell commands.
- Project A, C, D additions must respect this split. Project B operates entirely on Layer A.

**Permanently out of scope** (do not implement under any phase):
- Mid-action crash reconciliation (re-probe VM, compare to approved plan, decide what happened). Will be a separate design doc later, not in this plan.
- Multi-worker / HA. SQLite only. Project A enforces single-process via a lease table.
- Postgres checkpointer. v2.
- `errander runs abandon`. Dangerous without reconciliation. Skip.
- Embeddings / vector retrieval for runbooks (Project C). Phase C2 is keyword/tag match only; embeddings are a v2 extension.
- Any new LLM/MCP/tool-calling inside subgraphs.

**Doc-sync rule (CLAUDE.md).** Every commit updates STATUS.md, command-log, todo, lessons (if relevant), plus feature-specific docs. Section 13 has the checklist.

---

## 4. Project A — Workflow Durability

Covers review items #1, #2, #3, #6, #7. The "checkpointing cluster" — all phases share migrations, state contracts, and code paths.

### Background

Runtime path today is bare `.compile()` at `errander/main.py:1155`. Approval is a polling node (`approval_gate_node` → `await_dual_approval` at `errander/safety/approval.py:280`), so the agent process must stay alive for the entire approval window. There is no resume after crash.

The SRE's narrow contract:

> Checkpointing protects planning, approval wait, deferred/reapproval, and between-wave orchestration. It must NOT blindly resume inside a side-effecting SSH action.
> LangGraph checkpointing is workflow durability — not rollback, not idempotency, not VM state recovery.

Most of the SRE-requested measurement metrics already exist as Prometheus histograms (`errander_batch_duration_seconds`, `errander_approval_wait_seconds`, `errander_action_duration_seconds`), so Phase A1 is small. What's missing: restart frequency, interrupted-batch counts, and a no-Grafana way to summarise.

---

### Phase A1 — Measurement · **DONE 2026-05-18**

Commit: `feat: durability measurement (orphan-batch scan, --measure-durability CLI, VMFactsStore)`
- 1953 tests passing, ruff clean, mypy strict clean.
- `--measure-durability` against current `errander.sqlite`: 0 batches in 14-day window, BATCHES_INTERRUPTED_TOTAL=0.
- Nothing past A1 ships until the data is reviewed against §4-gate (collect 1–2 weeks of production data).

Ship instrumentation, run it for 1–2 weeks, then revisit.

#### A1.1 New Prometheus counters

In `errander/observability/metrics.py`:

```python
AGENT_STARTS_TOTAL = Counter(
    "errander_agent_starts_total",
    "Agent process startups (proxy for restart frequency)",
    registry=REGISTRY,
)

BATCHES_INTERRUPTED_TOTAL = Counter(
    "errander_batches_interrupted_total",
    "Batches detected on startup with BATCH_STARTED but no terminal event",
    registry=REGISTRY,
)
```

`AGENT_STARTS_TOTAL.inc()` is called once during `errander/main.py` startup, before the scheduler starts.

#### A1.2 Startup orphan-batch scan

New `errander/observability/startup_scan.py`:

- Query `audit_events` for distinct `batch_id` where `BATCH_STARTED` exists but no `BATCH_COMPLETED` / `FLEET_ABORT` event within the last 7 days.
- For each, emit a structured WARNING log: `batch_id`, `started_at`, `last_seen_event_type`, `last_seen_at`.
- Increment `BATCHES_INTERRUPTED_TOTAL` by the count.
- Do NOT mark the batches in any way (no schema change in A1). Purely visibility.

Called from `main.py` once, immediately after `run_migrations` completes and before the scheduler starts.

#### A1.3 `--measure-durability` CLI

Queries `audit_events` directly (SQLite, no Prometheus dependency) and prints:

```
Errander durability snapshot — window: last 14 days
  Batches:        total=N   completed=N   interrupted=N   completion_rate=NN.N%
  Batch duration (BATCH_STARTED → BATCH_COMPLETED):
    p50=Xs   p95=Xs   max=Xs   sample=N
  Approval wait (APPROVAL_REQUESTED → first of GRANTED/REJECTED/TIMEOUT):
    p50=Xs   p95=Xs   max=Xs   sample=N
    auto-rejected=N   granted=N   rejected=N
  Longest actions (ACTION_STARTED → ACTION_COMPLETED/FAILED):
    patching       p95=Xs   max=Xs   sample=N
    docker_prune   p95=Xs   max=Xs   sample=N
    ... (one row per action_type seen)
  Agent restarts during a live batch: N
```

Percentile math inline (sort + index — no numpy). `--window-days N` flag, default 14. Wire into existing CLI in `errander/main.py` next to `--audit` / `--check-llm`. Add a row to `RUN.md`.

#### A1.4 Tests

In `tests/observability/`:
- `test_startup_scan.py` — fixtures seed three batches (completed, in-flight, interrupted); scan reports only the interrupted, increments counter exactly once.
- `test_measure_durability.py` — fixtures seed realistic events; assert p50/p95/max values, auto-rejected vs rejected split, per-action grouping.

Style matches `tests/safety/test_audit.py` (async aiosqlite, in-memory DB).

#### A1.5 What NOT to add in A1

- No new tables. `batch_status` is A2.
- No `langgraph-checkpoint-sqlite` install.
- No `runs inspect/resume` CLI.
- No changes to graph state or subgraphs.

#### A1.6 Acceptance criteria

- `uv run pytest` passes.
- `uv run ruff check .` clean.
- `uv run mypy .` clean (strict).
- `uv run python -m errander --measure-durability` against existing `errander.sqlite` prints the report, exit 0.
- `/metrics` exposes both new counters.
- Doc sync per §13.

#### A1.7 Deliverable

Single commit (one-line message per CLAUDE.md):

```
feat: durability measurement (orphan-batch scan, --measure-durability CLI)
```

Then **stop**. Report back with:
1. Output of `--measure-durability` against current `errander.sqlite` (sanitise if needed).
2. Whether `BATCHES_INTERRUPTED_TOTAL` showed any non-zero on first run.

---

### Project A Decision Gate (review after 1–2 weeks)

Phases A2–A6 proceed only if measurement justifies the cost.

| Signal | Threshold suggesting "proceed" |
|---|---|
| p95 batch duration | > 10 minutes |
| p95 approval wait | > 5 minutes |
| Interrupted batches per week | ≥ 1 |
| Agent restarts during a live batch | ≥ 1 per month |
| Operator complaint "I lost a batch" | any |

All signals below: do not proceed. Re-measure quarterly.
Any threshold crossed: proceed to A2. Record the data + decision in `docs/learning/XX-langgraph-checkpointing.md` before any A2 code lands.

---

### Phase A2 — `batch_status` Taxonomy · DESIGN ONLY

Promotes batch identity from implicit `audit_events.batch_id` to a first-class row. Every later phase needs somewhere to record "the agent looked at this on resume and decided X."

**Migration 0004** (`errander/safety/migrations.py`):

```sql
CREATE TABLE batches (
  batch_id TEXT PRIMARY KEY,
  env_name TEXT NOT NULL,
  dry_run INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  status_reason TEXT,
  approver TEXT,
  plan_id TEXT,
  plan_hash TEXT
);
CREATE INDEX idx_batches_status ON batches (status);
CREATE INDEX idx_batches_started_at ON batches (started_at DESC);
```

Status values (StrEnum, new `errander/models/batches.py`):
- `RUNNING` — set on `init_batch_node` insert.
- `COMPLETED` — every planned action succeeded.
- `COMPLETED_WITH_FAILURES` — terminal but at least one action failed.
- `ABORTED` — fleet-level abort (window failed, no healthy targets, approval rejected/timeout).
- `NEEDS_OPERATOR_REVIEW` — reserved. Nothing in A2 produces it; A5+A6 will.

**Files:** new `errander/safety/batches.py` (`BatchStore` wrapper, logical separation), `errander/agent/graph.py` (insert RUNNING in `init_batch_node`, update in terminal nodes), `errander/web/server.py` (surface status in batch list), tests.

**Risk:** low. Additive schema, no state change.

---

### Phase A3 — Serialization Tests · DESIGN ONLY

Must land before A5. Once `SqliteSaver` is wired, every PR can silently break serialization. These tests are the guardrail; they pass today and lock in the contract.

For each `*GraphState` TypedDict in `errander/agent/graph.py`, `vm_graph.py`, `errander/agent/subgraphs/*.py`: build a realistic instance, round-trip through `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer`, assert equality and absence of:
- live aiosqlite / asyncssh connections
- `SandboxExecutor`, `FileLocker`, `ApprovalManager`, `SlackClient`, `LLMClient` instances
- secrets (any value whose key contains `key`, `token`, `password`, `secret`)
- raw output blobs > 4 KiB (forces A4 before A5)

**The 4 KiB cap will FAIL on some subgraphs today.** That's the point — failures form the A4 work list.

**Files:** new `tests/agent/test_state_serialization.py`. No source changes.

**Risk:** medium. First time anyone has audited what's actually in subgraph state. Expect surprises.

---

### Phase A4 — Move Big Artifacts Out · DESIGN ONLY

Checkpoints persist state at every super-step. A 5 MB `apt list --upgradable` blob in state = a 5 MB checkpoint per node transition.

**Migration 0005:**

```sql
CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  vm_id TEXT,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload BLOB NOT NULL
);
CREATE INDEX idx_artifacts_batch ON artifacts (batch_id);
```

State changes example: `PatchingGraphState.upgradable_packages: list[dict]` → `upgradable_packages_artifact_id: str`. Keep small metadata (counts, sizes, ≤50 package names) inline.

**Files:** new `errander/safety/artifacts.py`, migration 0005, every subgraph in `errander/agent/subgraphs/*.py` that holds large blobs (likely `patching.py`, `docker_prune.py`, `log_rotation.py`), reducer adjustments in `graph.py` if any blob-merging reducers exist, per-subgraph tests.

**Risk: highest in Project A.** Touches every subgraph and changes their state contract. Sequence per-subgraph PRs, not one mega-PR. Run full pytest after each.

**Stop and ask if** state genuinely cannot be moved out (value read mid-flow by a reducer). Acceptable fallback: keep inline but bound size.

---

### Phase A5 — SQLite Checkpointing · DESIGN ONLY

**Wiring:**
1. Add `langgraph-checkpoint-sqlite` to `pyproject.toml`.
2. In `errander/main.py:1134`, instantiate `SqliteSaver` against the same SQLite file (per SRE's "same DB acceptable" decision), pass to `.compile(checkpointer=saver)`.
3. `thread_id = batch_id` in invocation config.
4. Keep `graph_checkpoints` table logically distinct from `audit_events`.

**Safe-boundaries enforcement (key design point):** LangGraph checkpoints at every super-step; we cannot tell it "only checkpoint at these nodes." Instead, **the resume path enforces the boundary:**

- On `runs resume`, load last checkpoint, inspect `next`.
- Safe nodes: `approval_gate`, `dispatch_current_wave`, `check_wave_health`, `generate_plan_artifact`, `validate_window`, `validate_targets`, `generate_report`. Resume allowed.
- Any other `next` (inside `run_vm` or a subgraph): REFUSE resume. Error message directs operator to investigate the VM manually. Set `batches.status = NEEDS_OPERATOR_REVIEW`.

Safe-resume node allowlist: single constant in `errander/agent/graph.py`, unit-tested.

**Single-process enforcement.** New `agent_lease` table (migration 0006): PID + hostname + heartbeat timestamp. Refuse to start if another row exists with heartbeat < 60s old. Heartbeat every 30s. Not HA — a guardrail against accidentally running two agents against the same SQLite file.

**Deferred-execution overlap.** `is_deferred_replay` / `preloaded_plan_json` already implements "load artifact + verify hash + continue from approval-gate." Crash-resume needs the same. Refactor both to share one helper — do not duplicate.

**Files:** `pyproject.toml`, `errander/main.py`, `errander/agent/graph.py` (allowlist + helper), new `errander/safety/agent_lease.py`, migration 0006, tests for full kill-and-resume cycle.

**Risk:** medium. Biggest hazard: `Send()` payloads are serialised too. Any new field added to a fan-out payload must satisfy the same constraints as state.

---

### Phase A6 — `runs inspect/resume` CLI · DESIGN ONLY

```
errander runs list [--status RUNNING|NEEDS_OPERATOR_REVIEW|...] [--last N]
errander runs inspect <batch_id>
errander runs resume <batch_id> [--force --reason "..."]
```

- `runs list` — table from `batches`.
- `runs inspect` — batch row + last 20 audit events + last checkpoint (timestamp, `next` node, safe-to-resume yes/no).
- `runs resume` — invokes batch graph with saved checkpoint config. If `next` not in allowlist, refuses unless `--force` + `--reason` (writes new `OPERATOR_FORCE_RESUME` audit event).
- **No `runs abandon`** (per SRE).

**Files:** `errander/main.py` (CLI wiring), new `errander/commands/runs.py`, `errander/models/events.py` (new event), `RUN.md`, tests.

**Risk:** low. Read paths plus one invocation already covered by A5 tests.

---

## 5. Project B — Operational Learning Memory (#8)

Aggregate evidence-based facts about per-VM and per-action outcomes and surface them to the LLM. Layer A only — never writes infrastructure state.

### Background

From the review:
> Disk cleanup usually reclaims N GB on this VM.
> Patching often fails on this VM due to dpkg lock.
> This VM usually needs reboot after kernel updates.
> This action was repeatedly rejected by humans.
> This action has a high success rate in this environment.

These are SQL aggregations over `audit_events` + `ai_decisions` + `vm_disk_history`, returned as typed Pydantic models, cached briefly. Not a new long-term-memory subsystem — a queryable view of what's already recorded.

**Why no new table for facts:** materialising aggregations creates a freshness problem (when to refresh?). Compute on demand from existing data; cache per OperatorAssistant request. Errander's data volume is small (single fleet, monthly retention) — SQL is fast enough.

**Dependency:** Phase A1 strengthens the audit-query patterns this project will reuse. B1 can start once A1 lands; doesn't require waiting for the A-gate decision.

### Phase B1 — `VMFactsStore` · **DONE 2026-05-18**

New `errander/safety/vm_facts.py`. Pure read-only wrapper over existing stores. Returns Pydantic models:

```python
class ActionOutcomeFact(BaseModel):
    vm_id: str
    action_type: str
    success_rate: float        # 0.0 - 1.0
    sample_size: int           # last N=20 attempts
    last_failure_reason: str | None
    last_success_at: datetime | None

class VMRebootPatternFact(BaseModel):
    vm_id: str
    reboots_required_after_patching: int
    sample_size: int

class ActionRejectionFact(BaseModel):
    action_type: str
    rejections_last_90d: int
    rejection_reasons: list[str]   # from APPROVAL_REJECTED detail
```

API: `await store.action_outcomes(vm_id, action_type=None) -> list[ActionOutcomeFact]` etc.

**Files:** new `errander/safety/vm_facts.py`, tests in `tests/safety/test_vm_facts.py`, no migrations.

### Phase B2 — Wire Into OperatorAssistant · **DONE 2026-05-18**

Extend `OperatorAssistant._build_context` (at `errander/agent/operator_assistant.py:65`) to include a `vm_facts` section in the `FleetContext` model. Add fact summaries to the LLM prompt template via a new section "Operational history facts."

`FleetContext` (in `errander/models/analysis.py`) gets new fields:
- `action_outcomes: list[ActionOutcomeFact]`
- `reboot_patterns: list[VMRebootPatternFact]`
- `frequently_rejected_actions: list[ActionRejectionFact]`

Fallback: if `VMFactsStore` is None or queries fail, the existing context-build path continues to work — facts are additive.

**Files:** `errander/agent/operator_assistant.py`, `errander/models/analysis.py`, prompt template update wherever `_format_prompt` is defined, tests.

### Phase B3 — Operator-Facing CLI · DESIGN ONLY

```
errander vm-facts <vm_id> [--action <type>]
errander vm-facts --action <type>          # cross-fleet aggregate
```

Prints fact tables. Useful for SRE to spot-check whether facts match reality before trusting LLM summaries built on them. Three-table output: outcomes, reboot pattern, rejection counts.

**Files:** `errander/main.py` (CLI), new `errander/commands/vm_facts.py`, `RUN.md`, tests.

### Phase B4 — Tests + Doc · DESIGN ONLY

- `tests/agent/test_operator_assistant_facts.py` — assert facts are present in context and surfaced in fallback (non-LLM) summary path.
- `docs/learning/XX-operational-learning-memory.md` — what was built, why no new table, sample queries.

---

## 6. Project C — Runbook & Postmortem Memory (#9)

Bring authored runbooks and prior postmortems into OperatorAssistant context.

### Background

From the review:
> Layer A can safely use runbooks, prior incidents, previous maintenance reports, and audit history to answer:
> - Why did this batch fail?
> - Is this VM safe to patch tonight?
> - What should I check before restarting nginx?

This is content management, not just code. Layer A only.

### Decided 2026-05-18

**Storage: filesystem** — markdown files in a configured directory (default `./runbooks/`), authored in any editor, version-controlled separately. SQLite table and external wiki link-out were considered and rejected for v1 (former needs authoring tooling, latter needs per-customer integration).

### Phase C1 — Loader · DESIGN ONLY

- New `errander/safety/runbooks.py` — `RunbookStore` with `load_dir(path)` that recursively reads `.md` files, extracts YAML frontmatter (title, tags, applies_to, severity), keeps the body as text.
- Same for `./postmortems/` — same loader, different default directory.
- Configurable paths in `Settings` (`runbook_dir`, `postmortem_dir`).
- No content limits — loaded into memory at startup, reloaded on SIGHUP or via `errander runbooks reload`.

### Phase C2 — OperatorAssistant Retrieval · DESIGN ONLY

Add `gather_runbook_context(question, vm_ids, action_types)` to `OperatorAssistant`. Matching strategy: **keyword + tag match, NOT embeddings.**

- Tokenise question (drop stopwords).
- Score each runbook by overlap with title + tags + applies_to + first 200 chars.
- Return top N (default 3) above a minimum overlap score.

Why no embeddings: adds an embedding model + vector store + latency budget for marginal lift at Errander's runbook count (likely < 100). Revisit if corpus grows past ~500 runbooks.

Surface in `FleetContext`:
- `relevant_runbooks: list[RunbookExcerpt]`
- `relevant_postmortems: list[PostmortemExcerpt]`

LLM prompt template gets a "Reference material" section. Strict instruction: cite the runbook by slug when used.

### Phase C3 — Operator CLI · DESIGN ONLY

```
errander runbooks list
errander runbooks reload
errander runbooks show <slug>
```

Same for postmortems. No authoring CLI in v1 — markdown files are authored externally.

### Phase C4 — Tests + Sample Content · DESIGN ONLY

- `tests/safety/test_runbooks.py` — loader, frontmatter parsing, reload.
- `tests/agent/test_operator_assistant_runbooks.py` — keyword match returns expected runbook for a planted question.
- Add 2–3 example runbooks to `example/runbooks/` so the feature is demonstrable out of the box.
- `docs/learning/XX-runbook-memory.md`.

---

## 7. Project D — Historical Replay & AI Evals (#10)

Use past LLM decisions as an eval set. Catches: policy drift, prompt regressions, fallback parity, hallucinated citations.

### Background

From the review:
> Given the same VM state, does the agent produce the same safe plan?
> Did the LLM recommend an action outside policy?
> Did fallback behavior match expected priority?
> Did the report exaggerate or hide failures?

### Critical prerequisite discovered during planning

`AIDecisionStore` (`errander/safety/ai_audit.py:22`) stores `prompt_hash` (16-char SHA-256) and `response_raw`, but **NOT** the full prompt or rendered context. You can detect prompt drift (hash changed) but you cannot replay. **D1 must add full prompt and context-snapshot capture before any replay logic can be built.**

### Phase D1 — Full Prompt + Context Capture · **DONE 2026-05-18** (commit below)

Implemented: `prompt_full`, `context_snapshot`, `model_params` added to `ai_decisions`. Schema change is idempotent (ALTER TABLE with suppress on existing DBs; `_CREATE_TABLE_SQL` includes cols for fresh installs). `decisions.py` `prioritize_actions()` records full prompt + context at success and fallback call sites; no_llm path records context only. 16 new tests. 1969 tests total.

Commit: `feat: D1 full prompt + context capture in ai_decisions`

**Original design (for reference):**

**Migration 0007 (implemented as idempotent ALTER TABLE in `AIDecisionStore.initialize()` — not in shared `_MIGRATIONS`):** add columns to `ai_decisions`:

```sql
ALTER TABLE ai_decisions ADD COLUMN prompt_full TEXT;
ALTER TABLE ai_decisions ADD COLUMN context_snapshot TEXT;     -- JSON
ALTER TABLE ai_decisions ADD COLUMN model_params TEXT;          -- JSON: temperature, etc
```

`AIDecisionStore.record(...)` accepts and stores them. Existing 16-char `prompt_hash` stays — useful for fast drift detection.

**Storage concern:** full prompts can be 10–50 KB each. With ~10 LLM calls per batch, a year of daily batches is ~150 MB. **Confirmed acceptable by user 2026-05-18.** Add a retention CLI in D4.

**Files:** migration 0007, `errander/safety/ai_audit.py`, every caller of `record(...)` updated to pass the full prompt + context, tests.

### Phase D2 — Replay Harness · DESIGN ONLY

New `errander/commands/ai_replay.py`:

```
errander ai-replay --since 7d [--decision-type prioritize_actions] [--limit N]
errander ai-replay --batch-id <id>
```

For each captured decision:
1. Re-render prompt with stored context snapshot.
2. Call current LLM with same params.
3. Parse new response into the same Pydantic model the original used.
4. Run assertion functions (D3).
5. Record per-decision pass/fail to a fresh `ai_replay_runs` table (migration 0008).

Handles LLM nondeterminism by asserting on **structured properties**, not exact match.

### Phase D3 — Assertion Functions · DESIGN ONLY

New `errander/observability/ai_assertions.py`. Each function takes `(original_decision, replay_response, context)` → `AssertionResult`. Library to ship:

- `policy_compliance` — recommended actions are within `enabled_actions` for the env.
- `risk_tier_classification_stable` — same action gets same risk tier.
- `fallback_parity` — when forced into fallback mode (no LLM), output ordering matches priority constants.
- `citation_presence` — for OperatorAssistant responses, asserts citation field is non-empty when context contained relevant facts/runbooks.
- `no_blocked_action` — recommended actions never include the never-automate set (kernel ops, data deletion).

Each assertion has a config dict for thresholds (e.g., `risk_tier_classification_stable` tolerates 0 mismatches by default).

### Phase D4 — Reporting + Scheduling · DESIGN ONLY

```
errander ai-replay-report --since 30d
errander ai-replay --since 24h --schedule       # registers a daily APScheduler job
errander ai-decisions prune --older-than 90d    # storage retention
```

Report format: per-assertion pass rate, regression watchlist (assertions that flipped from passing to failing in the window), top failing prompts.

**Files:** `errander/commands/ai_replay.py`, new assertions module, migration 0008 (`ai_replay_runs` table), scheduler hook in `errander/scheduling/scheduler.py`, `RUN.md`, tests.

---

## 8. Project E — HITL Interrupt/Resume · DEFERRED (#4)

### Why deferred

Joint decision with SRE: this is the right end-state architecturally, but it's a 1–2 week refactor with broad blast radius (`ApprovalManager`, Slack poller, UI button, `main.py` invocation loop). It doesn't pay off without checkpointing (Project A) already in place. The SRE's narrow contract handles approval-wait via "store the pending plan" (which Project A already does via deferred execution + checkpointing of approval-gate state) — true interrupt semantics are not required for v1.

### Triggers to build

Build this when any of:
- Operators report "process restarts kill mid-approval batches" repeatedly (Phase A1 measurement will surface this).
- Approval wait p95 routinely > 30 minutes.
- Need to deploy the agent in a stateless / restart-tolerant environment (e.g., Kubernetes pod with rolling restarts).
- Project A's checkpoint-of-approval-state proves insufficient — i.e., a real crash happened mid-approval and resume left state in a confusing place.

### Design sketch (do not implement)

1. Split `approval_gate_node` into:
   - `generate_plan_artifact` (writes artifact + plan_hash)
   - `interrupt` (LangGraph `interrupt()` call — pauses graph, persists state via checkpointer)
   - `verify_plan_hash` (after resume, confirms hash unchanged)
2. Move `await_dual_approval` OUT of the graph. Approval becomes external:
   - Slack reaction handler → calls `graph.invoke(Command(resume={"approved": True, "user": "U123"}), config)`
   - UI button → same.
   - Both paths converge on a single `resume_with_decision(batch_id, decision)` helper.
3. `main.py` invocation switches from "await one long-running call" to "invoke → catch `Interrupt` → external wait → resume." Adds an explicit terminal state for the original `invoke()` call.
4. Idempotency: the `plan_hash` becomes the resume idempotency key. Re-submitting an approval for an already-resumed batch returns the recorded decision instead of double-executing.

**Prerequisites this design needs:** all of Project A (checkpointing actually persists the interrupt state), plus a refactored `ApprovalManager` whose pending state is durable, not in-memory.

---

## 9. Declined — OperatorAssistant as LangGraph (#5)

### Why declined

`OperatorAssistant` is read-only. It doesn't crash mid-flight, doesn't need resume, doesn't need approval, doesn't fan out to many parallel side-effecting calls. Turning it into a `StateGraph` adds LangGraph for marketing reasons rather than safety reasons — which is exactly what the rest of the review warns against. The current Python class structure is sufficient.

### What would change the decision

Build this only if:
- Operators report investigation latency is too high and parallel multi-source context gathering would help (currently sequential).
- The investigation steps become composable/extensible enough that a graph helps more than a function refactor (e.g., third-party plugins need to add steps).
- Long-running investigations need to pause for additional input or background data fetches.

Today none of those is observed.

---

## 10. Execution Order

| Order | Item | Status | Trigger to proceed |
|---|---|---|---|
| 1 | A1 — Measurement + B1 → B2 (in parallel) | **DO NOW** | (none) |
| 2 | Project A decision gate | — | After 1–2 weeks of A1 data, any threshold crossed |
| 3 | A2 → A3 → A4 → A5 → A6 (sequential) | DESIGN ONLY today | Gate cleared |
| 4 | B3 → B4 | DESIGN ONLY today | After B1–B2 land |
| 5 | C1 (with user input) → C2 → C3 → C4 | DESIGN ONLY today | After operators ask "I want runbook context in assistant answers" |
| 6 | D1 → D2 → D3 → D4 | DESIGN ONLY today | After operators ask "I want to know if LLM quality is regressing" |
| 7 | E (full design + implementation) | DEFERRED | Any trigger in §8 |

**Why this order:** Project A is the safety-critical durability work — it gates trust in the rest. Project B uses the audit-query patterns Phase A1 strengthens, so it's parallelizable. Projects C and D are LLM-quality work — important but not safety-critical, prioritise based on operator pull. Project E is the end-state architecture for HITL, but only needed if A's checkpointing doesn't solve the practical problem.

**Critically: A3 must land before A5.** Serialization tests written after checkpointing is locking the barn after the horse leaves.

---

## 11. Open Items Sonnet Must Flag (do not silently decide)

1. **Phase A1.** If the orphan-batch scan flags many false positives because some real terminal events aren't recorded in `audit_events`, that's a finding — report it. Don't paper over it by adding new event types in A1.
2. **Phase A1.** If `--measure-durability` shows p95 batch duration < 60s and zero interrupted batches in available history, surface that loudly. It's the strongest possible argument for **not** proceeding to A2.
3. **Phase A1.** If existing Prometheus histogram buckets are too coarse for observed durations (everything in one bucket), note it. Bucket tuning is a separate small PR — do not do it in A1.
4. **Project A, A4.** If you discover state that genuinely cannot be moved out of subgraph state, stop and ask. Acceptable fallback (with user sign-off) is keeping it inline but bounded.

Previously open items now decided (2026-05-18):
- Runbook storage location: **filesystem** (`./runbooks/*.md`).
- Full prompt + context storage in Project D: **acceptable** (~150 MB/year).
- Phase A1 measurement window: **14 days** (default).
- Project B timing: **start in parallel with Phase A1** (B1–B2 ship alongside A1).

---

## 12. Reference Pointers

Project A:
- LangGraph state and `Send()` fan-out: `errander/agent/graph.py:87,1564,1715`
- Wave dispatcher: `errander/agent/graph.py:1594`
- Bare `.compile()` (no checkpointer today): `errander/main.py:1155`
- Approval polling: `errander/agent/graph.py:1196` + `errander/safety/approval.py:280`
- Audit store: `errander/safety/audit.py`
- Migrations registry: `errander/safety/migrations.py:28`
- Prometheus metrics already exposed: `errander/observability/metrics.py:72`
- `EventType` enum: `errander/models/events.py:15`

Project B:
- `OperatorAssistant`: `errander/agent/operator_assistant.py:40`
- `FleetContext` / `AssistantResponse` models: `errander/models/analysis.py`
- Audit, AI decisions, disk history stores: `errander/safety/{audit,ai_audit,disk_history}.py`

Project D:
- `AIDecisionStore`: `errander/safety/ai_audit.py:22` — **stores prompt_hash, not full prompt** (the D1 gap)
- LLM client interface: `errander/integrations/llm.py`

Cross-cutting:
- AI Architecture / Layer A vs B invariant: `docs/AI-ARCHITECTURE.md`, CLAUDE.md "AI Safety Invariant"
- Source review: `ai_sre_langgraph_agentic_review.md`

---

## 13. Doc Sync (CLAUDE.md mandatory)

For every commit:
- `STATUS.md` — Last Updated, In Progress, Files Changed
- `docs/command-log.md` — every shell command run
- `tasks/todo.md` — mark complete on landing
- `tasks/lessons.md` — only if there was a correction or surprise

For each phase landed, also update:
- `RUN.md` — new CLI commands
- `docs/learning/XX-<feature>.md` — what was built and why
- `README.md` test count if it changed
- `docs/SECRETS.md` / `docs/LLM-PROVIDERS.md` if relevant

**Do NOT** update README or CLAUDE.md feature lists to claim a feature exists until it's actually shipped. Phase A1 ships measurement only — do not claim checkpointing.

Commit message format per CLAUDE.md: one line, `type: short description` under 72 chars.
