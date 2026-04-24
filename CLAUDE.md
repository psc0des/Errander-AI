# Errander-AI — LangGraph Autonomous Maintenance Agent

Autonomous maintenance agent that eliminates repetitive operational toil across heterogeneous VM infrastructure. Performs secure patching (non-kernel), log rotation, Docker pruning, disk cleanup, and more — with safety gates, rollback, and full audit logging.

## Stack (100% Open Source, Cloud-Agnostic)
- Language: Python 3.12+
- Agent Framework: LangGraph (state machines for decision workflows)
- LLM: Qwen3-8B-AWQ on vLLM (self-hosted, private VPN, Tesla T4 16GB VRAM)
- LLM Client: OpenAI Python SDK pointed at configurable base URL
- LLM Modes: thinking mode for planning + failure analysis, `/no_think` for report generation
- LLM Responses: structured JSON via Pydantic models
- Infrastructure: Targets heterogeneous VMs (Linux — Ubuntu/RHEL/Debian)
- SSH: asyncssh (async-native, key-based auth only)
- Notifications & Approval: Slack API (outbound HTTPS only, polling-based approval via reactions)
- Scheduling: APScheduler (built-in, agent owns its own schedule)
- Observability: Prometheus + Grafana (metrics + dashboards) + structured JSON logging
- Database: SQLite v1, PostgreSQL v2 (audit trail, scan history, reports)
- Queue/Cache: Valkey v2 (BSD-licensed Redis fork) for VM locking and approval queues
- Testing: pytest + pytest-asyncio
- Linting: ruff
- Type checking: mypy (strict mode)
- Package manager: uv
- No SaaS dependencies except Slack for v1
- No cloud-provider-specific services — everything runs on any Linux VM with Docker

## Key Commands
- `uv run pytest` — run tests
- `uv run python -m errander --run-now --env <env> --dry-run` — dry-run a batch immediately
- `uv run python -m errander --check-llm` — verify vLLM endpoint connectivity and latency
- `uv run python -m errander --audit --batches` — view recent batch history
- `uv run python -m errander --audit --batch-id <id>` — view all events for a batch
- `uv run ruff check .` — lint
- `uv run mypy .` — typecheck

## Code Style
- Strict typing everywhere (mypy strict mode)
- Async-first architecture
- Dataclasses or Pydantic models for all state
- No bare exceptions — always catch specific errors
- All operations must be idempotent

## Network Architecture

```
┌─────────────────────────────────────────────────┐
│                   VPN (private)                  │
│                                                  │
│  ┌──────────────┐     ┌──────────────────────┐  │
│  │ Self-hosted   │     │ Agent VM              │  │
│  │ LLM (vLLM)   │◄────│  - Errander-AI agent    │  │
│  │ (private IP)  │     │  - APScheduler        │  │
│  └──────────────┘     │  - Slack poller        │  │
│                        │  - Audit DB (SQLite)   │  │
│                        │  - Prometheus /metrics  │  │
│                        └──────┬───────┬────────┘  │
│                               │       │           │
│                          SSH  │       │ HTTPS     │
│                               │       │ (outbound │
│                               ▼       │  only)    │
│                     ┌─────────────┐   │           │
│                     │ Target VMs   │   │           │
│                     │ (private)    │   │           │
│                     └─────────────┘   │           │
└───────────────────────────────────────┼───────────┘
                                        │
                                        ▼
                               ┌─────────────────┐
                               │ Slack API        │
                               │ (public internet)│
                               └─────────────────┘
                                        ▲
                                        │
                               ┌─────────────────┐
                               │ Operator         │
                               │ (mobile/laptop)  │
                               └─────────────────┘
```

- Agent VM has NO public IP — fully private
- All Slack communication is outbound HTTPS (polling, not webhooks)
- LLM endpoint is a private IP / internal DNS inside VPN
- SSH to target VMs is within the VPN
- No nginx, no inbound webhooks, no TLS certificates to manage

## Architecture (target)

```
errander/
├── agent/                  # LangGraph agent definitions
│   ├── graph.py            # Parent orchestrator graph (fan-out to VMs)
│   ├── vm_graph.py         # Per-VM maintenance graph (dispatches to action sub-graphs)
│   ├── subgraphs/          # Sub-graphs per action type
│   │   ├── patching.py     # OS patching (non-kernel)
│   │   ├── log_rotation.py # Log rotation
│   │   ├── docker_prune.py # Docker cleanup
│   │   ├── disk_cleanup.py # Disk space management
│   │   └── backup_verify.py# Backup verification
│   ├── state.py            # Agent state definitions (batch, per-VM, per-action)
│   └── decisions.py        # LLM-powered decision logic (with hardcoded fallback)
├── safety/                 # Safety architecture
│   ├── validators.py       # Pre-execution validation checks
│   ├── rollback.py         # Rollback capabilities per action type
│   ├── approval.py         # Slack polling approval gate
│   ├── locking.py          # VM-level locking (file-based v1, Redis v2)
│   └── audit.py            # Audit logging for all actions
├── execution/              # Actual command execution layer
│   ├── ssh.py              # asyncssh connection management + pooling
│   ├── commands.py         # Strategy pattern: PackageManager interface
│   ├── os_detection.py     # Runtime OS detection + config verification
│   └── sandbox.py          # Dry-run / sandbox execution mode
├── integrations/           # External service integrations
│   ├── slack.py            # Slack API client (outbound only, reaction polling)
│   ├── llm.py              # LLM client (OpenAI SDK → vLLM, with fallback)
│   └── secrets.py          # Secrets interface (env vars v1, Vault v2)
├── observability/          # Metrics and monitoring
│   ├── metrics.py          # Prometheus metrics + /metrics endpoint
│   ├── tracking.py         # Action success/failure tracking
│   └── reporting.py        # Report generation (LLM-powered + template fallback)
├── config/                 # Configuration
│   ├── inventory.py        # VM inventory loader + validator
│   ├── policies.py         # Named maintenance policies (relaxed/moderate/strict)
│   ├── schema.py           # Config YAML schema validation
│   └── settings.py         # Global settings + env var loading
├── models/                 # Data models
│   ├── actions.py          # Action types and results
│   ├── vm.py               # VM / target models
│   ├── plans.py            # Maintenance plan (dry-run output, saved for approval)
│   └── events.py           # Event/audit event models
├── scheduling/             # Scheduling
│   ├── scheduler.py        # APScheduler setup
│   └── windows.py          # Maintenance window enforcement
└── main.py                 # Entry point (long-lived process)
tests/                      # Mirrors src structure
tasks/
├── todo.md                 # Current task tracking
└── lessons.md              # Self-improvement log
docs/
├── SETUP.md                # End-to-end setup guide (new users start here)
├── SPEC.md                 # Full project specification
├── langgraph-primer.md     # LangGraph reference
├── architecture-options.md # Architecture decision record
├── safety-architecture.md  # Safety design decisions
├── command-log.md          # Every command run during development
└── learning/               # Per-feature learning docs (01-XX-feature-name.md)
example/
├── inventory.yaml          # Annotated reference inventory (prod/staging/dev)
└── settings.yaml           # Annotated reference settings (schedule, LLM, Slack)
deploy/
└── vllm/
    ├── docker-compose.yml  # Production vLLM container (GPU passthrough, Qwen3-8B-AWQ)
    └── .env.example        # Configurable deployment vars (model, GPU, port, cache dir)
```

## Risk Tiers (safety gates)

| Tier | Actions | Approval |
|---|---|---|
| Low | Disk cleanup, log rotation, Docker prune | Automatic |
| Medium | Non-kernel patching, config changes | Log + notify |
| High | Service restarts, backup verification | Human approval required |
| Critical | Kernel operations, data deletion | Blocked — never automated |

## Rollback Tiers

| Tier | Actions | Strategy |
|---|---|---|
| Full Rollback | Non-kernel patching | Snapshot full package list before execution. Batch rollback to previous versions on failure. Critical alert if rollback itself fails. |
| Re-Pull (no true rollback) | Docker prune | Pruned images/containers are gone. Can re-pull images if needed. Accept that prune is destructive but low risk. |
| No Rollback Needed | Log rotation, disk cleanup | Log rotation: data still exists, just compressed/rotated. Disk cleanup: only targets known-safe paths (see whitelist below). |
| Never Touch | Kernel, active data dirs | Running kernel, active data directories, anything not on the explicit safe-to-clean whitelist — never automated. |

### Disk Cleanup Whitelist (only these paths are safe to clean)
- `/tmp` (files older than configurable threshold)
- apt/yum package cache
- Old journal logs (`journalctl --vacuum-time`)
- Orphaned package dependencies

Anything not on this whitelist requires human approval to clean.

## Domain Rules
- NEVER automate kernel patching — this is explicitly out of scope
- NEVER touch files/directories outside the disk cleanup whitelist without human approval
- Rollback strategy must be defined per action type (see Rollback Tiers above) — not all actions require full rollback
- ALL actions must be logged to the audit trail before AND after execution
- Dry-run mode must be the default; live execution requires explicit flag
- SSH connections must use key-based auth, never passwords
- Target VMs must be validated (reachable, correct OS) before any action
- Actions must be idempotent — running twice produces the same result
- Agent must NEVER be blocked by LLM unavailability — hardcoded fallbacks for all LLM-powered functions
- All Slack communication is outbound HTTPS only — NO inbound webhooks, NO public endpoints
- Approval via Slack reaction polling, NOT webhooks
- Maintenance windows are agent-enforced — agent refuses to run outside defined windows (--force override with mandatory reason)

## Infrastructure Constraints (v1)

### LLM
- Model: Qwen3-8B-AWQ (Apache 2.0, official HuggingFace weights)
- Server: vLLM on dedicated VM with Tesla T4 16GB VRAM, 4 vCPUs, 16GB RAM
- vLLM serve: `vllm serve Qwen/Qwen3-8B-AWQ --enable-reasoning --reasoning-parser deepseek_r1 --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 8192 --gpu-memory-utilization 0.85`
- Exposes OpenAI-compatible API (`/v1/chat/completions`) on private IP
- Agent uses OpenAI Python SDK pointed at configurable `ERRANDER_LLM_BASE_URL`
- Thinking modes: thinking (planning + failure analysis), `/no_think` (report generation)
- All LLM responses: structured JSON via Pydantic models
- Timeout: 60 seconds (T4 is slower than cloud APIs)
- Sequential LLM calls preferred (low VRAM concurrency)
- Fallback: when LLM is unreachable, agent uses hardcoded default priority ordering and template-based reports
- Upgrade path: Qwen3.5-9B-AWQ when official weights + stable vLLM support available

### Slack (outbound only)
- Agent communicates with Slack entirely via outbound HTTPS to Slack API
- Approval: post report to `#errander-approvals`, poll for ✅/❌ reactions every 30s
- Timeout: 30 minutes (configurable), auto-REJECT on timeout
- Zero inbound traffic to agent VM

### Secrets (environment variables for v1)
```
ERRANDER_SLACK_BOT_TOKEN      # posting messages + polling reactions
ERRANDER_SLACK_CHANNEL_ID     # dedicated approvals channel
ERRANDER_LLM_BASE_URL         # private vLLM endpoint
ERRANDER_LLM_API_KEY          # if vLLM requires auth
ERRANDER_AUDIT_DB_URL         # SQLite path for v1
```
SSH keys: referenced by file path in inventory config, never inlined.
`.gitignore` must include: `.env`, `*.pem`, `*.key`, `*.sqlite`

### V2 Upgrade Path
- PostgreSQL for audit trail (replace SQLite) — design data models for PostgreSQL from v1
- Valkey (BSD-licensed Redis fork) for VM locking and approval queues (replace file-based)
- HashiCorp Vault for secrets (replace env vars)
- Slack webhooks via nginx reverse proxy (replace polling if latency matters)
- Dashboard: React/Next.js, hosted anywhere, reads from PostgreSQL via thin API
- Qwen3.5-9B-AWQ replaces Qwen3-8B-AWQ when available

---

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update tasks/lessons.md with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to tasks/todo.md with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to tasks/todo.md
6. **Capture Lessons**: Update tasks/lessons.md after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Only touch what's necessary. No side effects with new bugs.

## Command Tracking
- Log every shell command, CLI command, and tool invocation used 
  during development to docs/command-log.md
- Format: timestamp, command, what it does, and why it was used
- Group by category: project setup, dependencies, git, testing, 
  deployment, vLLM, Slack, SSH, debugging
- Include both successful and failed commands (mark failures with 
  the error and what fixed it)
- This is a developer reference log, not an audit trail — keep 
  it practical and searchable

## Learning Documentation
- For every feature implemented, create a new file in docs/learning/
- Filename format: XX-feature-name.md (e.g., 01-project-scaffold.md, 
  02-disk-cleanup-subgraph.md)
- Each file must explain:
  - What was built and why
  - Key concepts used (LangGraph patterns, Python patterns, etc.)
  - Code walkthrough of the important parts — not just what, but 
    HOW and WHY it works that way
  - Gotchas and mistakes encountered during implementation
  - Links to relevant docs or references
  - Questions to test understanding (quiz yourself section)
- Write for a developer learning these concepts for the first time
- Use code snippets from the actual project, not generic examples
- If a concept is complex, break it down with diagrams (Mermaid)

## Status Tracking
- Maintain STATUS.md in the project root at all times
- Update STATUS.md at the end of every session or after completing 
  a feature
- Structure:
  - Last updated: timestamp
  - Current phase: what we're working on now
  - Completed: what's done and working
  - In progress: what's partially done
  - Next up: what comes after current work
  - Decisions made: key architectural/design choices this session
  - Blockers: anything stuck or needing input
  - Files changed: list of files added or modified this session

