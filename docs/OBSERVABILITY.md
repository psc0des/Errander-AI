# Errander-AI — Observability

How to see what Errander-AI is thinking and doing. This is the canonical reference for **operators** (how to monitor a live deployment) and for **coding agents** (Opus/Sonnet — how the surfaces map to the architecture so you extend the right one).

It builds directly on the two-layer safety model in [`AI-ARCHITECTURE.md`](AI-ARCHITECTURE.md). Read that first if "Layer A" and "Layer B" aren't already second nature.

> **One-sentence model:** **Layer A (the brain) reasons; Layer B (the hands) acts.** Each layer has its own observability surface, and they are *not* interchangeable — the reasoning is not a record of what happened to your infrastructure.

---

## The most important distinction: built-in vs. bring-your-own

Errander draws a hard line between **what it produces and owns** and **what you point at it**. Get this wrong and you'll either trust a tool that isn't configured, or treat an external dashboard as the system of record. It is neither.

**Built-in — Errander produces these. In-network, always-on, zero external dependency. These are the system of record.**

| Surface | Layer | Question it answers | Authoritative for | Where |
|---|---|---|---|---|
| **Audit trail** | B (hands) | *What exactly did the agent do?* | **What changed on infrastructure** | `audit_events` table (PostgreSQL) |
| **AI decision log** | A (brain) | *Why did the agent recommend this?* | What the LLM was asked + answered | `ai_decisions` table (PostgreSQL) |
| **`/metrics` endpoint** | B (mostly) | *Is execution healthy / fast / frequent?* | (raw counters — see below) | HTTP `:9090/metrics` |
| **Monitoring dashboard** | B (mostly) | *How healthy is the fleet over time?* | aggregate view (not per-event) | `GET /ui/monitoring` (web UI) |
| **Structured JSON logs** | both | *What was the play-by-play / diagnostics?* | nothing — diagnostics, may rotate away | stdout |

**Bring-your-own — external tools you supply and run. Strongly recommended, but NOT part of Errander, tool-agnostic, and never the system of record. They are views/consumers layered on the built-ins.**

| External tool (or equivalent) | Consumes / observes | Layer | Status |
|---|---|---|---|
| **Prometheus** (+ **Grafana**) | scrapes the `/metrics` endpoint | B (health) | ✅ supported; **dedicated external VM only** — not the agent VM. Run `scripts/install-prometheus.sh` + `scripts/install-grafana.sh` on a separate monitoring VM; Grafana dashboard auto-provisioned |
| **LangSmith** *or any LangGraph tracer* | the Layer-A reasoning graph | A (brain) | 🔜 planned (after Prometheus); off by default |
| **ELK / Loki / any log store** | ingests the stdout JSON logs | diagnostics | bring-your-own (see `example/ELK/`) |

> **Strong recommendation, explicit non-ownership.** For deep Layer-A tracing and for log search, we *recommend* an external tool — LangSmith (or an equivalent of your choice) for reasoning traces, ELK/Loki for logs — but Errander does not bundle, require, or depend on any of them. Pick what fits your stack. If you run none of them, you lose **no authoritative data**: the audit trail and AI decision log are still complete and in-network.

The golden rule of which-to-trust:

- **"Did it happen, and what exactly?"** → **audit trail** (built-in). Never Prometheus, never LangSmith, never a log dashboard.
- **"Is the fleet maintenance healthy in aggregate?"** → **`/ui/monitoring`** (built-in dashboard — approval funnel, safety signals, action trends, duration averages; 24h / 7d / 30d time-range selector). Prometheus + Grafana only if you have a dedicated external monitoring VM and need alerting.
- **"Why did the LLM choose that?"** → **AI decision log** (built-in, always there) or **LangSmith** (external, richer, planned).
- **"What was the diagnostic play-by-play?"** → **structured logs** (built-in stream) → searchable via **ELK/Loki** (external).

> **For coding agents (Claude/Opus/Sonnet):** treat the built-in column as *guaranteed to exist* — write to it, query it, rely on it. Treat the bring-your-own column as *may or may not be configured* — never assume a LangSmith/Prometheus/ELK is present, never make Errander depend on one, and never route Layer B execution data through an external tool. External tools observe; they never participate.

---

## 1. Audit trail — Layer B, the record of actions

**This is the source of truth for what happened to your infrastructure.** It is deterministic Python writing immutable rows before *and* after every action — no LLM involved.

- **Model:** `errander/models/events.py` → `AuditEvent` (`event_type`, `batch_id`, `vm_id`, `action_type`, `detail`, `timestamp`, `metadata`).
- **Store:** `errander/safety/audit.py` → `AuditStore` (async PostgreSQL). Query via `get_events(batch_id, vm_id, event_type, action_type, limit)`.
- **Storage:** PostgreSQL at `ERRANDER_AUDIT_DB_URL` (e.g. `postgresql://errander:errander@localhost:5432/errander`).
- **Guarantee:** for destructive actions, **one row per object** (the Exact-Object Approval invariant — see [CLAUDE.md](../CLAUDE.md)). N approved objects ⇒ N audit outcomes, never a "batch removed N" shortcut.

### Event types (grouped)

These are the categories Layer B emits (`EventType` in `events.py`):

- **Batch lifecycle:** `batch_started`, `batch_completed`, `fleet_abort`, `execution_deferred`, `deferred_execution_started`, `operator_force_resume`
- **Action lifecycle:** `action_planned`, `action_started`, `action_completed`, `action_failed`
- **Rollback:** `rollback_started`, `rollback_completed`, `rollback_failed`
- **Approval:** `approval_requested`, `approval_granted`, `approval_rejected`, `approval_timeout`
- **Safety preflight:** `sudo_preflight_failed`, `target_preflight_failed`, `os_mismatch`, `target_readiness_blocked`, `preflight_lock_detected`, `preflight_lock_clear`, `disk_gate_blocked`
- **Drift:** `drift_baseline_saved`, `drift_detected`, `drift_kind_baseline_saved`, `drift_kind_changed`
- **SRE signals:** `reboot_required_detected`, `service_health_regression`, `disk_usage_captured`, `failed_ssh_logins_observed`
- **Daily probe (read-only):** `daily_probe_started`, `daily_probe_complete`, `daily_probe_failed`
- **service_restart:** `service_restart_requested`, `service_restart_unit_not_allowed`, `service_restart_approved`, `service_restart_rejected`, `service_restart_executed`, `service_restart_verify_ok`, `service_restart_verify_failed`
- **docker_hygiene (per-object):** `docker_hygiene_object_removed`, `docker_hygiene_object_drift_skipped`, `docker_hygiene_object_remove_failed`
- **Agent proposals (detect-and-propose, fable-plan Phases 1-4):** `proposal_created`, `proposal_refreshed`, `proposal_approved`, `proposal_rejected`, `proposal_snoozed`, `proposal_expired`, `proposal_execution_started`, `proposal_execution_completed`, `proposal_execution_failed`, `proposal_suppressed` (Phase 4 — logged when a repeatedly-rejected `(vm_id, action_type)` refuses a fresh auto-proposal; `detail` carries the rejection count and cooldown-until date). See [AI-ARCHITECTURE.md](AI-ARCHITECTURE.md) → "Detect-and-propose".
- **Config:** `settings_changed`, `inventory_changed`
- **User management (R2 RBAC):** `user_created`, `user_deleted`, `user_groups_changed`, `user_password_changed` — every account/membership change records the acting identity (`cli:<os-user>` or `migration:env`); approval rows additionally carry `decided_by` (`ui:<username>`) + `decided_by_group`

### How to read it

```bash
# Recent batch summaries
uv run python -m errander --audit --batches

# All events for one batch
uv run python -m errander --audit --batch-id <batch-id>

# Filter by VM, action type, or event type
uv run python -m errander --audit --vm-id prod/web-01
uv run python -m errander --audit --action-type patching --last 50
uv run python -m errander --audit --event-type action_started

# Full plan snapshot (fallback when a Slack message is truncated)
uv run python -m errander --plan-show <plan-id>
```

Web UI: `/ui/batches`, `/ui/batches/{id}` (per-event), `/ui/vms/{vm_id}` (history across batches).

---

## 2. AI decision log — Layer A, the record of reasoning (built-in)

Every LLM call that influences a decision is logged for explainability. This is **Layer A**: it captures what the model was asked and what it returned — *not* whether anything was executed.

- **Store:** `errander/safety/ai_audit.py` → `AIDecisionStore`. Query via `get_decisions(batch_id, vm_id, decision_type, limit)` and `get_decision_by_id(id)`.
- **Record (`AIDecision`):** `decision_type` (e.g. `planning_note`, `operator_assistant`, `generate_report`, and — when `--ask --agentic` is on, or when probe-triggered investigations are enabled (fable-plan Phase 3) — `investigation_agent_step` per tool hop + `investigation_agent` for the final answer), `model`, `base_url` (redacted), `prompt_template_id`, `prompt_hash`, `prompt_full` (redacted), `response_raw`, `outcome` (`success` / `fallback` / `no_llm`), `latency_ms`, `context_snapshot` (incl. redaction + budget stats), `model_params`, `timestamp`. `batch_id` distinguishes the two callers: `"ask"` for CLI-invoked investigations, `"probe-trigger:{vm_id}"` for probe-triggered ones — the latter also doubles as the dedup marker the trigger queries before launching a new investigation for that VM.
- **`planning_note` (R1):** the only LLM call in the batch-planning path. The plan itself (`prioritize_actions()`) is 100% deterministic — `generate_planning_note()` produces a short (≤700 char) informational note about the already-finalized plan, stored as `ai_note` inside `vm_plans` and rendered on the approval surfaces under "AI analysis — informational only". The note can never change which actions run, in what order, or with what parameters. Historical `prioritize_actions` rows from before R1 remain replayable (`evals/replay.py`).
- **Redaction:** prompts pass through `ContextRedactor` before storage (secrets stripped). Source IDs in LLM output are validated against known sources; hallucinated citations are dropped.

### How to read it

```bash
# Recent AI decisions (optionally filter by type / batch)
uv run python -m errander --ai-decisions --decision-type planning_note --last 20

# Full detail for one decision (prompt, response, latency, context)
uv run python -m errander --ai-decision-show <decision-id>
```

Web UI: `/ui/ai-decisions`, `/ui/ai-decisions/{id}`.

**Why this matters operationally:** a rising `fallback` / `no_llm` outcome rate means the LLM is unreachable and the agent is running on hardcoded priority ordering — correct behaviour (the agent never blocks on the LLM), but worth knowing.

---

## 3. Prometheus metrics — Layer B execution health

The agent **exposes** `/metrics` on its UI port (default `9090`, `ERRANDER_METRICS_PORT`) in Prometheus text format. It does **not** bundle a Prometheus server.

> **Built-in view first:** the **`/ui/monitoring`** dashboard (see §1 above) visualises these same metrics in-process — action trends, approval funnel, safety signals, avg durations, 24h / 7d / 30d time-range selector — with no external tool required. Prometheus + Grafana are only worth adding if you have a **dedicated external monitoring VM** and need time-series history across restarts or alertmanager-based paging.

**Optional — install on a dedicated external monitoring VM** (not the agent VM):

```bash
bash scripts/install-prometheus.sh   # listens on :9091, scrape target = <agent-vm>:9090
bash scripts/install-grafana.sh      # Grafana on :3000, dashboard auto-provisioned
```

Distro-agnostic (official binary + systemd). See [SETUP.md → Monitoring stack](../SETUP.md) and [README.md → Observability](../README.md).

### Metrics exposed

| Metric | Layer | Meaning |
|---|---|---|
| `errander_actions_total{action_type,status,vm_id}` | B | Actions executed, by outcome and VM |
| `errander_action_duration_seconds{action_type}` | B | Per-action execution time |
| `errander_batch_duration_seconds` | B | Full batch wall-clock time |
| `errander_ssh_errors_total{vm_id,reason}` | B | SSH connection/command failures (`execution/ssh.py`) |
| `errander_vm_lock_held_seconds{vm_id}` | B | VM lock hold duration |
| `errander_approval_wait_seconds` | B | Time blocked waiting for human approval |
| `errander_wave_health_checks_total{wave,outcome}` | B | Per-wave rollout health checks |
| `errander_agent_starts_total` | B | Agent process startups (restart proxy) |
| `errander_batches_interrupted_total` | B | Batches that started but never hit a terminal event |
| `errander_llm_requests_total{outcome}` | **A** | LLM call outcomes — the one Layer-A signal here |

> **The one exception:** `errander_llm_requests_total` is emitted by the agent but reflects **Layer A** (the brain). It's the *only* Layer-A data Prometheus sees — call outcomes (`success`/`fallback`/`timeout`/`error`), never prompts or reasoning. Use it as a cheap "is the LLM healthy / how often are we falling back" gauge. For the *why*, go to the AI decision log or LangSmith.

> **Don't confuse with the other Prometheus direction.** This section is **Prometheus → Errander** (monitoring the agent). Separately, the agent can *read* target-VM metrics from a Prometheus *you* run (**Errander → Prometheus**, via `ERRANDER_PROMETHEUS_BASE_URL`) to inform Layer A — see SETUP.md. Same tool, opposite arrows.

---

## 4. External Layer-A tracing — LangSmith *or equivalent* (wired, opt-in)

> **Status: wired (fable-plan Phase 5). External, optional, bring-your-own, off by default.** LangSmith is the **reference** choice — but it is **a recommendation, not a dependency**, and any equivalent tracer of your choice is fine; the mechanism below (`wrap_openai`) is LangSmith-specific, so an alternate tracer would need its own equivalent client-wrapping integration.

> **Correction to the original design note:** LangSmith does **not** "attach with no code changes via env vars" for Errander's Layer A reasoning — that claim only holds for LangGraph-*orchestrated* nodes. Errander's actual Layer A calls (`OperatorAssistant`, `InvestigationAgent`, the advisory planning-note/report generators) are hand-rolled OpenAI-SDK calls through `LLMClient`, never LangGraph nodes — LangGraph is used only for Layer B's deterministic batch graph, which must never be traced (see the design constraint below). The real, now-implemented mechanism: `LLMClient.__init__` lazily wraps its internal `AsyncOpenAI` client with [`langsmith.wrappers.wrap_openai`](https://docs.smith.langchain.com/) whenever `langsmith.utils.tracing_is_enabled()` is true — which itself reads the standard `LANGSMITH_TRACING` (or legacy `LANGCHAIN_TRACING_V2`) / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` env vars, so **the env-var-only activation experience is preserved** even though the underlying mechanism is client-wrapping, not LangGraph auto-instrumentation. Set those env vars and every `LLMClient` call — planning note, report, deterministic `--ask`, and the agentic loop's tool-calling hops — is traced with zero further code changes on your part. `langsmith` ships as a transitive dependency of `langchain-core`/`langgraph` (both already required for Layer B), so no new dependency was added; if it's ever absent, wrapping is skipped and the client degrades to untraced rather than failing (tracing is observability, never load-bearing — see `errander/integrations/llm.py::_maybe_wrap_for_tracing`).
>
> **Why wrapping `LLMClient` preserves the Layer-A-only boundary:** this class is never imported by any execution sub-graph or the SSH executor (verified: zero matches for `LLMClient(` under `agent/subgraphs/` and `execution/`) — every caller is Layer A reasoning (text/recommendations only), regardless of whether that reasoning happens to be invoked from a CLI command or from an advisory node inside the Layer B batch graph (e.g. the planning note). The tracer never sits in an execution path.

Once enabled, [LangSmith](https://docs.smith.langchain.com/) gives per-call traces, latency breakdowns, and token usage — a richer *view* of the same Layer-A reasoning the built-in AI decision log already records.

### What it adds vs. what's redundant or N/A for Errander

Errander's Layer A is mostly structured single-shot LLM calls (planning note, report, the deterministic `--ask` operator assistant) plus **one bounded tool-using loop**: the opt-in agentic investigation engine (`--ask --agentic`, default OFF — fable-plan Phase 2). Right-size the LangSmith investment accordingly:

| LangSmith panel | Value to Errander | Why |
|---|---|---|
| **Traces** / **Run Types** | **High — genuinely new** | The visual node/edge trace tree is the main thing the built-in decision log can't show. Latency matters most on the self-hosted vLLM/T4 path (60s timeout). |
| **Feedback Scores** | **Medium / future** | Pairs with the existing replay-eval (`--ai-eval-replay`) for prompt-regression. Requires you to attach feedback — not automatic. |
| **LLM Calls** (count/latency) | **Low — redundant** | Already covered by `errander_llm_requests_total` (Prometheus) + `latency_ms`/`outcome` (AI decision log). |
| **Cost & Tokens** | **Conditional** | Important on a **cloud LLM** (real $); near-irrelevant on **self-hosted vLLM** (your own GPU). |
| **Tools** | **Conditional — relevant when `--ask --agentic` is enabled** | The deterministic `--ask`, planning note, and scheduled batch make no tool calls. The opt-in agentic investigation loop (fable-plan Phase 2) DOES make read-only tool calls; each hop is recorded in the built-in decision log as `decision_type="investigation_agent_step"`, and LangSmith's tool trace becomes the richer external view. Default OFF. |

### Design constraints (must hold when integrated)

- **Layer A only.** It observes the brain. It must **never** be wired into the Layer B execution path — that path stays deterministic with no external tracer in the loop. (See the AI Safety Invariant.)
- **Off by default, env-var gated, swappable.** On in dev/staging; a deliberate opt-in elsewhere; replaceable with any equivalent tracer.
- **Egress caveat.** LangSmith's default backend is LangChain's cloud — enabling it sends prompt contents (VM hostnames, log excerpts, package lists) off-network, which conflicts with the no-egress posture. Treat it as a **dev/staging** aid, not a no-egress-prod dependency.

It complements, never replaces, the built-in AI decision log (which stays the in-network, always-on system of record).

---

## What Errander can see — the fixed signal menu (Layer A inputs)

Everything above is about *observing Errander*. This section is the inverse: **what Errander can observe about your fleet**, and the hard limit on it.

Errander gathers signals through a **fixed menu of developer-built probes and queries**. Each query is a hardcoded template written ahead of time; at runtime only **parameters** are filled in (the VM host from inventory, a time window, a result limit). **The LLM never composes a query, and the operator can't supply one** — there is no query language exposed.

### The menu today

| Signal | How it's gathered | Scope | Needs |
|---|---|---|---|
| Pending packages, OS facts | SSH discovery (`apt list --upgradable`, etc.) | per-VM | SSH |
| Disk usage **+ growth trend** | SSH `df -B1` → recorded to `VMDiskHistoryStore` → slope computed (`disk_trend.py`) | per-mountpoint, over a trailing window | SSH |
| Reboot-required | SSH probe (`reboot_check.py`) | per-VM | SSH |
| Service health | systemd state via SSH (`service_check.py`) | system services | SSH |
| Failed SSH logins | journald `ssh`/`sshd` + `/var/log/auth.log`,`secure` (`failed_logins.py`) | **system** (SSH auth) | SSH |
| Config drift | baselines for `sudoers`, `authorized_keys`, `listening_ports`, `scheduled_jobs` | per-VM | SSH |
| CPU / Memory / Load (point-in-time) | 3 fixed PromQL queries (`integrations/prometheus.py`) | per-VM | Prometheus opted in |
| Top error/warn log patterns | 1 fixed Elasticsearch query, host-aggregated (`integrations/elk.py`) | **host-level, not app-specific** | ELK opted in |

Two things this table makes explicit:

- **Disk *trend* is covered — but not via Prometheus.** The Prometheus queries are CPU/mem/load point-in-time only; disk growth comes from the separate SSH-`df`→history→slope pipeline. Same kind of question ("how is disk trending?"), dedicated deterministic mechanism.
- **Log reading is system / host level, never app-targeted.** ELK aggregates the top errors for the *whole host*; the SSH paths read *system* logs (SSH auth, systemd). There is **no** "tail app X's logfile" capability.

### What happens when a signal isn't on the menu

If a question needs data outside this menu — say, per-process disk I/O, network retransmits, or a specific application's logs — Errander does **not** improvise:

1. **It will not generate a new query.** That's the deterministic design — reproducible and auditable, no surprises.
2. **It proceeds with what it has.** Every probe degrades gracefully (SSH failure → `None`/`[]`, Prometheus/ELK failure → `[]`); a missing signal is simply absent, never a crash, never a block.
3. **Adding a signal is a code change, not a config flag and not an LLM decision.** A developer writes a new probe/query (and for a new *action*, per CLAUDE.md: a new sub-graph + manifest + risk tier + rollback strategy). It's reviewed and tested, not composed on the fly.

So for the scheduled maintenance batch and the `--ask` operator assistant, Errander can only "see" what someone pre-built a probe/query for. This is a deliberate trade-off, not an oversight — it's what keeps the gathered context bounded, reproducible, redactable, and cheap (one LLM call, not a tool loop).

> **An opt-in agentic version that *composes* read-only queries live (a bounded ReAct loop, `--ask --agentic`, default OFF) is now built** (fable-plan Phase 2): it loosens the fixed menu *for that path only* — the LLM chooses which read-only tools to call, within a tool-call and wall-clock budget, and falls back to this deterministic path on any failure. Each hop is redacted and audited (`investigation_agent_step`). The scheduled maintenance batch and the default `--ask` still use the fixed menu — reproducible, cheap, one call. (The separate `/ui/chat` console remains out of core — see the Roadmap note in `README.md`.)

---

## For coding agents (Opus / Sonnet)

When you add or change observability, classify it first — **"Is this Layer A or Layer B?"** — exactly as in [`AI-ARCHITECTURE.md`](AI-ARCHITECTURE.md). Then:

**Where each surface lives in code:**

| Surface | Code |
|---|---|
| Audit trail | `errander/safety/audit.py` (`AuditStore`), `errander/models/events.py` (`EventType`, `AuditEvent`) |
| AI decision log | `errander/safety/ai_audit.py` (`AIDecisionStore`, `AIDecision`) |
| Prometheus metrics + `/metrics` server | `errander/observability/metrics.py` (metric singletons + `start_metrics_server`) |
| Metric wiring (where counters increment) | `errander/agent/graph.py`, `errander/agent/vm_graph.py`, `errander/safety/approval.py`, `errander/execution/ssh.py` |

**Rules when extending:**

- **Adding a new action (Layer B):** it MUST emit audit events before and after execution. Destructive actions MUST emit **one event per object** (`docker_hygiene` per-object events are the reference). Increment `errander_actions_total` / `errander_action_duration_seconds`. **Never** add an LLM, MCP, or tool call to an execute node.
- **Adding a new LLM call (Layer A):** log it via `AIDecisionStore.log(AIDecision(...))` with `decision_type`, timing, `outcome`, and a **redacted** prompt. Keep the store optional (`ai_decision_store: AIDecisionStore | None = None`) so tests/CLIs without a DB still work.
- **Adding a metric:** define it as a module-level singleton in `metrics.py` on the shared `REGISTRY`; label sparingly (high-cardinality labels like raw IDs blow up Prometheus). State its layer in the metric's docstring.
- **A new audit event type:** add it to `EventType` in `events.py`; if it's destructive/object-level, mirror the docker_hygiene per-object pattern. Migration tests lock the schema — update them.
- **LangSmith:** when wiring it, gate on env vars, restrict to Layer A graphs, and add nothing to Layer B. Do not make it a hard dependency.

**The invariant that governs all of this:** the audit trail is authoritative for *actions*; metrics are authoritative for *aggregate health*; the AI decision log + LangSmith are authoritative for *reasoning*. Keep them in their lanes — never let a reasoning trace stand in for an action record, and never let an LLM into the audited execution path.

---

## See also

- [`AI-ARCHITECTURE.md`](AI-ARCHITECTURE.md) — the canonical two-layer safety model
- [`../README.md`](../README.md) → Observability — operator quick-start
- [`../SETUP.md`](../SETUP.md) → both Prometheus directions
- [`../RUN.md`](../RUN.md) → metrics + logs at runtime
- [`SECRETS.md`](SECRETS.md) — env vars referenced here
