# Errander-AI — Observability

How to see what Errander-AI is thinking and doing. This is the canonical reference for **operators** (how to monitor a live deployment) and for **coding agents** (Opus/Sonnet — how the surfaces map to the architecture so you extend the right one).

It builds directly on the two-layer safety model in [`AI-ARCHITECTURE.md`](AI-ARCHITECTURE.md). Read that first if "Layer A" and "Layer B" aren't already second nature.

> **One-sentence model:** **Layer A (the brain) reasons; Layer B (the hands) acts.** Each layer has its own observability surface, and they are *not* interchangeable — the reasoning is not a record of what happened to your infrastructure.

---

## The four surfaces at a glance

| Surface | Layer | Question it answers | Authoritative for | Status |
|---|---|---|---|---|
| **Audit trail** | B (hands) | *What exactly did the agent do?* | **What changed on infrastructure** | ✅ built-in |
| **AI decision log** | A (brain) | *Why did the agent recommend this?* | What the LLM was asked + answered | ✅ built-in |
| **Prometheus metrics** | B (mostly) | *Is execution healthy / fast / frequent?* | Aggregate execution health | ✅ built-in (scraper opt-in) |
| **LangSmith traces** | A (brain) | *How did the LangGraph reasoning flow, step by step?* | Rich Layer-A debugging + evals | 🔜 planned (after Prometheus) |

The golden rule of which-to-trust:

- **"Did it happen, and what exactly?"** → **audit trail**. Never Prometheus, never LangSmith.
- **"Is the fleet maintenance healthy in aggregate?"** → **Prometheus**.
- **"Why did the LLM choose that?"** → **AI decision log** (built-in) or **LangSmith** (richer, planned).

---

## 1. Audit trail — Layer B, the record of actions

**This is the source of truth for what happened to your infrastructure.** It is deterministic Python writing immutable rows before *and* after every action — no LLM involved.

- **Model:** `errander/models/events.py` → `AuditEvent` (`event_type`, `batch_id`, `vm_id`, `action_type`, `detail`, `timestamp`, `metadata`).
- **Store:** `errander/safety/audit.py` → `AuditStore` (async SQLite v1, PostgreSQL planned v2). Query via `get_events(batch_id, vm_id, event_type, action_type, limit)`.
- **Storage:** SQLite at `ERRANDER_AUDIT_DB_URL` (e.g. `errander.sqlite`).
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
- **Config:** `settings_changed`, `inventory_changed`

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
- **Record (`AIDecision`):** `decision_type` (e.g. `prioritize_actions`, `operator_assistant`), `model`, `base_url` (redacted), `prompt_template_id`, `prompt_hash`, `prompt_full` (redacted), `response_raw`, `outcome` (`success` / `fallback` / `no_llm`), `latency_ms`, `context_snapshot` (incl. redaction + budget stats), `model_params`, `timestamp`.
- **Redaction:** prompts pass through `ContextRedactor` before storage (secrets stripped). Source IDs in LLM output are validated against known sources; hallucinated citations are dropped.

### How to read it

```bash
# Recent AI decisions (optionally filter by type / batch)
uv run python -m errander --ai-decisions --decision-type prioritize_actions --last 20

# Full detail for one decision (prompt, response, latency, context)
uv run python -m errander --ai-decision-show <decision-id>
```

Web UI: `/ui/ai-decisions`, `/ui/ai-decisions/{id}`.

**Why this matters operationally:** a rising `fallback` / `no_llm` outcome rate means the LLM is unreachable and the agent is running on hardcoded priority ordering — correct behaviour (the agent never blocks on the LLM), but worth knowing.

---

## 3. Prometheus metrics — Layer B execution health

The agent **exposes** `/metrics` on its UI port (default `9090`, `ERRANDER_METRICS_PORT`) in Prometheus text format. It does **not** bundle a Prometheus server.

**Install a controller-node Prometheus** (scrapes the agent's own `/metrics`):

```bash
bash scripts/install-prometheus.sh   # also offered as an opt-in step in bootstrap.sh
```

Distro-agnostic (official binary + systemd), listens on **`:9091`** to avoid the agent's `:9090`. See [SETUP.md → Monitoring the agent with Prometheus](../SETUP.md) and [README.md → Observability](../README.md).

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

## 4. LangSmith — Layer A tracing (planned)

> **Status: planned, not yet wired.** Target: integrate after the Prometheus path is settled. This section documents the intended design so it's built consistently.

Because the decision engine is **LangGraph**, [LangSmith](https://docs.smith.langchain.com/) attaches with no code changes via env vars (`LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`) and gives per-node/edge traces, latency breakdowns, token usage, and prompt-regression evals — a richer view of the same Layer-A reasoning the AI decision log records.

**Design constraints (must hold when integrated):**

- **Layer A only.** LangSmith observes the brain. It must **never** be wired into the Layer B execution path — that path stays deterministic with no external tracer in the loop. (See the AI Safety Invariant.)
- **Off by default, env-var gated.** On in dev/staging; a deliberate opt-in elsewhere.
- **Egress caveat.** LangSmith's default backend is LangChain's cloud — enabling it sends prompt contents (VM hostnames, log excerpts, package lists) off-network, which conflicts with the no-egress posture. Treat it as a **dev/staging** aid, not a no-egress-prod dependency.

LangSmith complements, never replaces, the built-in AI decision log (which stays the in-network, always-on record).

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
