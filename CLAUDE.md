# Errander-AI — Supervised Agentic AI SRE Platform

A supervised agentic AI SRE platform that eliminates operational toil while keeping humans in control of live infrastructure changes. Performs secure patching (non-kernel), log rotation, Docker hygiene (object-level approval), disk cleanup, backup verification, and operator-triggered service restart — with safety gates, rollback, and full audit logging. Every live change requires human Slack/Web UI approval.

## Stack (100% Open Source, Cloud-Agnostic)
- Language: Python 3.12+
- Agent Framework: LangGraph (state machines for decision workflows)
- LLM: any OpenAI-compatible endpoint — user picks at install time. Two supported paths: (a) **cloud API** (OpenAI, Anthropic, Groq, etc.) for fastest setup; (b) **self-hosted vLLM** running Qwen3-8B-AWQ on a 16 GB VRAM GPU (Tesla T4 reference) for private, no-egress deployments.
- LLM Client: OpenAI Python SDK pointed at configurable base URL (model + temperature configurable; no provider-specific prompt prefixes baked in)
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
- `uv run python -m errander --plan-show <plan-id>` — print full plan snapshot (fallback when Slack message is truncated)
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
│   │   ├── docker_hygiene.py # Rich Docker assessment + object-level removal (v1.1)
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
SETUP.md                    # End-to-end setup guide (new users start here — at repo root)
docs/
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
| Low | Disk cleanup, log rotation, backup verification | Automatic |
| Medium | Docker prune, non-kernel patching, config changes | Log + notify |
| High | Service restart (`service_restart`) — operator-triggered only in v1 | Human Slack approval required (all policy tiers) |
| Critical | Kernel operations, data deletion | Blocked — never automated |

**Service restart is operator-triggered only in v1.** Auto-detection from probe output (detect-and-propose) is deferred to v1.1. Adding a unit to the `restartable_units` allowlist in inventory + on-target `/etc/errander/restart-allowlist` is required before Errander will restart it.

**v1 approval coverage decision (2026-05-23):** Categorical approval is acceptable for LOW-risk, whitelist-bounded, non-destructive actions (`/tmp`, `apt-cache`, `journal`, `log_rotation`). These actions cannot permanently destroy data (files are either temp-only or compressed/rotated, not deleted), and their scope is hardcoded — never LLM-decided. They are honestly labeled `[CATEGORICAL]` in the Slack approval message. `orphaned-deps` is the exception: it removes installed packages (destructive), so it receives exact-object treatment — exact package names appear in the approval message and a drift gate re-checks candidates at execution time.

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

## AI Safety Invariant (MANDATORY)

Errander-AI uses a two-layer AI architecture. See `docs/AI-ARCHITECTURE.md` for the canonical model, and `docs/OBSERVABILITY.md` for how each layer is observed (audit trail = Layer B actions; AI decision log + LangSmith = Layer A reasoning; Prometheus = execution health).

> **MCP belongs in the operator brain, not in the execution hands.**

> **Layer A may investigate and recommend. Layer B alone may execute, and only through deterministic, approved, audited workflows.**

**Layer A — Operator Assistant** (LLM-driven): may use LLM, MCP, CLI, Skills, Prometheus, ELK, CVE feeds, GitHub, Slack context, runbooks. Produces text, recommendations, structured proposals. **Never executes infrastructure changes.**

**Layer B — Safe Execution** (deterministic Python): plans, validates, requests approval, executes, audits, verifies, rolls back. **No LLM in the live execution path. No MCP/CLI/Skill tool calls. No AI-generated shell commands. No AI self-approval.**

When proposing any AI-related feature or contribution, classify it as Layer A or Layer B first. If unsure, default to Layer A. Layer B changes require explicit safety review.

### Exact-Object Approval (MANDATORY for destructive actions)

For any action that removes, deletes, or otherwise destroys state (images, volumes, containers, files, package versions, etc.), the approval artifact must reference **exact objects**, not action categories.

> The agent presents exact objects → the operator approves exact objects → execution removes only those exact approved objects → the wrapper re-validates each object at execution time → the audit log records every individual object removed.

- **"Approved Docker cleanup"** is not a valid approval. **"Approved removal of image IDs `sha256:abc...`, `sha256:def...` and stopped container `worker-3` (exit 0)"** is.
- Bulk approvals ("approve everything dangling") are acceptable *only* when the plan enumerates the exact objects at approval time and the wrapper re-validates each one is still in the same state at execution time. State drift between approval and execution must abort the action for the drifted object, not silently proceed.
- Per-object audit entries are required. One audit row per removed object, not one row per batch.
- HITL is necessary but not sufficient — a human can rubber-stamp a vague plan. The protection comes from the *evidence quality* of the approval artifact, not the approval gesture itself.

This rule applies to all Layer B actions. No grandfathering — the previous bulk `docker_prune` action was removed in v1.1 Session 3 precisely because it violates this invariant. Any future action that cannot satisfy exact-object approval is out of scope.

### Implementation Contracts (for object-level destructive actions)

The Exact-Object Approval invariant is **enforced in code** by two contracts. Every new destructive action must satisfy both. The reference implementation is `errander/agent/subgraphs/docker_hygiene.py` — **mirror it, don't reinvent it.**

Grep `# INVARIANT:` across the codebase to find every site where these contracts are load-bearing. Each marker links back to this section.

**Contract A — Layered drift gates.** A single drift check has a race window between approval and execution. Every object-level destructive action MUST implement two gates:

1. **Snapshot-level (Python, in the execute node):** compare the current assessment's hash against the approval's `snapshot_hash`. Refuse execution outright on mismatch. The hash function MUST omit volatile fields (size, timestamps) that fluctuate between probes without indicating meaningful drift.
2. **Per-object (wrapper, at execution time):** for each approved object, re-query its current state on the target VM and verify the classification still holds. Skip drifted objects with a named reason (e.g., `image_re_tagged`, `container_restarted`, `now_referenced`) — never silently proceed.

Reference: `compute_assessment_hash()` + `execute_node()` in `docker_hygiene.py`; per-class re-validation in `errander-docker-remove-v2` wrapper.

**Contract B — Per-object output parsers must never silently drop.** Wrappers return one result line per approved object. The Python parser MUST:

1. **Synthesize FAILED for missing results.** If the wrapper omits a result for an approved object (crash mid-loop, network glitch), the parser MUST emit a `FAILED` result with `error="no_result_from_wrapper"` rather than dropping the object from the returned tuple. The audit log must record N outcomes for N approved objects.
2. **Drop results for un-approved objects.** If the wrapper hallucinates a result for an object the operator didn't approve, the parser MUST log loudly and drop the result. Trusting wrapper output for un-approved objects creates a path for the wrapper to remove things the operator never saw.

Reference: `parse_remove_v2_output()` in `docker_hygiene.py` — the `seen` set and the `for key, finding in by_key.items()` tail loop.

**Tests that lock these contracts in:**
- `TestExecuteNode::test_snapshot_hash_mismatch_refuses_execution`
- `TestComputeAssessmentHash::test_unaffected_by_volatile_size`
- `TestParseRemoveV2Output::test_missing_result_becomes_failed`
- `TestParseRemoveV2Output::test_extra_result_for_unapproved_object_is_dropped`

When extracting a shared base class for the second object-level action, these tests should migrate to the base-class test suite — they're invariants, not docker_hygiene-specific.

## Domain Rules

### v1 Scope
v1 supports Linux host maintenance only: OS patching (non-kernel), disk cleanup,
log rotation, Docker hygiene (replacing Docker prune in v1.1), backup verification,
and operator-triggered service restart (6 actions). Kubernetes, app runtimes
(Tomcat/Nginx/Java GC), database management, network/firewall changes, and
arbitrary user-supplied commands are explicitly out of scope. Adding new actions
requires a new sub-graph + manifest + risk-tier classification + rollback strategy
— not a config flag.

> **v1.1 transition complete (2026-05-22, Session 3 shipped):** `docker_prune`
> is fully removed. `docker_hygiene` is the sole Docker action in BUILTIN_ACTIONS
> (6 actions total). `ActionType.DOCKER_PRUNE` is retained in the enum for
> audit-log read-back only, marked by `LEGACY_ACTION_TYPES` in `models/actions.py`.
> Config loader raises `ConfigError` on `docker_prune:` inventory keys; run
> `--migrate-inventory` to rename them to `docker_hygiene`.

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
- The agent supports any OpenAI-compatible endpoint. The user picks one at install time via `ERRANDER_LLM_BASE_URL` + `ERRANDER_LLM_MODEL` (+ `ERRANDER_LLM_API_KEY` if the provider needs it). See `docs/LLM-PROVIDERS.md`.
- Agent uses the OpenAI Python SDK; no provider-specific prompt prefixes are baked in. Model and temperature are configurable.
- All LLM responses: structured JSON via Pydantic models.
- Fallback: when the LLM is unreachable, the agent uses hardcoded default priority ordering and template-based reports — it must never block on LLM availability.

**Path A — Cloud API (recommended for fastest setup):**
- Any OpenAI-compatible cloud (OpenAI, Anthropic via OpenAI-compat endpoint, Groq, Together, etc.).
- Timeout: typically < 10 seconds.

**Path B — Self-hosted vLLM (recommended for private, no-egress deployments):**
- Reference model: Qwen3-8B-AWQ (Apache 2.0, official HuggingFace weights).
- Reference hardware: dedicated VM with NVIDIA Tesla T4 **16 GB VRAM**, 4 vCPUs, 16 GB RAM.
- vLLM serve: `vllm serve Qwen/Qwen3-8B-AWQ --reasoning-parser deepseek_r1 --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 8192 --gpu-memory-utilization 0.85`
- Exposes OpenAI-compatible API (`/v1/chat/completions`) on private IP.
- Timeout: 60 seconds (T4 is slower than cloud APIs).
- Sequential LLM calls preferred (low VRAM concurrency).
- Upgrade path: Qwen3.5-9B-AWQ when official weights + stable vLLM support are available.

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
ERRANDER_SIGNING_SECRET       # HMAC secret for signed web-approval URLs
                              # 32+ random bytes: head -c 32 /dev/urandom | base64
ERRANDER_WEB_BASE_URL         # externally-reachable base URL for agent VM web UI
                              # e.g. http://10.0.0.5:9090 — used to build signed
                              # web-approval URLs in Slack messages (docker_hygiene)
                              # empty = web approval URLs omitted from Slack messages
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

## Doc Sync Rule (mandatory)

**Everything goes in one commit.** Code changes + doc updates = single atomic commit, then push. Never push first and sync docs afterward.

### Pre-flight check before destructive-action work (mandatory)

Before implementing, modifying, or extending any **destructive action** (removes/deletes/destroys state on a target VM — patching, docker_hygiene, disk_cleanup, service_restart, future selective actions):

1. **Read** the "Implementation Contracts" subsection above (under AI Safety Invariant).
2. **Grep** the codebase for `INVARIANT` (the marker prefix is `# INVARIANT:`) and read every match. These are load-bearing breadcrumbs at the sites where the contracts are enforced. Touching them without understanding the contract risks breaking the Exact-Object Approval invariant.
   ```bash
   # POSIX shell:
   grep -rn "INVARIANT" errander/ scripts/
   # PowerShell:
   Select-String -Path errander\,scripts\ -Pattern "INVARIANT" -Recurse
   ```
3. **Mirror** the reference implementation (`docker_hygiene` for object-level approval flows) — do not reinvent the parser, the drift-gate pattern, or the approval artifact shape.

Skipping this pre-flight is a process violation, not a stylistic preference. The invariants exist because earlier reviews caught specific safety gaps — the markers are there to keep them caught.

Before every `git commit` + `git push`, update all files that are relevant to the changes made:

### Always update (every session)
- `STATUS.md` — Last Updated, In Progress, Files Changed
- `docs/command-log.md` — every shell command run this session
- `tasks/todo.md` — mark completed items, add new items
- `tasks/lessons.md` — any lessons from corrections or surprises this session

### Update when relevant (when the specific thing changes)
- `SETUP.md` — when setup steps or scripts change
- `docs/langgraph-primer.md` — when graph nodes, edges, state schema, fan-out structure, approval flow, or sub-graph layout changes; this file went stale before — keep it in sync
- `README.md` — when major features, architecture, or test counts change
- `RUN.md` — when CLI commands or run process changes
- `CLAUDE.md` — when project rules or architecture decisions change
- `docs/learning/XX-feature.md` — create one for every new feature implemented
- `docs/LLM-PROVIDERS.md` — when LLM provider support changes
- `docs/SECRETS.md` — when secrets management changes
- `docs/OBSERVABILITY.md` — when an observability surface changes (audit events, metrics, AI decision log, LangSmith)

---

## Commit Message Format

- **One line only** — no multi-line body, no bullet points, no blank lines
- Format: `type: short description (under 72 chars)`
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
- Examples:
  - `docs: fix Python 3.12 install on Ubuntu 22.04 — use uv python install`
  - `feat: add deferred execution store with 7-day expiry`
  - `fix: release VM lock on SSH timeout`

---

## Git Identity

- GitHub username: **psc0des**
- Git author name: `psc0des`
- Git author email: `sarathy.vass6@gmail.com`
- Before the first commit in any session, verify git config is correct:
  ```bash
  git config user.name   # must be psc0des
  git config user.email  # must be sarathy.vass6@gmail.com
  ```
- If either is wrong or blank, set them before committing:
  ```bash
  git config --global user.name "psc0des"
  git config --global user.email "sarathy.vass6@gmail.com"
  ```
- NEVER commit with a work/corporate email — if caught after push, the repo must be deleted and recreated (force-push rewrites history but GitHub retains old objects until GC)

---

## Repo Portability

The repo is fully self-contained — everything needed to run the project is in GitHub. To set up on any new machine:

```bash
git clone https://github.com/psc0des/Errander-AI.git
cd Errander-AI
uv sync --extra dev        # rebuilds virtualenv
cp example/inventory.yaml inventory.yaml   # edit with real VM IPs
```

Then recreate `.env` (never committed — intentionally excluded):
```
ERRANDER_LLM_BASE_URL=...
ERRANDER_LLM_MODEL=...
ERRANDER_SLACK_BOT_TOKEN=...
ERRANDER_SLACK_CHANNEL_ID=...
ERRANDER_AUDIT_DB_URL=errander.sqlite
ERRANDER_SIGNING_SECRET=...   # required for docker_hygiene web approval (v1.1)
ERRANDER_WEB_BASE_URL=...     # e.g. http://10.0.0.5:9090 — optional, enables signed URL in Slack
```

Nothing else is needed. `.venv/` and `.sqlite` are always regenerated locally.

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

