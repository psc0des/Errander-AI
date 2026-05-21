# Errander-AI — AI Architecture

> **MCP belongs in the operator brain, not in the execution hands.**

> **Layer A may investigate and recommend. Layer B alone may execute, and only through deterministic, approved, audited workflows.**

This document is the canonical safety model for Errander-AI. Every AI integration, every tool registration, every "should we let the LLM do X?" question is answered against this model. New contributors land here before touching execution code.

---

## Why this exists

Errander-AI eliminates Linux fleet maintenance toil — patching, log rotation, Docker prune, disk cleanup, backup verification — under human supervision, with rollback and audit. The single hardest design constraint is:

> The system must be safe enough to deploy on real production VMs, while still being meaningfully agentic and useful to operators.

Most AI agent designs collapse this into one layer where an LLM picks tools and runs them. That model is fine for chatbots, dangerous for infrastructure. Errander-AI splits the work into two layers with a strict, audited boundary between them.

---

## The two-layer model

```
+-----------------------------------------------------------------+
|  Layer A: Operator Assistant Layer (LLM-driven)                  |
|  ---------------------------------------------------------------  |
|  Investigates, explains, prioritizes, summarizes.                |
|  Generates text for humans.                                       |
|  Talks to MCP servers, CLIs, Skills, external APIs freely.       |
|  Never executes infrastructure changes.                           |
|  Open ecosystem: use whatever helps.                              |
+--------------------------------+--------------------------------+
                                 |
                       recommendations
                                 |
                                 v
                       +---------------------+
                       |  Human approval     |
                       +----------+----------+
                                  |
                                  v
+-----------------------------------------------------------------+
|  Layer B: Safe Execution Layer (deterministic Python)             |
|  ---------------------------------------------------------------  |
|  Runs whitelisted actions on target VMs.                          |
|  HITL approved, audit logged, rollback-capable.                   |
|  No LLM in the path. No MCP. No arbitrary tool calls.             |
|  This is the safety story. Do not compromise it.                  |
+-----------------------------------------------------------------+
```

The boundary is the entire safety story. Cross it at your peril.

---

## Layer A — Operator Assistant Layer

### What Layer A does

Layer A is where Errander-AI is allowed to be a full AI agent. It uses an LLM as the decision-maker and can call any number of external tools (MCP servers, CLIs, Skills, internal APIs) to gather context, investigate, correlate, and explain.

Layer A produces **text and recommendations for humans**. It never produces a command that runs on a target VM directly. The most action-flavored thing it can produce is a structured proposal that says "I recommend approving this maintenance batch with these parameters" — which still goes through Layer B's HITL approval gate before anything happens.

### What Layer A is allowed to use

| Tool category | Examples | Use case |
|---|---|---|
| MCP servers | Prometheus MCP, Grafana MCP, ELK MCP, Slack MCP, GitHub MCP, AWS/Azure/GCP MCP | Query observability, search logs, fetch deploy history, look up cloud inventory |
| CLIs over SSH | `kubectl get` (read-only), `aws ec2 describe-*`, `gh pr list` | Read state from systems that expose CLI |
| Internal APIs | Errander's own audit DB, drift baseline store, disk history store | Combine real-time observation with action history |
| LLM tools / Skills | CVE lookup, runbook fetch, documentation search | Enrich operator-facing reports |
| Composed workflows | Multi-step investigation: "summarize what changed on this VM in the last 24h" | Operator-driven exploration |

### What Layer A produces

- **Daily digest**: "3 VMs need attention today, with evidence and recommended action."
- **Maintenance plan recommendations**: "Based on disk forecast, CVE exposure, and last action history, recommend disk cleanup + patching batch for these 12 VMs at next window."
- **Post-action explanations**: "The patch batch succeeded; nginx restarted clean; service SLI returned to baseline at 14:32."
- **Operator chat answers**: "Why is prod-web-02 trending slow? Here's what I found across Prometheus, audit DB, and recent commits."
- **Pre-maintenance readiness reports**: "Of the 12 VMs scheduled for tonight's window, 9 are ready, 2 have sudo preflight failures, 1 has a stale backup."

### What Layer A must never do

- Generate shell commands that get executed on a target VM
- Approve its own recommendations
- Bypass any Layer B safety gate
- Call MCP/CLI/Skill tools that mutate target VM state
- Substitute LLM judgment for any deterministic safety rule (whitelist, kernel exclusion, risk tier, maintenance window, sudo preflight, rollback verification)

If a question arises about whether Layer A may do something — if the operation changes target VM state or could blast-radius, the answer is no, regardless of how clever the LLM is.

---

## Layer B — Safe Execution Layer

### What Layer B does

Layer B is the deterministic Python core that actually changes infrastructure. It is invoked only after a human approves a specific maintenance batch through the HITL approval flow. It executes whitelisted actions, captures evidence at every step, verifies postconditions, and rolls back on failure.

Layer B's safety story is the entire product. The list of "things that won't blow up" is encoded here, in code that has been reviewed, tested, and (where applicable) drilled in pre-production.

### What Layer B includes

- **Action sub-graphs** (patching, log_rotation, docker_prune, docker_hygiene, disk_cleanup, backup_verify, service_restart). `docker_hygiene` (v1.1, in progress) replaces `docker_prune` with object-level approval — see CLAUDE.md → Exact-Object Approval invariant.
- **HITL approval gate** (Slack + Web UI dual-channel, fail-closed when no approval manager)
- **Risk tier policy gates** (LOW / MEDIUM / HIGH / CRITICAL with per-environment policy)
- **Sudo preflight** (`sudo -n` capability check per binary before any privileged action)
- **Package lock probe** (fail-closed in live mode if dpkg/dnf lock state cannot be determined)
- **Drift baseline detection** (block live action if baseline mismatch and `drift_abort_on_detection=True`)
- **Maintenance window enforcement** (`--force` override requires mandatory reason, audited)
- **VM-level locking** (file-based v1, Redis v2 — prevents concurrent batches)
- **Audit logging** (every action, every command, every decision, every approval, every rollback)
- **Rollback with verification** (snapshot → execute → verify; if verify fails, rollback → verify rollback)
- **Service health regression detection** (pre/post snapshots of critical services)
- **Hardcoded fallbacks** (when LLM is unavailable, use deterministic priority ordering; agent never blocks on LLM)

### What Layer B never includes

- An LLM in the execution path
- MCP tool calls during action execution
- AI-generated shell commands
- Arbitrary CLI invocations chosen by the LLM
- "AI self-approval" of any kind
- Skills or Tools that mutate target VM state

The reason is not that the LLM is bad. The reason is that the safety guarantees (whitelist, kernel exclusion, rollback, audit, risk gates, fail-closed defaults) all require deterministic execution. One LLM-driven MCP call into the privileged path bypasses every one of those guarantees in a single step.

---

## The handoff

Layer A's output is *always* a recommendation. Layer B's input is *always* an approved batch with deterministic parameters.

```
1.  Layer A collects evidence (metrics, logs, audit, signals)
2.  Layer A produces a maintenance recommendation
        |
        v
3.  Recommendation surfaces to operator (Slack digest, Web UI)
        |
        v
4.  Operator reviews and approves a specific batch
        |
        v
5.  Approved batch enters Layer B with deterministic action plan
        |
        v
6.  Layer B runs sudo preflight, validates, executes, verifies, audits
        |
        v
7.  Result returns to Layer A, which writes a human-friendly summary
```

Three distinct steps, three distinct trust levels:

| Step | Authority | Trust model |
|---|---|---|
| Recommend | Layer A (LLM + tools) | Low trust — output is text for humans |
| Approve | Human operator | High trust — accountable decision |
| Execute | Layer B (deterministic) | Bounded trust — guarded by whitelist, policy, rollback, audit |

This is the entire safety architecture in one sentence: **the LLM never has the keys to the front door; humans do; deterministic Python drives the car.**

---

## Examples

### Example 1: Disk usage alert → maintenance batch

**Layer A:**
- Queries Prometheus MCP for disk usage trends across the fleet.
- Joins against Errander's audit DB to see last disk cleanup per VM.
- Calls the disk-trend signal engine to compute slope and forecast.
- Generates a Slack digest: "prod-web-02 disk at 84%, forecast 90% in 3h. Last cleanup 14 days ago reclaimed 18GB. Top reclaimable: journal 12GB, apt cache 4GB. **Recommended: approve disk cleanup batch for prod-web-02.**"

**Human:** clicks Approve.

**Layer B:**
- Validates approved batch against current state.
- Runs sudo preflight for journalctl, apt-get.
- Acquires VM lock.
- Executes whitelisted cleanup (journalctl --vacuum-time, apt-get clean, etc.) via `sudo -n /usr/bin/<bin>`.
- Captures before/after disk usage; logs audit events for every command.
- Releases lock, reports result back.

**Layer A:**
- Writes a human-friendly summary: "Disk cleanup on prod-web-02 succeeded; reclaimed 14.2 GB; usage now 62%. Audit trail at /ui/batches/abc123."

### Example 2: Operator chat investigation

**Operator** (in Slack): "@errander why is prod-api-01 slow?"

**Layer A:**
- Uses Prometheus MCP to fetch CPU, memory, network, request-rate metrics.
- Uses ELK MCP to search recent error logs.
- Queries internal audit DB for recent actions on this VM.
- Uses GitHub MCP to check recent commits to services running here.
- Composes an answer with evidence, correlations, and a recommendation: "Service nginx restarted 4 times in last 30 minutes. OOMKill events visible. Most recent deploy was 14:18 (PR #4521 added a new in-memory cache). Recommended: rollback that deploy, or restart with increased memory limit."

**Operator** decides what to do. If they want Errander to act, they trigger a maintenance batch through the approved channel.

**Layer A** never executed anything. Everything it did was investigation.

### Example 3: What Layer A CANNOT do

Operator: "@errander prod-web-02 is full, just clean it up."

**Layer A** response: "I can recommend a disk cleanup batch. Here's the plan: [...]. Click here to approve, or paste the approval command into the channel."

Layer A does **not** SSH into prod-web-02 and run cleanup. Even if the operator says "just do it." Even if the LLM is "sure" it's safe. The path through Layer B with HITL approval is the only path. No exceptions.

---

## What this means for new contributors

When you write code or propose a feature, ask:

**"Is this Layer A or Layer B?"**

If Layer A:
- Use any LLM, MCP, CLI, Skill, or external tool you want.
- Produce text, recommendations, structured proposals.
- Never produce a command that runs on a target VM.
- Never bypass a Layer B safety gate.

If Layer B:
- Deterministic Python only.
- No LLM in the execution path.
- No MCP tool calls during action execution.
- Every command must be auditable, idempotent, and rollback-safe (where the action type requires rollback).
- Follow the existing patterns (sub-graphs, sudo preflight, whitelist, etc.).

If you're unsure, default to Layer A — keep the dangerous path narrow.

---

## FAQ

### Why not let the LLM call Prometheus directly via MCP from inside an action sub-graph?

Because the LLM picks the query, interprets the result, and decides what to do with it. For exploratory queries (Layer A), that's fine. For action sub-graphs (Layer B), it breaks every safety guarantee: the audit trail becomes "the LLM decided to do X based on what it thought it saw"; the postcondition check becomes ambiguous; the rollback decision becomes LLM-mediated. Deterministic Python with direct HTTP to Prometheus is faster, cheaper, reproducible, and auditable.

### Why is the LLM allowed in the daily digest at all?

The daily digest is Layer A output: text for a human to read. The LLM doesn't compute slopes, query metrics, or decide severity — those are deterministic Python in the signal engine. The LLM only turns a list of structured `ProactiveSignal` objects into a Slack-friendly summary. If the LLM is unavailable, the digest falls back to a template rendering.

### Could a Layer B sub-graph call an MCP server for something read-only, like a CVE lookup?

No. Layer B sub-graphs do not call MCP. If a CVE lookup is useful for prioritization, it belongs in Layer A — the recommendation phase. By the time Layer B runs, the question of "which CVEs to patch" has already been decided and approved.

### What if MCP becomes the universal standard and we're missing out?

We're not missing out — we use MCP fully in Layer A. The operator chat, the daily digest, the investigation flows, the recommendation engine — all of those are MCP-eligible. The only thing we don't do is let MCP cross into the execution path, because that's where the safety story lives.

### Why is autonomous execution allowed at all if we don't trust the LLM in Layer B?

It isn't. The current product is HITL-only: `require_live_approval=True` and `autonomous_live_apply_enabled=False` are hardcoded until P0-1 (immutable approved plan artifact) and P0-2 (exact deferred replay) are implemented. Even after those are done, autonomous execution will be gated by environment policy, and the deterministic Layer B will still be the only thing that runs.

### Where do I look for the deeper rationale?

- `docs/SPEC.md` — full project specification, including the safety architecture details.
- `CLAUDE.md` — contributor invariants and domain rules.
- `ai_sre_audit_v2.md` — strict SRE audit history, including the validation of this two-layer model.

---

## Anchor phrases for the codebase

These two lines should appear verbatim in any document or contribution that touches the AI layer:

> MCP belongs in the operator brain, not in the execution hands.

> Layer A may investigate and recommend. Layer B alone may execute, and only through deterministic, approved, audited workflows.

If a PR violates either, it doesn't merge.
