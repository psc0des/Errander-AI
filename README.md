# Errander-AI

**Deterministic maintenance automation with an AI-assisted operator layer for Linux fleets.**

Errander-AI is a supervised maintenance agent for small-to-medium Linux fleets. It performs non-kernel patching, log rotation, Docker pruning, disk cleanup, and backup verification — with safety gates, rollback, idempotency, and full audit logging. Every live change requires human approval (Slack or Web UI). It runs on a single controller VM and manages any number of target servers over SSH.

The AI layer prioritizes, explains, correlates, and summarizes maintenance work for operators. The execution layer is deterministic Python — the LLM is never in the path that actually changes infrastructure.

100% open source. Cloud-agnostic. No SaaS dependencies except Slack (optional).

**Supported target OS:** Ubuntu 20.04+, Debian 11+, RHEL/Rocky/Alma 8+.

> See [`docs/AI-ARCHITECTURE.md`](docs/AI-ARCHITECTURE.md) for the canonical two-layer AI safety model.
>
> **MCP belongs in the operator brain, not in the execution hands.**

## Non-goals

Errander-AI is intentionally narrow. It is **not**:

- A monitoring system replacement — pair it with Prometheus, ELK, or your existing stack
- An application manager — does not deploy, restart, or manage Tomcat, Nginx, Kubernetes, or app services
- An Ansible / Salt / Puppet replacement — does not run arbitrary playbooks
- A fully autonomous SRE — every live change requires human approval (HITL)
- A kernel patching tool — kernel operations are explicitly excluded
- A configuration management tool — does not enforce desired-state across files

What it **is**: a safety-gated supervised execution layer for recurring Linux fleet maintenance toil that you'd otherwise do with cron, manual SSH, or a fragile playbook.

---

## How It Works

The LLM **never connects to target servers**. It never sees a terminal. It never runs a command.

```
Master VM (Agent + LLM)              Target VMs
+-------------------------+          +-----------+
|  Errander-AI Agent      |---SSH--->| Ubuntu-01 |
|  +-------------------+  |          +-----------+
|  | LangGraph         |  |          +-----------+
|  | (decision engine) |  |---SSH--->| RHEL-02   |
|  +---------+---------+  |          +-----------+
|            |             |          +-----------+
|  +---------v---------+  |---SSH--->| Debian-03 |
|  | LLM               |  |          +-----------+
|  | (your choice)     |  |          +-----------+
|  +-------------------+  |---SSH--->| Debian-N  |
+-------------------------+          +-----------+
```

The flow:

1. **Agent** SSHes into a target VM, runs discovery commands (`df -h`, `apt list --upgradable`, `docker system df`)
2. **Agent** collects the output and sends it to the **LLM**: "Given this system state, prioritize these maintenance actions"
3. **LLM** responds with structured JSON: `["disk_cleanup", "patching", "log_rotation"]`
4. **Agent** takes that answer and executes the plan via SSH — through hardcoded safety gates
5. **LLM** generates a human-readable report of what happened

The LLM is a **brain in a jar** — it thinks, but it has no hands. The agent is the hands.

### AI vs Pure Automation

| Aspect | Pure Automation (Ansible) | Errander-AI |
|--------|--------------------------|-------------|
| "Patch all servers" | Runs same playbook on every server | Examines each server's state, decides what to prioritize |
| Server A: 2% disk, Server B: 90% disk | Same playbook on both | Prioritizes disk cleanup on B, skips cleanup on A |
| Patching fails | Stops or retries blindly | LLM analyzes the error, recommends retry vs rollback vs escalate |
| Reporting | Template output | LLM writes a context-aware summary of what happened and why |
| Nothing to do | Runs anyway | Idempotent — detects "already clean" and skips (like Ansible's changed/ok) |

### Graceful Degradation

The agent is **LLM-enhanced, not LLM-dependent**. If the LLM goes down:

- Action prioritization falls back to hardcoded risk-tier ordering
- Failure analysis falls back to heuristic-based recommendations
- Report generation falls back to template-based summaries

The agent never stops working. It degrades from "AI-assisted" to "smart automation."

---

## Architecture

### Three-Level Graph Structure (LangGraph)

```
Level 1: Batch Orchestrator
  init_batch -> validate_window -> validate_targets -> fan_out -> collect -> report -> [approval] -> END
                                                          |
                                                    Send() per VM

Level 2: Per-VM Maintenance Graph (one per target)
  lock -> discover -> plan_actions -> dispatch -> check_more -> audit -> unlock
                                        |
                                  Sub-graph per action

Level 3: Action Sub-Graphs
  validate -> assess -> [snapshot] -> execute -> verify -> END
                  |                                |
            (nothing to do?                  (dry-run?
             skip execute)                    skip verify)
```

- **Multi-VM parallelism**: LangGraph `Send()` fan-out processes the fleet concurrently
- **Action isolation**: A bug in docker prune logic cannot affect the patching flow
- **Independent testability**: Each sub-graph tested without the parent
- **Extensibility**: Adding a new action type = write a sub-graph + register it in the dispatcher

### Network Architecture

```
+--------------------------------------------------+
|                  VPN (private)                    |
|                                                   |
|  +---------------+     +----------------------+  |
|  | LLM endpoint  |     | Agent VM             |  |
|  | (cloud or     |<----| - Errander-AI agent  |  |
|  |  self-hosted) |     | - APScheduler        |  |
|  +---------------+     | - Slack poller        |  |
|                         | - Audit DB (SQLite)  |  |
|                         | - Prometheus /metrics|  |
|                         | - Web UI :9090/ui    |  |
|                         +-----+------+---------+  |
|                               |      |            |
|                          SSH  |      | HTTPS      |
|                               |      | (outbound  |
|                               v      |  only)     |
|                      +------------+  |            |
|                      | Target VMs |  |            |
|                      | (private)  |  |            |
|                      +------------+  |            |
+--------------------------------------+-----------+
                                       |
                                       v
                              +-----------------+
                              | Slack API       |
                              | (public)        |
                              +-----------------+
                                       ^
                                       |
                              +-----------------+
                              | Operator        |
                              | (mobile/laptop) |
                              +-----------------+
```

- Agent VM has **no public IP** — fully private
- All Slack communication is **outbound HTTPS** (polling, not webhooks)
- LLM endpoint is a **private IP** inside VPN
- SSH to target VMs is **within the VPN**
- No nginx, no inbound webhooks, no TLS certificates to manage

---

## Action Types

| Action | Risk Tier | What It Does | Idempotency | Rollback |
|--------|-----------|-------------|-------------|----------|
| **Disk Cleanup** | Low | Clean `/tmp`, package cache, journal logs, orphaned deps | Skips if nothing reclaimable | None needed (safe paths only) |
| **Log Rotation** | Low | Find oversized logs, `logrotate --force` or manual gzip+truncate | Skips if no large files found | None needed (data compressed, not deleted) |
| **Docker Prune** | Low | Remove dangling images, stopped containers | Skips if nothing to prune | Re-pull images if needed |
| **Patching** | Medium | Non-kernel `apt upgrade` / `dnf upgrade` with kernel exclusion | Skips if already up-to-date | Full — version snapshot before, batch rollback on failure |
| **Backup Verify** | High | Read-only check: backups exist, are recent, non-zero size | Inherently idempotent (read-only) | N/A |

### Safety Gates

| Tier | Approval | Example |
|------|----------|---------|
| **Low** | Automatic | Disk cleanup, log rotation, Docker prune |
| **Medium** | Log + notify | Non-kernel patching |
| **High** | Human approval required (Slack + Web UI) | Backup verification |
| **Critical** | **Blocked — never automated** | Kernel operations, data deletion |

Kernel packages (`linux-*`, `linux-image-*`, `kernel-*`) are **always excluded** from patching. This is a hardcoded check, never an LLM decision.

### Disk Cleanup Whitelist

Only these paths are safe to clean — **hardcoded, never LLM-decided**:

- `/tmp` (files older than configurable threshold)
- apt/yum package cache
- Journal logs (`journalctl --vacuum-time`)
- Orphaned package dependencies

Anything not on this whitelist requires human approval.

---

## Approval Flow

Dual-channel approval — Slack reactions and Web UI buttons race:

1. Agent completes dry-run and posts the plan to `#errander-approvals` on Slack
2. **Race**: first channel to decide wins
   - Slack: operator reacts with :white_check_mark: (approve) or :x: (reject)
   - Web UI: operator clicks Approve/Reject at `/ui/approvals`
3. Timeout after 30 minutes (configurable) = auto-reject

All Slack communication is outbound HTTPS. No webhooks, no inbound traffic.

---

## Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.12+ | Async-first, strict typing |
| Agent Framework | LangGraph | State machines for decision workflows |
| LLM | Any OpenAI-compatible endpoint | User-choice at install: cloud API (OpenAI, Anthropic, Groq, Azure AI Foundry, etc.) **or** self-hosted vLLM running Qwen3-8B-AWQ on a 16 GB VRAM GPU (Tesla T4 reference). See `docs/LLM-PROVIDERS.md`. |
| LLM Client | OpenAI Python SDK | Pointed at configurable base URL |
| SSH | asyncssh | Async-native, key-based auth only, connection pooling |
| Notifications | Slack API | Outbound HTTPS only, reaction polling |
| Scheduling | APScheduler | Agent owns its own schedule |
| Audit Trail | SQLite (v1) | PostgreSQL planned for v2 |
| Observability | Prometheus + Web UI | `/metrics`, `/health`, `/ui` on port 9090 |
| VM Locking | File-based (v1) | Valkey (Redis fork) planned for v2 |
| Testing | pytest + pytest-asyncio + Playwright | 1707 tests |
| Linting | ruff | |
| Type Checking | mypy (strict mode) | |
| Package Manager | uv | |

**No cloud-provider-specific services.** Everything runs on any Linux VM with Docker.

---

## Project Structure

```
errander/
  agent/                    # LangGraph agent definitions
    graph.py                # Batch orchestrator (fan-out to VMs)
    vm_graph.py             # Per-VM maintenance graph
    decisions.py            # LLM-powered decisions (with hardcoded fallback)
    subgraphs/
      disk_cleanup.py       # Disk space management
      log_rotation.py       # Log compression and rotation
      docker_prune.py       # Docker resource cleanup
      patching.py           # Non-kernel OS patching
      backup_verify.py      # Backup verification (read-only)
  safety/                   # Safety architecture
    validators.py           # Pre-execution validation (kernel exclusion, whitelist)
    rollback.py             # Rollback strategies per action type
    approval.py             # Dual-channel approval (Slack + Web UI)
    locking.py              # VM-level locking (file-based v1)
    audit.py                # Audit logging (async SQLite)
  execution/                # Command execution layer
    ssh.py                  # asyncssh connection management + pooling
    commands.py             # PackageManager strategy (AptManager, DnfManager)
    os_detection.py         # Runtime OS detection + config verification
    sandbox.py              # Dry-run / sandbox execution mode
  integrations/             # External service integrations
    slack.py                # Slack API client (outbound only)
    llm.py                  # LLM client (OpenAI SDK → any OpenAI-compatible endpoint)
  observability/            # Metrics and monitoring
    metrics.py              # Prometheus metrics + Web UI + /metrics endpoint
  config/                   # Configuration
    settings.py             # Global settings + env var loading
    inventory.py            # VM inventory loader
    policies.py             # Maintenance policies (relaxed/moderate/strict)
    schema.py               # Config YAML schema validation
  scheduling/               # Scheduling
    scheduler.py            # APScheduler setup
    windows.py              # Maintenance window enforcement
  main.py                   # Entry point
tests/                      # Mirrors src structure (1707 tests)
deploy/
  vllm/
    docker-compose.yml      # vLLM container (GPU passthrough)
docs/
  SPEC.md                   # Full project specification
  learning/                 # Per-feature learning docs
example/
  inventory.yaml            # Reference inventory config
  settings.yaml             # Reference settings config
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- SSH key access to target VMs
- (Optional) Slack bot token for notifications
- (Optional) An LLM endpoint for AI-powered decisions — either a cloud API (OpenAI, Anthropic, Groq, etc.) or a self-hosted vLLM on a 16 GB VRAM GPU. The agent runs without one using hardcoded fallbacks.

### Install and Run Tests

```bash
git clone https://github.com/psc0des/Errander-AI.git errander
cd errander
uv sync
uv run pytest
```

> **New machine?** `scripts/bootstrap.sh` (Linux) or `scripts/bootstrap.ps1` (Windows) handles git, uv, and Python 3.12 installation automatically.

### Configure

Run the interactive setup script — it prompts for everything and writes `.env` + `inventory.yaml`:

```bash
bash scripts/configure.sh
```

Or set env vars manually:

```bash
export ERRANDER_LLM_BASE_URL="https://<resource>.openai.azure.com/openai/v1/"
export ERRANDER_LLM_MODEL="<deployment-name>"
export ERRANDER_LLM_API_KEY="<key>"
export ERRANDER_AUDIT_DB_URL="errander.sqlite"

# Optional — Slack notifications
export ERRANDER_SLACK_BOT_TOKEN="xoxb-..."
export ERRANDER_SLACK_CHANNEL_ID="C0..."
```

Create `inventory.yaml` (see `example/inventory.yaml`) and optionally `settings.yaml` (see `example/settings.yaml`).

### Run

```bash
# Dry-run a batch immediately (safe — no changes made)
uv run python -m errander --run-now --env dev --dry-run

# Check LLM connectivity
uv run python -m errander --check-llm

# View audit trail
uv run python -m errander --audit --batches

# Start the long-lived agent (scheduler mode)
uv run python -m errander
```

### Web UI

After starting, visit `http://localhost:9090/ui`:

- `/ui` — Dashboard (status, event count, recent batches)
- `/ui/batches` — Batch history
- `/ui/batches/{id}` — Batch detail with all events
- `/ui/vms/{vm_id}` — VM history across all batches
- `/ui/approvals` — Pending approvals with Approve/Reject buttons
- `/metrics` — Prometheus metrics
- `/health` — Health check

---

## Key Commands

```bash
uv run pytest                                          # Run all 1707 tests
uv run ruff check .                                    # Lint
uv run mypy .                                          # Type check
uv run python -m errander --run-now --env dev --dry-run # Dry-run a batch
uv run python -m errander --probe-now dev              # Daily probe (read-only, no maintenance)
uv run python -m errander --ask "Any disk issues?" --env dev  # LLM fleet analysis (Layer A)
uv run python -m errander --check-targets dev          # Pre-flight VM readiness check
uv run python -m errander --check-llm                  # Verify LLM endpoint
uv run python -m errander --audit --batches            # View recent batches
uv run python -m errander --audit --batch-id <id>      # Events for a batch
```

---

## Configuration

### Inventory (`config/inventory.yaml`)

Defines target VMs grouped by environment:

```yaml
environments:
  production:
    policy: strict
    maintenance_window: "02:00-06:00"
    maintenance_days: ["Saturday", "Sunday"]
    hosts:
      - vm_id: prod/web-01
        hostname: 10.0.1.10
        ssh_user: errander-ai
        ssh_key_path: ~/.ssh/errander_prod
        os_family: ubuntu

  dev:
    policy: relaxed
    hosts:
      - vm_id: dev/test-01
        hostname: 10.0.2.10
        ssh_user: errander-ai
        ssh_key_path: ~/.ssh/errander_dev
        os_family: ubuntu
```

### Policies

| Policy | Auto-Approve | Human Approve | Use Case |
|--------|-------------|---------------|----------|
| **relaxed** | Low, Medium, High | — | Dev environments |
| **moderate** | Low | Medium, High | Staging |
| **strict** | Low | Medium, High | Production |

Critical actions are **always blocked** regardless of policy.

### Maintenance Windows

Agent refuses to run outside defined windows unless `--force` with a mandatory reason:

```bash
# This will be blocked outside the maintenance window
uv run python -m errander --run-now --env production

# Override with reason (logged to audit trail)
uv run python -m errander --run-now --env production --force --force-reason "emergency patching for CVE-2024-XXXX"
```

---

## Observability

### Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `errander_actions_total` | Counter | Actions by type, status, VM |
| `errander_action_duration_seconds` | Histogram | Action execution time |
| `errander_batch_duration_seconds` | Histogram | Batch total time |
| `errander_ssh_errors_total` | Counter | SSH errors by VM and reason |
| `errander_llm_requests_total` | Counter | LLM calls by outcome |
| `errander_approval_wait_seconds` | Histogram | Time waiting for approval |
| `errander_vm_lock_held_seconds` | Histogram | Lock hold duration |

### Audit Trail

Every action is logged to SQLite with batch ID, VM ID, action type, status, timestamp, and metadata. Query via CLI:

```bash
uv run python -m errander --audit --batch-id batch-abc123
uv run python -m errander --audit --vm-id prod/web-01
uv run python -m errander --audit --action-type patching --last 50
```

---

## LLM Deployment

The agent works with any OpenAI-compatible endpoint — pick one at install time:

- **Cloud API** (fastest setup): set `ERRANDER_LLM_BASE_URL`, `ERRANDER_LLM_MODEL`, and `ERRANDER_LLM_API_KEY` in `.env` for OpenAI, Anthropic, Groq, Azure AI Foundry, etc. See `docs/LLM-PROVIDERS.md` for paste-ready configs.
- **Self-hosted vLLM** (private, no data egress): runs on a dedicated GPU VM inside the VPN. Reference hardware is an NVIDIA Tesla T4 with 16 GB VRAM, 4 vCPUs, 16 GB RAM.

For self-hosted vLLM:

```bash
cd deploy/vllm
cp .env.example .env   # Configure MODEL_ID, GPU_MEM_UTIL, etc.
docker compose up -d
```

Default configuration: Qwen3-8B-AWQ on Tesla T4 16GB VRAM with reasoning mode enabled.

Verify connectivity from the agent VM:

```bash
uv run python -m errander --check-llm
```

---

## Design Principles

- **Two-layer AI architecture** — Layer A (Operator Assistant) uses LLM, MCP, CLI, Skills freely for investigation and recommendation; Layer B (Safe Execution) is deterministic Python with no LLM in the path. See [`docs/AI-ARCHITECTURE.md`](docs/AI-ARCHITECTURE.md).
- **Fail loud, fail fast, fail safe** — never silently swallow errors; when in doubt, stop and escalate
- **Idempotent** — every action can be run twice with the same result; assess before execute
- **LLM-enhanced, not LLM-dependent** — hardcoded fallbacks for all LLM-powered functions; agent never blocks on LLM availability
- **Fully private** — no public IPs, no inbound traffic, no exposed endpoints
- **HITL-first** — every live infrastructure change requires human approval; autonomous execution is gated and currently disabled

---

## V2 Roadmap

- PostgreSQL for audit trail (replace SQLite)
- Valkey (BSD-licensed Redis fork) for distributed VM locking
- React/Next.js dashboard
- HashiCorp Vault for secrets (replace env vars)
- Slack webhooks via nginx reverse proxy (replace polling)

---

## License

[Add your license here]
