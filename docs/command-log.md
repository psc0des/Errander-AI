# Errander-AI Command Log

## Approval surface wording fix (2026-06-09)

```bash
# Audit all Slack-only approval references
grep -rn "Slack approval\|human Slack\|needs Slack\|require.*Slack\|Slack.*approv" \
  --include="*.py" --include="*.md" --include="*.yaml" --include="*.sh" \
  . | grep -v ".venv/"

# Full test suite — confirm no regressions after text-only changes
uv run pytest -x -q   # 2537 passed
```

---

## Enterprise inventory wizard + comment-preserving YAML (2026-06-09)

```bash
# Install ruamel.yaml
uv sync

# Run new wizard tests
uv run pytest tests/config/test_inventory_wizard.py -v

# Full test suite after changes
uv run pytest -x -q   # 2537 passed

# Lint + type check new files
uv run ruff check errander/config/inventory_wizard.py errander/config/configure.py tests/config/test_inventory_wizard.py
uv run mypy errander/config/inventory_wizard.py errander/config/configure.py

# Baseline mypy check (stash/pop to verify 621 pre-existing errors, no new ones)
git stash
uv run mypy .  # 621 errors in 70 files (baseline)
git stash pop
uv run mypy .  # 621 errors in 70 files (same — no regressions)
```

---

## Per-target actions: support (2026-06-09)

```bash
# Tests: verify schema changes, service restart CLI, and fan-out
uv run pytest tests/config/test_schema_actions.py tests/config/test_schema.py -x -q
uv run pytest tests/agent/test_service_restart_cli.py tests/config/test_schema_actions.py -x -q
uv run pytest tests/config/ tests/agent/ -q
uv run pytest -x -q   # full suite — 2517 passed
```

---

## configure.sh security hardening (2026-06-09)

```bash
# No shell commands run — all changes were targeted file edits
# Files changed: scripts/configure.sh, errander/config/add_target.py
# Security fixes: ELK API key encryption, 4 prompt_val→prompt_secret,
#   SIGNING_SECRET auto-generation, WEB_BASE_URL prompt
```

---

## langgraph-primer.md updated (2026-06-08)

```bash
# Read and updated docs/langgraph-primer.md — no shell commands needed
# Changes: removed stale docker_prune diagram, replaced interrupt() HITL section
# with accurate Slack-polling description, added real batch graph flow diagram
```

---

## Time-range selector + Prometheus+Grafana demoted (2026-06-08)

```bash
uv run ruff check errander/observability/metrics.py errander/safety/audit.py
uv run mypy errander/observability/metrics.py errander/safety/audit.py
uv run pytest --tb=short -q
git add errander/observability/metrics.py scripts/bootstrap.sh \
    SETUP.md README.md SETUP-Win-Controller.md \
    docs/MONITORING-VALIDATION.md docs/command-log.md \
    STATUS.md tasks/todo.md tasks/lessons.md
git commit -m "feat: add /ui/monitoring time-range selector; demote Prometheus+Grafana to external-VM-only"
git push
```

---

## Monitoring gap-fill — approval funnel, safety signals, durations (2026-06-05)

```bash
uv run ruff check errander/safety/audit.py errander/observability/metrics.py
uv run mypy errander/safety/audit.py errander/observability/metrics.py
uv run pytest --tb=short -q
git add errander/safety/audit.py errander/observability/metrics.py \
    STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: monitoring gap-fill — approval funnel, safety signals, duration averages"
git push
```

## Controller Monitoring page — /ui/monitoring (2026-06-05)

```bash
# Lint changed files
uv run ruff check errander/safety/audit.py errander/observability/metrics.py
# Type check changed files
uv run mypy errander/safety/audit.py errander/observability/metrics.py
# Full test suite
uv run pytest --tb=short -q
# Commit
git add errander/safety/audit.py errander/observability/metrics.py \
    STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: add /ui/monitoring — audit DB trends + live process counters with Chart.js"
git push
```

## teardown.sh + doc sync (2026-06-05)

```bash
git add scripts/teardown.sh SETUP.md README.md
git commit -m "feat: add teardown.sh — full uninstall for clean re-testing"
git push

git add STATUS.md tasks/todo.md docs/command-log.md
git commit -m "docs: doc sync — teardown.sh"
git push
```

## Grafana tarball rewrite — distro-agnostic, zero interactive prompts (2026-06-05)

```bash
# Rewrite install-grafana.sh: official tarball instead of apt package repo
# Also: bootstrap git-update fixes (fetch+reset+re-clone fallback)
git add scripts/install-grafana.sh scripts/bootstrap.sh README.md
git commit -m "fix: replace apt-based Grafana install with official tarball — zero interactive prompts, all distros"
git push

# Doc sync
git add STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: doc sync — Grafana tarball rewrite"
git push
```

## Grafana monitoring stack (2026-06-05)

```bash
# Add Grafana install script, provisioning files, dashboard JSON, update bootstrap.sh
git add scripts/install-grafana.sh scripts/bootstrap.sh SETUP.md deploy/grafana/
git commit -m "feat: add Grafana install + pre-provisioned Errander dashboard to monitoring stack"
git push

# Doc sync: README, OBSERVABILITY, STATUS, todo, command-log
git add README.md docs/OBSERVABILITY.md STATUS.md tasks/todo.md docs/command-log.md
git commit -m "docs: doc sync — Grafana monitoring stack"
git push
```

## Bootstrap refactor v2 — zero-sudo install.sh + Windows doc split (2026-06-04)

```bash
# Split Windows steps from SETUP.md into SETUP-Win-Controller.md
git add SETUP.md SETUP-Win-Controller.md
git commit -m "docs: split Windows controller steps into SETUP-Win-Controller.md"
git push

# Refactor bootstrap.sh (clone + Prometheus) and simplify install.sh (zero sudo)
git add scripts/bootstrap.sh scripts/install.sh SETUP.md
git commit -m "feat: bootstrap handles clone + Prometheus; install.sh is zero-sudo"
git push

# Doc sync: STATUS.md, todo.md, lessons.md, command-log.md
git add STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: doc sync — bootstrap v2 + Windows split session notes"
git push
```

## SETUP.md — LangSmith wiring docs + env vars (2026-06-02)

```bash
# Add LangSmith section to SETUP.md (after Prometheus monitoring section, before ELK);
# LANGCHAIN_* vars in .env template + env-var reference table;
# fix CLAUDE.md "Docker pruning" → "Docker hygiene"
git add SETUP.md CLAUDE.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: SETUP.md — LangSmith wiring docs, .env template vars, env-var table"
git push origin main
```

## README — reposition as supervised agentic AI + roadmap (2026-05-29)

```bash
# Reposition README opening (supervised agentic AI), name Layer A/B in intro,
# fix stale "Docker pruning" → "Docker hygiene", add near-term Roadmap section
git add README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: README — position as supervised agentic AI, name Layer A/B, add near-term roadmap"
# Push all pending commits to origin
git push origin main
```

## Dashboard Chat — implementation plan (Plan B) + roadmap (2026-05-29)

```bash
# Author Plan B (depends on Plan A; contract-altitude + reconcile step); link from Plan A;
# record persistent roadmap in STATUS "Next Up" + tasks/todo.md ROADMAP
git add tasks/dashboard-chat-implementation-plan.md tasks/investigation-agent-implementation-plan.md STATUS.md tasks/todo.md docs/command-log.md
git commit -m "docs: add dashboard-chat plan (Plan B) + record planned-features roadmap"
git push origin main
```

## Layer A Investigation Agent — implementation plan (2026-05-29)

```bash
# Read llm.py to ground the tool-calling design (AsyncOpenAI, complete() has no tools= yet)
# Author tasks/investigation-agent-implementation-plan.md (plan only — no feature code)
git add tasks/investigation-agent-implementation-plan.md STATUS.md tasks/todo.md docs/command-log.md
git commit -m "docs: add Layer A investigation-agent implementation plan (for later Sonnet build)"
git push origin main
```

## OBSERVABILITY.md — "What Errander can see" fixed signal menu (2026-05-29)

```bash
# Confirm gathering mechanisms before documenting (read, not edited):
#   errander/execution/disk_trend.py      — df -B1 → VMDiskHistoryStore → slope
#   errander/execution/failed_logins.py   — journald/auth.log failed SSH logins
#   errander/integrations/prometheus.py   — 3 fixed PromQL (CPU/mem/load)
#   errander/integrations/elk.py          — 1 fixed host-aggregated ES error query
# Add "What Errander can see — the fixed signal menu (Layer A inputs)" section
git add docs/OBSERVABILITY.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: OBSERVABILITY.md — document the fixed signal menu (what Errander can see)"
git push origin main
```

## README — "The two layers in one run" mapping (2026-05-29)

```bash
# Add step→layer mapping table to README "How It Works" (bridges concept + flow)
git add README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: README — map each step of a run to Layer A / Layer B in How It Works"
git push origin main
```

## OBSERVABILITY.md — built-in vs. bring-your-own boundary (2026-05-29)

```bash
# Rework OBSERVABILITY.md overview: built-in (owned, authoritative) vs.
#   bring-your-own external tools (Prometheus/Grafana, LangSmith-or-equiv, ELK/Loki);
#   add structured-logs surface; make LangSmith section tool-agnostic + value/N-A table
git add docs/OBSERVABILITY.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: OBSERVABILITY.md — built-in vs bring-your-own boundary, tool-agnostic external tracing"
git push origin main
```

## docs/OBSERVABILITY.md — per-layer observability reference (2026-05-29)

```bash
# Ground the doc in code before writing (read, not edited):
#   errander/models/events.py        — EventType enum (audit event categories)
#   errander/safety/audit.py         — AuditStore.get_events()
#   errander/safety/ai_audit.py      — AIDecisionStore.get_decisions/get_decision_by_id
#   errander/main.py                 — --audit / --ai-decisions CLI flags
#   docs/AI-ARCHITECTURE.md          — canonical two-layer model (align terminology)
# Author docs/OBSERVABILITY.md; add pointers in CLAUDE.md + README.md
git add docs/OBSERVABILITY.md CLAUDE.md README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: add OBSERVABILITY.md — per-layer observability (audit, AI decisions, Prometheus, LangSmith)"
git push origin main
```

## bootstrap — optional Prometheus install on controller node (2026-05-29)

```bash
# Confirm agent metrics port before choosing Prometheus port (avoid 9090 collision)
#   settings.metrics_port default 9090 → Prometheus uses 9091
# Author scripts/install-prometheus.sh (binary+systemd, distro-agnostic) +
#   wire opt-in step into scripts/bootstrap.sh (renumber /8 → /9)
bash -n scripts/bootstrap.sh           # syntax check → OK
bash -n scripts/install-prometheus.sh  # syntax check → OK
git ls-files -s scripts/bootstrap.sh   # confirm sibling scripts are mode 100644 (invoked via `bash`)
git add scripts/install-prometheus.sh scripts/bootstrap.sh README.md SETUP.md RUN.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: bootstrap optionally installs Prometheus on controller node to scrape the agent"
git push origin main
```

## README — observability section rewrite (2026-05-29)

```bash
# Verify Layer A/B separation + Prometheus wiring against code before editing README
#   Grep errander/execution/ for LLM refs → none (Layer B clean)
#   Grep start_metrics_server / _metrics_handler / metric singletons → wired from main.py:2213
# Edit README.md Observability section: two-layer table, Prometheus-on-controller install,
#   scrape config, port-collision note, optional LangSmith subsection
git add README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: README observability — two-layer split + install Prometheus on controller node"
git push origin main
```

## bootstrap creates errander-agent automatically (2026-05-28)

```bash
# Rewrite bootstrap.sh step 8: create errander-agent, move repo, rebuild venv as service user
# Collapse SETUP.md Step 1 Option A/B into single two-command Linux section
git add scripts/bootstrap.sh SETUP.md
git commit -m "feat: bootstrap creates errander-agent automatically — remove Option A/B choice from SETUP.md"
git push origin main
```

## bootstrap.sh — copy uv to /usr/local/bin (2026-05-28)

```bash
# Copy uv to /usr/local/bin after install so service users can reach it without ~/.local/bin in PATH
git add scripts/bootstrap.sh
git commit -m "fix: copy uv to /usr/local/bin in bootstrap so service users (errander-agent) can run it"
git push origin main
```

## bootstrap.sh — uv sync --extra dev (2026-05-28)

```bash
# Change bootstrap.sh uv sync to include --extra dev; update SETUP.md comments
git add scripts/bootstrap.sh SETUP.md STATUS.md tasks/todo.md docs/command-log.md
git commit -m "chore: bootstrap installs dev deps (uv sync --extra dev) — no manual step for Option A"
git push origin main
```

## README — explicit v1 target scope table (2026-05-26)

```bash
# Edit README.md — add v1 target scope table to "What Errander-AI Is — and Is Not"
git add README.md STATUS.md tasks/todo.md docs/command-log.md
git commit -m "docs: add v1 target scope table — serverless/managed cloud/k8s explicitly out of scope"
git push origin main
```

## add-target.sh — add target VMs without re-running configure.sh (2026-05-26)

```bash
# Verify add_target module imports cleanly
uv run python -c "from errander.config.add_target import main; print('import OK')"
# Stage and commit
git add errander/config/add_target.py scripts/add-target.sh SETUP.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: add-target.sh — add VMs to existing inventory without re-running configure.sh"
git push origin main
```

## Approval UI overhaul — per-item approval + decision reasoning (2026-05-25)

```bash
# Implement per-item approval cards, Decision Reasoning section, username fix
# Changes: approval.py, graph.py, metrics.py + 4 test files (3-tuple unpack)
uv run pytest tests/safety/test_approval.py tests/agent/test_graph.py tests/agent/test_plan_apply_flow.py tests/safety/test_deferred_artifact.py -q --tb=short
# Fix UnboundLocalError in approval_gate_node (approved_items init) + mypy dict() overload + ruff F541/E501/SIM105
uv run pytest -q --tb=short -x    # 126 tests passing
uv run ruff check errander/safety/approval.py errander/agent/graph.py errander/observability/metrics.py
uv run mypy errander/safety/approval.py errander/agent/graph.py errander/observability/metrics.py
git add errander/safety/approval.py errander/agent/graph.py errander/observability/metrics.py tests/safety/test_approval.py tests/agent/test_graph.py tests/agent/test_plan_apply_flow.py tests/safety/test_deferred_artifact.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: per-item approval UI, Decision Reasoning section, operator username in history"
git push origin main
```

## Doc sync — README stale sections + login + UI bind (2026-05-25)

```bash
# README.md: fix Docker Prune → docker_hygiene in Action Types + Safety Gates tables
# README.md: add login page + ERRANDER_UI_BIND to Web UI / configure sections
# STATUS.md, todo.md, lessons.md, command-log.md: sync all session phases
git add README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "docs: sync README docker_hygiene, login page, UI bind, session-cookie auth notes"
git push origin main
```

## Login page — feature bullet wording fix (2026-05-25)

```bash
# Changed 4 feature bullets in _ui_login_get to reflect full platform scope
git add errander/observability/metrics.py
git commit -m "fix: update login page feature bullets to reflect full platform scope"
git push origin main
```

## Session-cookie login page — Stitch redesign (2026-05-25)

```bash
# Redesigned login page using Sovereign Architect Stitch design system
# Split layout: left indigo shell + LED + bullets; right white card + gradient button
# Playwright tests rewritten for session-cookie auth (302, .lc-err, correct creds)
uv run pytest tests/ui/ -q --tb=short    # all UI tests pass
git add errander/observability/metrics.py tests/ui/test_ui_auth_playwright.py
git commit -m "feat: redesign login page with Sovereign Architect split layout"
git push origin main
```

## Session-cookie auth — replace Basic Auth popup (2026-05-25)

```bash
# Replaced HTTP Basic Auth with HTML login form + in-memory session store
# _session_auth_middleware, _ui_login_get, _ui_login_post, _ui_logout added
uv run pytest tests/ui/ -q --tb=short    # all UI tests pass
git add errander/observability/metrics.py tests/ui/test_ui_auth_playwright.py
git commit -m "feat: replace Basic Auth popup with session-cookie HTML login page"
git push origin main
```

## ERRANDER_UI_BIND fix — network-accessible Web UI (2026-05-25)

```bash
# Added ERRANDER_UI_BIND=0.0.0.0 to configure.sh .env write block
# Also added manually to controller .env and restarted service
sudo systemctl restart errander
sudo systemctl status errander
```

## Systemd EnvironmentFile fix — optional key loading (2026-05-25)

```bash
# Service crashed: ERRANDER_SECRETS_KEY not set
# Diagnosed: ~/.errander.key not listed in systemd EnvironmentFile directives
journalctl -u errander -n 50 --no-pager
# Fixed SETUP.md unit template; applied fix on VM:
sudo sed -i '/EnvironmentFile=.*\.env/a EnvironmentFile=-/root/.errander.key' /etc/systemd/system/errander.service
sudo systemctl daemon-reload
sudo systemctl restart errander
sudo systemctl status errander   # active (running)
```

## SETUP.md Step 8/9 clarifications (2026-05-25)

```bash
# Step 8: rewrote per-env dry-run section (multiple env examples)
# Step 9: added intro (service mode = all envs automatic) + web UI access instructions
git add SETUP.md
git commit -m "docs: clarify SETUP.md step 8 multi-env and step 9 service mode + web UI access"
git push origin main
```

## docker_available: wire enabled_actions into run_vm Send payloads (2026-05-25)

```bash
# enabled_actions was never passed into VMGraphState for run_vm — discover_node always saw None
# Fix: pass enabled_actions=list(_enabled_raw) in both route_after_validate and dispatch_current_wave

uv run mypy errander/agent/graph.py errander/agent/vm_graph.py   # 0 errors
uv run pytest -q --tb=short                                       # 2507 passed
git add errander/agent/graph.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: pass enabled_actions into run_vm VMGraphState — docker_available fallback now reachable"
git push origin main
```

## docker_available execution-phase fallback (2026-05-25)

```bash
# discover_node re-ran detect_os() independently of plan_vm_node → always docker_available=False
# Fix: mirror plan_vm_node wrapper-check pattern in discover_node; add enabled_actions to VMGraphState

uv run mypy errander/agent/vm_graph.py        # 0 errors
uv run pytest -q --tb=short                   # 2507 passed
git add errander/agent/vm_graph.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: docker_available wrapper fallback in discover_node (execution phase)"
git push origin main
```

## Dry-run report UX overhaul (2026-05-25)

```bash
# Three issues: garbled disk_cleanup sizes, no dry-run context, 0-login noise
# Fix: parse sizes in assess_node; BatchReport.dry_run flag; render_batch_report UX

uv run pytest tests/ -q --tb=short  # 2507 passed
uv run ruff check .                  # All checks passed
uv run mypy errander/ --no-error-summary  # 0 errors
git add errander/agent/subgraphs/disk_cleanup.py errander/models/reports.py
git add errander/agent/graph.py errander/observability/reporting.py
git add STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: dry-run report UX — parse disk sizes, dry-run banner, filter 0-login noise"
git push origin main
```

## docker_hygiene wrapper-mode docker_available fallback (2026-05-25)

```bash
# Diagnosed: docker_hygiene missing from dry-run plan even though enabled: true in inventory
# Root cause: detect_os() probes "docker info" without sudo → fails for errander user
# → docker_available=False → _is_action_applicable() filters out docker_hygiene
# Fix: plan_vm_node probes sudo -n errander-docker-assess-v2 --check as fallback

uv run pytest tests/ -q --tb=short  # 2507 passed
uv run ruff check .                  # All checks passed
uv run mypy errander/ --no-error-summary  # 0 errors
git add errander/agent/graph.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: docker_available fallback via wrapper --check in plan_vm_node"
git push origin main
```

## service_restart automated-batch exclusion (2026-05-24)

```bash
# Diagnosed: service_restart dispatched in automated batch with Unit ''
# Root cause: _enabled_actions included service_restart from inventory cfg.enabled
# Fix: ActionManifest.operator_triggered=True + filter in main.py _enabled_actions

uv run pytest tests/ -q --tb=short  # 2507 passed
uv run ruff check .                  # All checks passed
uv run mypy errander/ --no-error-summary  # 0 errors
git add errander/models/manifest.py errander/agent/subgraphs/service_restart.py errander/main.py
git add STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: exclude operator_triggered actions from automated batch planning"
git push origin main
```

## --check-targets ALLOWLIST OK confirmation (2026-05-24)

```bash
# Diagnosed: --check-targets silent on allowlist match; no output != verified
# Fix: added ALLOWLIST OK print in run_check_targets() when drift_found=False
# Updated test_no_drift_when_allowlist_matches to assert ALLOWLIST OK appears

uv run pytest tests/test_main.py -k "allowlist" -q  # 4 passed
git add errander/main.py tests/test_main.py
git commit -m "fix: --check-targets prints ALLOWLIST OK confirmation when units match"
git push origin main
```

## configure.sh SSH host key fix (2026-05-24)

```bash
# Verified the bug: ERRANDER_SSH_STRICT_HOST_KEYS missing from .env write in configure.sh
# → agent raised ConnectionError on every SSH attempt after configure.sh completed

# Read ssh.py to understand the strict/TOFU/known_hosts logic
# Read configure.sh to find the .env write block
# Read main.py to verify bootstrap_known_hosts runs before load_settings()

# Applied 3 edits to scripts/configure.sh:
# 1. Added SSH section to .env write block (ERRANDER_SSH_STRICT_HOST_KEYS=false default)
# 2. Added SSH bootstrap step after LLM verify (prompt + sed flip to true on success)
# 3. Added --check-targets <env> to Done banner Step 6

# Applied 3 edits to SETUP.md:
# 1. Updated configure.sh quick-path description to mention SSH host key pinning
# 2. Added SSH section to manual .env template in Step 5
# 3. Added ERRANDER_SSH_STRICT_HOST_KEYS + ERRANDER_SSH_KNOWN_HOSTS to env var table

# Verified git status — changes not yet committed (user decides when to commit)
git status --short
git diff --stat
```

## Workspace hygiene — gitignore cleanup (2026-05-24)

```bash
# Delete untracked artifact files
rm -f approvals_text.txt errander-*.png tmp_*.yaml *.sqlite-journal
rm -rf .playwright-mcp/

# Verify clean
git status --short  # only .gitignore modified

# Commit and push
git add .gitignore
git commit -m "chore: gitignore sqlite journals, playwright artifacts, UI screenshots"
git push origin main
```

## Quality gate cleanup (2026-05-24)

```bash
# Auto-fix ruff violations
uv run ruff check --fix .  # 237 fixed

# Check remaining violations
uv run ruff check .  # ~63 remaining after auto-fix

# Run mypy
uv run mypy errander/  # 15 errors initially → 0 after fixes

# Final verification
uv run ruff check .           # All checks passed
uv run mypy errander/         # Success: no issues found in 102 source files
uv run pytest --tb=short -q   # 2507 passed in 92.55s
```

## SRE P2 Fixes (2026-05-24)

```bash
# Targeted tests
uv run pytest tests/agent/test_operator_assistant.py -q  # 31 passed

# Ruff fix
uv run ruff check --fix tests/agent/test_operator_assistant.py  # 1 fixed

# Full suite
uv run pytest --tb=short -q  # 2507 passed
```

## SRE Trust Gap Fixes — P1 (2026-05-24)

```bash
# Targeted tests for SRE findings 1-3
uv run pytest tests/agent/test_decisions.py tests/agent/test_operator_assistant.py tests/integrations/test_llm.py -q  # 91 passed

# Ruff auto-fix on changed files
uv run ruff check --fix errander/agent/operator_assistant.py tests/agent/test_decisions.py  # 8 fixed, 0 remaining

# Verify ruff clean
uv run ruff check errander/integrations/llm.py errander/agent/decisions.py errander/agent/operator_assistant.py errander/main.py tests/agent/test_decisions.py tests/agent/test_operator_assistant.py  # All checks passed

# Mypy on changed files (pre-existing errors in main.py, not from our changes)
uv run mypy errander/integrations/llm.py errander/agent/decisions.py errander/agent/operator_assistant.py errander/main.py  # 4 pre-existing errors in main.py

# Full suite
uv run pytest --tb=short -q  # 2505 passed
```

## AI Trust Layer Phase 4 — Operational Memory Confidence (2026-05-24)

```bash
# Phase 4 targeted tests
uv run pytest tests/safety/test_vm_facts.py tests/agent/test_operator_assistant_facts.py -q  # 41 passed

# Ruff + mypy on changed files
uv run ruff check errander/safety/vm_facts.py errander/agent/operator_assistant.py  # clean
uv run mypy errander/safety/vm_facts.py errander/agent/operator_assistant.py  # clean

# Full suite
uv run pytest --tb=short -q  # 2475 passed
```

## AI Trust Layer Phase 2 — Prompt Versioning & Replay Evals (2026-05-24)

```bash
# Phase 2 specific tests
uv run pytest tests/ai_evals/test_replay.py tests/safety/test_migrations.py -v  # 41 passed

# Lint on Phase 2 files
uv run ruff check errander/evals/ tests/ai_evals/test_replay.py errander/main.py  # clean

# Full suite
uv run pytest --tb=no -q  # 2462 passed
```

## AI Trust Layer Phase 3 — Context Budget & Redaction Policy (2026-05-24)

```bash
# New Phase 3 tests
uv run pytest tests/safety/test_context_redactor.py tests/safety/test_context_budget.py -v  # 37 passed

# Lint + type check on Phase 3 files
uv run ruff check errander/safety/context_redactor.py errander/safety/context_budget.py errander/agent/operator_assistant.py tests/safety/test_context_redactor.py tests/safety/test_context_budget.py
uv run mypy errander/safety/context_redactor.py errander/safety/context_budget.py errander/agent/operator_assistant.py

# Full suite
uv run pytest --tb=no -q  # 2434 passed
```

## AI Trust Layer Phase 1 — Decision Explainability + Adversarial Tests (2026-05-23)

```bash
# New test files — all passing
uv run pytest tests/safety/test_ai_decisions_store.py tests/ai_evals/test_adversarial.py -v  # 30 passed

# Ruff on modified files — check new code is clean
uv run ruff check errander/observability/metrics.py tests/ai_evals/test_adversarial.py tests/safety/test_ai_decisions_store.py errander/safety/ai_audit.py errander/main.py  # clean on new code

# Mypy on modified source files — only pre-existing errors remain
uv run mypy errander/safety/ai_audit.py errander/observability/metrics.py  # 1 pre-existing error in metrics.py

# Full suite — 2397 passing
uv run pytest --tb=no -q  # 2397 passed
```

## Post-Residual Fixes — deferred hygiene wiring + doc P2 (2026-05-23)

```bash
# Deferred replay regression test after _window_opener hygiene_manager fix
uv run pytest tests/agent/test_deferred_replay.py -x -q  # 17 passed

# Full agent + main suite — no regressions
uv run pytest tests/test_main.py tests/agent/ -x -q  # 846 passed
```

## SRE Residual Fixes — 5 issues from Opus 4.7 validation (2026-05-23)

```bash
# Targeted test run: docker hygiene after Finding 1 (--no-trunc wrapper fix + TestWrapperIdFormat)
uv run pytest tests/agent/subgraphs/test_docker_hygiene.py -x -q  # passed

# Targeted test run: service_restart after Finding 2 (window check + VM locking)
uv run pytest tests/agent/test_service_restart_cli.py -x -q  # passed

# Targeted test run: disk_cleanup after Finding 4 (orphaned-deps exact preview + drift gate)
uv run pytest tests/agent/subgraphs/test_disk_cleanup.py -x -q  # passed

# Ruff lint on changed Python files (shell file skipped — ruff does not lint .sh)
uv run ruff check errander/main.py errander/agent/subgraphs/disk_cleanup.py errander/agent/graph.py tests/agent/test_service_restart_cli.py tests/agent/subgraphs/test_docker_hygiene.py tests/agent/subgraphs/test_disk_cleanup.py  # All checks passed

# Full suite validation
uv run pytest -x -q  # 2366 passed (+12 new tests from Findings 1, 2, 4)
```

## AI SRE Gap Fix — 7 safety/quality fixes (2026-05-23)

```bash
# Full suite baseline before starting
uv run pytest -x -q  # 2299 passed (baseline)

# Run service_restart tests after P0-1 live-mode CLI gate
uv run pytest tests/agent/test_service_restart_cli.py -x -q  # 6 passed

# Run disk_cleanup tests after P1-3 orphaned-deps opt-in
uv run pytest tests/agent/subgraphs/test_disk_cleanup.py -x -q  # 6 new tests passed

# Run command_builder tests after P2-3 shell injection hardening
uv run pytest tests/execution/test_command_builder.py -x -q  # passed

# Run schema_actions tests after P2-3 config-load-time validation
uv run pytest tests/config/test_schema_actions.py -x -q  # passed

# Run backup_verify tests after P2-2 manifest docstring
uv run pytest tests/agent/subgraphs/test_backup_verify.py -x -q  # passed

# Run service_restart subgraph tests for adversarial unit names
uv run pytest tests/agent/subgraphs/test_service_restart.py -x -q  # passed

# Run migrations tests after migration 8 (plan_snapshots)
uv run pytest tests/safety/test_migrations.py -x -q  # passed

# Run plan inspection tests after P2-1 full plan inspectable
uv run pytest tests/agent/test_plan_inspection_p21.py -x -q  # passed

# Fix: test_snapshot_node_rejects_bad_unit → assert_not_awaited (snapshot_node doesn't return status=FAILED)
# Fix: test_tampered_token_rejected_by_verifier → replace entire sig with known-wrong base64 (not flip last char)
# Fix: migration tests updated to expect versions=[0..8] and count=9

# Full suite validation
uv run pytest -x -q  # 2354 passed (+55 new tests)

# Lint all changed files
uv run ruff check errander/safety/migrations.py errander/safety/audit.py errander/agent/graph.py errander/web/server.py errander/main.py errander/agent/subgraphs/disk_cleanup.py errander/execution/commands.py  # All checks passed
```

## decisions.py semantic debt — DOCKER_PRUNE → DOCKER_HYGIENE (2026-05-22)
```bash
# Lint decisions.py after each edit
uv run ruff check errander/agent/decisions.py  # All checks passed

# Full suite validation (caught 6 stale test references to DOCKER_PRUNE)
uv run pytest -x -q  # 2258 passed after fixing all references
```

## Docker hygiene v1.2 — unused image execution scope (2026-05-22)
```bash
# Targeted test run after changes
uv run pytest tests/agent/subgraphs/test_docker_hygiene.py tests/safety/test_hygiene_approval.py -x -q  # 104 passed

# Full suite validation
uv run pytest -x -q  # 2258 passed

# Lint changed source files
uv run ruff check errander/models/docker_hygiene.py errander/agent/subgraphs/docker_hygiene.py errander/safety/hygiene_approval.py  # All checks passed
```

## Docker hygiene v1.1 Session 3 (2026-05-22)
```bash
# Delete docker_prune source files and tests
# (done via Edit/Write tools, not shell commands)

# Run tests to verify source changes compile and pass
uv run pytest -q --tb=no --ignore=tests/ui   # 2075 passed

# After test file fixes — run specific failing files
uv run pytest tests/agent/subgraphs/test_registry.py tests/agent/subgraphs/test_service_restart_manifest.py tests/agent/test_state_serialization.py tests/agent/test_sudo_preflight.py tests/models/test_actions.py tests/config/test_schema_actions.py tests/config/test_migrate.py tests/safety/test_rollback.py tests/test_main.py tests/config/test_schema.py tests/execution/test_target_validation.py -v --tb=short

# Full suite validation
uv run pytest -q --tb=no   # 2252 passed
```

## Docker hygiene v1.1 Session 2b-iii (2026-05-22)
```bash
# Run new orchestration test suite
uv run pytest tests/agent/test_hygiene_orchestration.py -x -q   # 8 passed

# Fix: fake_runner() got unexpected keyword argument — added **_: object
# Fix: ApprovalSurface.SLACK → ApprovalSurface.SLACK_REPLY
# Fix: patch target errander.safety.hygiene_approval.poll_hygiene_replies_once (not vm_graph)
uv run pytest tests/agent/test_hygiene_orchestration.py -x -q   # 8 passed

# Full suite
uv run pytest -x -q   # 2317 passed (+8 net new)

# Ruff on changed files
uv run ruff check errander/config/settings.py errander/observability/metrics.py errander/agent/vm_graph.py errander/agent/graph.py errander/main.py tests/agent/test_hygiene_orchestration.py   # All checks passed

# Mypy on changed files
uv run mypy errander/config/settings.py errander/observability/metrics.py errander/agent/vm_graph.py errander/agent/graph.py errander/main.py   # no new errors
```

## SRE QA round 3 — P2 inventory/admin static facts (2026-05-21)

```bash
# Verify 4 P2 fixes pass regression tests
uv run pytest tests/ui/ -x --tb=short -q
# 1 failed — nginx/gunicorn in page_settings restart_rows; gated _unit_iter
# 177 passed in 48.90s after fix
```

---

## SRE QA round 2 — remaining fixture leaks (2026-05-21)

```bash
# Run provider tests after adding regression section 7
uv run pytest tests/ui/test_web_providers.py -x --tb=short -q
# 1 failed — Qwen3-8B-AWQ in settings env table; fixed description text
# 1 failed — prod-web-01 echoed in not-found message; fixed test (expected)

# All 52 provider tests pass
uv run pytest tests/ui/test_web_providers.py --tb=short -q
# 52 passed in 0.40s

# Full UI suite — 177 tests passing (9 new regression tests)
uv run pytest tests/ui/ --tb=short -q
# 177 passed in 49.38s
```

---

## Evidence gating — fixture data leak fix (2026-05-21)

```bash
# Verify all UI tests still pass after evidence gating edits
uv run pytest tests/ui/ -v --tb=short
# 168 passed in 51.58s

# Run provider tests alone to confirm no regressions
uv run pytest tests/ui/test_web_providers.py -v --tb=short
# 43 passed in 0.43s

# Check git diff to review all server.py evidence gating changes
git diff errander/web/server.py --stat
# errander/web/server.py | 228 +++/--- (181 insertions, 47 deletions)
```

---

## Provider layer — Operations Hub backed by real stores (2026-05-21)

```bash
# Run provider test suite (new tests)
uv run pytest tests/ui/test_web_providers.py -v --tb=short
# 43 passed in 0.43s

# Run full UI suite to verify no regressions
uv run pytest tests/ui/ -v --tb=short
# 168 passed in 46.79s
```

---

## P0 regression fix — f-string JS brace escape (2026-05-21)

```bash
# Reproduce the SyntaxError
uv run python -m py_compile errander/web/server.py
# SyntaxError: f-string: expecting '=', or '!', or ':', or '}'

# Verify fix
uv run python -m py_compile errander/web/server.py && echo OK
uv run python -c "import errander.web.server; print('import OK')"

# Run new smoke tests
uv run pytest tests/ui/test_web_server_smoke.py -v --tb=short
# 14 passed

# Full suite
uv run pytest --tb=short -q
# 2120 passed
```

---

## Project B3 — vm-facts CLI (2026-05-20)

```bash
# Run new B3 tests
uv run pytest tests/commands/test_vm_facts.py -q --tb=short
# 16 passed

# Lint + type-check
uv run ruff check errander/commands/vm_facts.py errander/main.py --fix
uv run mypy errander/commands/vm_facts.py --ignore-missing-imports

# Full suite
uv run pytest --tb=short -q
# 2106 passed
```

---

## QA/SRE UI bug fixes (2026-05-20)

```bash
# Run full test suite after all 4 QA bug fixes
uv run pytest --tb=short -q
# 2090 passed in 75.72s
```

---

## Project A — LangGraph Workflow Durability A2–A6 (2026-05-20)

```bash
# Run new A2 BatchStore tests
.venv/Scripts/pytest.exe tests/safety/test_batches.py -q --tb=short
# 16 passed

# Run new A3 serialization tests
.venv/Scripts/pytest.exe tests/agent/test_state_serialization.py -q --tb=short
# 17 passed

# Run new A4 ArtifactStore tests
.venv/Scripts/pytest.exe tests/safety/test_artifacts.py -q --tb=short
# 13 passed

# Run new A5 AgentLease tests
.venv/Scripts/pytest.exe tests/safety/test_agent_lease.py -q --tb=short
# 14 passed

# Full regression suite after each phase
.venv/Scripts/pytest.exe -q --tb=short
# 2090 passed, 1 warning

# Lint check on all new/modified files
.venv/Scripts/ruff.exe check errander/ tests/
# All checks passed.
```

## Glossary verify + Inventory/Settings polish + Mobile responsive (2026-05-20)

```bash
# Render-check all three pages inline
.venv/Scripts/python.exe -c "from errander.web.server import page_inventory, page_settings, page_glossary; ..."
# All checks passed

# Full regression suite
.venv/Scripts/pytest.exe -q --tb=short
# 2027 passed, 1 warning
```

## VM Detail + Batches page enrichment (2026-05-20)

```bash
# Render-check both pages inline (no server needed)
.venv/Scripts/python.exe -c "from errander.web.server import page_vm, page_batches; ..."
# All assertions passed

# Full regression suite
.venv/Scripts/pytest.exe -q --tb=short
# 2027 passed, 1 warning
```

## Node Exporter flag + configure.sh interactive setup (2026-05-20)

```bash
# Run vm_metrics tests (flag-driven discover)
.venv/Scripts/pytest.exe tests/observability/test_vm_metrics.py -q --tb=short
# 35 passed in 0.19s

# Full regression suite
.venv/Scripts/pytest.exe -q --tb=short
# 2027 passed, 1 warning in 76.25s
```

## Real metrics collection + live API (2026-05-20)

```bash
# Verify vm_metrics module imports cleanly
uv run python -c "from errander.observability.vm_metrics import parse_probe_output, query_metrics, collect_all, cleanup_old_metrics; print('OK')"

# End-to-end test: migration, insert, query_metrics in memory
uv run python -c "
import asyncio, aiosqlite
from errander.safety.migrations import run_migrations
from errander.observability.vm_metrics import query_metrics
# ... full test script — 3 cpu rows, 3 mem rows, 1 disk row inserted; 24h query returns all
"

# Smoke-test startup hook (no real inventory = scheduler skipped, db always attached)
uv run python -c "import asyncio, os; os.environ['ERRANDER_AUDIT_DB_URL']=':memory:'; ..."

# Lint auto-fix vm_metrics.py (UP037 quoted annotations, I001 import sort)
uv run ruff check --fix errander/observability/vm_metrics.py

# Run migration tests alone
.venv/Scripts/pytest.exe tests/safety/test_migrations.py -q --tb=short

# Full test suite (1992 tests, 0 failures)
.venv/Scripts/pytest.exe -q --tb=short
```

## VM Detail — Metricbeat-style sparklines (2026-05-20)

```bash
# Syntax check after adding _sparkline_svg, _mini_sparkline_svg, _vm_resource_trends helpers
python -c "import errander.web.server; print('OK')"

# Verify Resource Trends and disk mini-sparklines render in HTML
curl -s -b /tmp/ui_cookie.txt http://localhost:8099/vm/prod-api-01 | \
  grep -o "Resource Trends\|vm-trends-card\|trend-btn\|polyline\|_setTrend"

# Verify OOM scenario (prod-db-01): current mem 94%, /var ↑16% 24h
curl -s -b /tmp/ui_cookie.txt http://localhost:8099/vm/prod-db-01 | \
  grep -o "94%\|↑16% 24h\|↑10% 24h"

# Bounce server after endpoint-pinning fix (last history point = current CPU/MEM)
# PowerShell: Stop-Process -Id 18336,20000 -Force
uv run python -m errander.web   # restart

# Visual verification: Chrome browser screenshots via claude-in-chrome MCP
# Confirmed: 24h sparklines, 7d toggle, dashed threshold lines, disk mini-sparks
```

## SRE UX punch-list — P0 wave (2026-05-20)

```bash
# UI location discovery — the lesson here is grep first, ask Stitch second
# (see tasks/lessons.md "Always confirm where the UI ACTUALLY lives before editing")
grep -rn "RUN BATCH NOW\|Admin Controls\|sidebar" errander/
ls errander/web/                              # found: server.py (3.2k LoC), data.py, __main__.py
wc -l errander/web/*.py                       # 536 + 3206 lines

# Pre-edit safety: parse-check both edited files before live-test
python -c "import ast; ast.parse(open('errander/web/server.py', encoding='utf-8').read()); print('OK')"
python -c "import ast; ast.parse(open('errander/web/evidence.py', encoding='utf-8').read()); print('OK')"

# Render-check every page function (catches f-string substitution errors)
python -c "from errander.web import server; \
  funcs=['page_fleet','page_approvals','page_audit','page_batches','page_inventory', \
         'page_settings','page_admin','page_glossary','page_agent']; \
  [print(fn, 'OK', len(server.layout(fn,'/',fn,'',getattr(server,fn)()))) for fn in funcs]"

# Live server bounce (PID was holding stale code, needed restart to pick up edits)
netstat -ano | grep ":8099"                   # found PID 31748
# PowerShell: Stop-Process -Id 31748 -Force
python -m errander.web                        # restart on :8099

# Verify rendered HTML contains every SRE-required marker
curl -s -c /tmp/c.txt -L -d "username=admin&password=errander" http://localhost:8099/login -o /dev/null
curl -s -b /tmp/c.txt http://localhost:8099/approvals | grep -c "Plan Hash"            # 2 ✓
curl -s -b /tmp/c.txt http://localhost:8099/approvals | grep -c "LAYER B · DETERMINISTIC" # 2 ✓
curl -s -b /tmp/c.txt http://localhost:8099/approvals | grep -c "evidence-grid"        # 4 ✓
curl -s -b /tmp/c.txt http://localhost:8099/admin     | grep -c "DESTRUCTIVE — AUDITED"# 2 ✓
curl -s -b /tmp/c.txt http://localhost:8099/agent     | grep -c "SAFETY BOUNDARY"       # 1 ✓
```

Also: Stitch MCP probes (`list_screens`, `get_project`) used early in session before discovering the UI was in-repo. Lesson logged.

## SRE UX punch-list — P1 wave (Fleet + Audit, 2026-05-20)

```bash
# Fleet: _operator_queue() helper added, page_fleet() now prepends it before KPIs;
# VM grid section title changed from "VM Fleet" → "Fleet Inventory" with /inventory link.
# Audit: page_audit() rewritten to expand each event into 2 <tr>s — summary + collapsible
# evidence panel. Filters wired via data-* attributes + vanilla JS. CSV/JSON export uses
# a Blob + anchor download, honoring tr.style.display for filter-aware export.

python -c "from errander.web import server; \
  h = server.page_fleet(); \
  assert 'Operator Queue' in h and 'Fleet Inventory' in h"
python -c "from errander.web import server; \
  h = server.page_audit(); \
  assert 'audit-row-expand' in h and 'EXPORT JSON' in h and 'Plan Hash' in h"

# Bounced again to pick up edits (PID was 30564 this round)
# Verified interactively: clicked failed staging-api-01 row → evidence panel revealed
# event_id, action_id, plan_hash, approver, approval_source, before/after, command,
# stdout, stderr (red), rollback_status (green), deep-link chips.
```

## P0-1 final closure — verify_node query scope fix (2026-05-19)

```bash
# Run verify node tests only
uv run pytest tests/agent/subgraphs/test_patching.py::TestVerifyNode -v
# 7 passed (including 2 new partial-update tests)

# Full suite regression check
uv run pytest --tb=no -q
# 1991 passed, 0 failures
```

## Glossary overhaul (2026-05-19)

```bash
# Syntax-check server.py after _GLOSS + _WF_JS edits
uv run python -c "import errander.web.server; print('OK')"

# Kill old server (stale PID from prior session) and restart
MSYS_NO_PATHCONV=1 taskkill.exe /PID 15056 /F
uv run python -m errander.web &>/tmp/web_server.log &

# Browser: verified glossary page at http://localhost:8099/glossary
#   - New ACTIONS: Backup Verify, Service Restart
#   - New SAFETY: Layer A, Layer B
#   - INFRA: vLLM → LLM Endpoint
#   - Workflow: Plan Enrichment badge P0-1 → PRE-APPROVAL, sublabel fixed
#   - Workflow: Action Exec. sublabel → "6 sub-graphs · all actions"
```

## Login screen + Godmode E2E sweep (2026-05-19)

```bash
# Verify login page, token logic, and route registration
uv run python -c "from errander.web.server import page_login, create_app, _valid_token, _make_token; ..."

# Curl test — verify error block returned for wrong credentials
curl -s -X POST http://localhost:8099/login -d "username=admin&password=badpass" | grep -A3 "login-error"

# Verify all modified pages render after E2E fixes
uv run python -c "from errander.web.server import page_fleet, page_agent, page_settings, page_inventory, page_vm; ..."

# Kill old server (Windows)
MSYS_NO_PATHCONV=1 taskkill.exe /PID <pid> /F

# UI test suite — 111 passed, 0 regressions
uv run pytest tests/ui/ -x -q
```

---

## /agent page — route handler + route registration (2026-05-19)

```bash
# Verify page_agent renders without errors
uv run python -c "from errander.web.server import page_agent; page_agent(); print('page_agent OK')"

# Verify /agent route is registered in create_app()
uv run python -c "from errander.web.server import create_app, handle_agent; app = create_app(); routes = [str(r.resource) for r in app.router.routes()]; print('/agent registered:', any('/agent' in r for r in routes))"

# UI test suite — 111 passed, 0 regressions
uv run pytest tests/ui/ -x -q
```

---

## UI overhaul — information density + actionability (2026-05-19)

```bash
# Import + render check — all 5 key pages
uv run python -c "from errander.web.server import page_fleet, page_approvals, page_audit, page_vm, page_batches; page_fleet(); page_approvals(); page_audit(); page_vm('prod-api-01'); page_batches(); print('OK')"

# UI test suite — 111 passed
uv run pytest tests/ui/ -x -q
```

---

## Fix — audit detail strings + Playwright test sync (2026-05-18)

```bash
# Full test suite — 1969 passed
uv run pytest tests/ -x -q --tb=short
```

---

## Bug fix — vm_plans duplicate (2026-05-18)

```bash
# Import check after graph.py changes
uv run python -c "import errander.agent.graph; print('OK')"

# Graph tests
uv run pytest tests/agent/test_graph.py -x -q --tb=short  # 33 passed
```

---

## UI redesign — Sovereign Architect (2026-05-18)

```bash
# Live dry-run validation on DR env (diagnosed stale lock, then cleared it)
uv run python -m errander --run-now --env dr --inventory inventory.yaml --dry-run --force --force-reason "initial dry-run validation"
rm /errander/.errander-locks/dr_vm-dr-01.lock

# Second dry-run after lock cleared — 3 actions simulated OK
uv run python -m errander --run-now --env dr --inventory inventory.yaml --dry-run --force --force-reason "initial dry-run validation"

# Live run (no --dry-run) — hit approval gate, diagnosed UI bind issue
uv run python -m errander --run-now --env dr --inventory inventory.yaml --force --force-reason "live validation"

# Syntax-check metrics.py after CSS rewrite
uv run python -c "import errander.observability.metrics; print('OK')"

# Commit + push
git add errander/observability/metrics.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: Sovereign Architect UI redesign + Test LLM button"
git push
```

---

## OSS readiness review (2026-05-18)

```bash
# Full suite after target_validation.py fix
uv run pytest tests/ -x -q --tb=short  # 1969 passed

# Push all session commits
git push  # RUN.md, SETUP.md, main.py, target_validation.py changes
```

---

## Phase D1 — Full prompt + context capture in ai_decisions (2026-05-18)

```bash
# New tests
uv run pytest tests/safety/test_ai_audit.py -v  # 16 passed

# Full suite
uv run pytest --tb=short -q  # 1969 passed

# Lint + typecheck
uv run ruff check errander/safety/ai_audit.py errander/agent/decisions.py tests/safety/test_ai_audit.py
uv run mypy errander/safety/ai_audit.py errander/safety/migrations.py errander/agent/decisions.py
# All clean
```

---

## Phase A1 + B1/B2 — Durability measurement + VMFactsStore (2026-05-18)

```bash
# Run new tests
uv run pytest tests/observability/test_startup_scan.py tests/observability/test_measure_durability.py tests/safety/test_vm_facts.py tests/agent/test_operator_assistant_facts.py -x -q
# 52 passed

# Full suite
uv run pytest -x -q  # 1953 passed

# Lint + typecheck
uv run ruff check errander/observability/durability.py errander/observability/startup_scan.py errander/safety/vm_facts.py errander/agent/operator_assistant.py errander/models/analysis.py errander/main.py
uv run ruff check --fix tests/agent/test_operator_assistant_facts.py tests/observability/test_measure_durability.py tests/observability/test_startup_scan.py tests/safety/test_vm_facts.py errander/safety/vm_facts.py errander/agent/operator_assistant.py
uv run mypy errander/observability/durability.py errander/observability/startup_scan.py errander/safety/vm_facts.py errander/agent/operator_assistant.py errander/models/analysis.py errander/main.py
# Success: no issues found in 6 source files

# CLI deliverable
uv run python -m errander --measure-durability
# Errander durability snapshot  window: last 14 days
#   Batches: total=0  completed=0  interrupted=0  completion_rate=0.0%
#   (no events in DB within 14-day window — BATCHES_INTERRUPTED_TOTAL=0)
```

---

## SRE audit fix Round 3 — service_restart wrapper probed in check_target (2026-05-17)

```bash
uv run pytest tests/execution/test_target_validation.py -v --tb=short  # 11 passed
uv run pytest --tb=no -q  # 1901 passed
uv run ruff check errander/execution/target_validation.py tests/execution/test_target_validation.py
uv run mypy errander/execution/target_validation.py tests/execution/test_target_validation.py
git add errander/execution/target_validation.py tests/execution/test_target_validation.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md README.md
git commit -m "fix: generic manifest-driven wrapper probe in check_target covers service_restart"
git push origin main
```

## README test count sync (2026-05-17)

```bash
# README.md still showed 1707 — stale by 191 tests. Updated to 1898.
git add README.md docs/command-log.md
git commit -m "docs: update README test count to 1898"
git push origin main
```

## SRE audit fix Round 2 — route_plan_vms Send payload + manifest-derived binaries (2026-05-17)

```bash
# Remove unused type: ignore comments flagged by mypy
uv run mypy errander/agent/graph.py errander/execution/target_validation.py \
  errander/agent/subgraphs/patching.py tests/agent/test_enabled_actions_planning.py  # clean
uv run ruff check errander/agent/graph.py errander/execution/target_validation.py \
  errander/agent/subgraphs/patching.py tests/agent/test_enabled_actions_planning.py  # All checks passed
uv run pytest --tb=no -q  # 1898 passed, 0 skipped
git add errander/agent/graph.py errander/agent/subgraphs/patching.py \
  errander/execution/target_validation.py tests/agent/test_enabled_actions_planning.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "fix: route_plan_vms passes enabled_actions in Send payload, manifest-derived binary checks"
git push origin main
```

## SRE audit fix Round 1 — enabled_actions enforcement (2026-05-17)

```bash
# Bug 1: enabled_actions not passed to prioritize_actions
# Bug 2: check_target binary checks not per-action
uv run pytest tests/agent/test_enabled_actions_planning.py tests/execution/test_target_validation.py -x -q  # 14 passed
uv run ruff check errander/execution/target_validation.py errander/agent/graph.py errander/main.py \
  tests/agent/test_enabled_actions_planning.py tests/execution/test_target_validation.py  # All checks passed
uv run mypy errander/execution/target_validation.py errander/agent/graph.py errander/main.py \
  tests/agent/test_enabled_actions_planning.py tests/execution/test_target_validation.py  # Success
uv run pytest --tb=no -q  # 1893 passed, 0 skipped
git add errander/execution/target_validation.py errander/agent/graph.py errander/main.py \
  tests/agent/test_enabled_actions_planning.py tests/execution/test_target_validation.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "fix: enforce enabled_actions in planning and check-targets binary probes"
git push origin main
```

## RUN.md catch-up (2026-05-17)

```bash
# --migrate-inventory and --restart-service sections missed in prior commits
git add RUN.md STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "docs: RUN.md --migrate-inventory and --restart-service sections"
git push origin main
```

## v1-action-opt-in commit S.4 (2026-05-17)

```bash
# SETUP.md service-restart section + CLAUDE.md/README update + example/inventory.yaml + learning doc
# Documentation-only commit — no Python files changed; ruff/mypy not applicable
uv run pytest --tb=no -q                      # 1885 passed, 0 skipped
git add SETUP.md CLAUDE.md README.md example/inventory.yaml \
  docs/learning/40-service-restart-module.md \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "docs: SETUP.md service-restart section, CLAUDE.md/README update, learning doc"
git push origin main
git tag v1-rc1
git push origin v1-rc1
```

## v1-action-opt-in commit S.3 (2026-05-17)

```bash
# --restart-service CLI + restartable_units validation + allowlist drift + approval tests
uv run pytest tests/config/test_schema_actions.py tests/test_main.py tests/agent/test_approval.py -x -q  # 71 passed
uv run ruff check errander/main.py errander/config/schema.py tests/config/test_schema_actions.py tests/test_main.py tests/agent/test_approval.py  # All checks passed
uv run ruff check tests/test_main.py --fix    # fixed I001 import order (added yaml import)
uv run mypy errander/main.py errander/config/schema.py tests/config/test_schema_actions.py tests/test_main.py tests/agent/test_approval.py  # clean
uv run pytest --tb=no -q                      # 1885 passed, 0 skipped
git add errander/config/schema.py errander/main.py \
  tests/config/test_schema_actions.py tests/test_main.py tests/agent/test_approval.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: --restart-service CLI, restartable_units validation, allowlist drift check"
git push origin main
```

## v1-action-opt-in commit S.2 (2026-05-17)

```bash
# Created install-systemctl-restart-wrapper.sh + 23 drift tests
uv run pytest tests/scripts/test_install_systemctl_restart_wrapper.py -v  # 23 passed
uv run ruff check tests/scripts/test_install_systemctl_restart_wrapper.py  # All checks passed
uv run pytest --tb=no -q  # 1859 passed
git add scripts/install-systemctl-restart-wrapper.sh \
  tests/scripts/test_install_systemctl_restart_wrapper.py \
  STATUS.md docs/command-log.md tasks/todo.md
git commit -m "feat: install-systemctl-restart-wrapper.sh, allowlist, wrapper drift test"
git push origin main
```

## v1-action-opt-in commit S.1 (2026-05-17)

```bash
# service_restart sub-graph + manifest + state model + 7 audit event types
uv run pytest tests/agent/subgraphs/test_service_restart.py tests/agent/subgraphs/test_service_restart_manifest.py tests/agent/subgraphs/test_service_restart_parser.py tests/agent/subgraphs/test_registry.py -v  # 59 passed
uv run pytest --tb=no -q                 # 1836 passed
uv run ruff check errander/agent/subgraphs/service_restart.py errander/models/service_restart.py tests/agent/subgraphs/test_service_restart*.py  # All checks passed
uv run ruff check tests/agent/subgraphs/test_service_restart_parser.py --fix  # fixed 1 import sort
uv run mypy errander/models/service_restart.py errander/agent/subgraphs/service_restart.py  # clean
git add errander/models/service_restart.py errander/agent/subgraphs/service_restart.py \
  errander/agent/subgraphs/__init__.py errander/models/events.py errander/models/actions.py \
  tests/agent/subgraphs/test_service_restart.py tests/agent/subgraphs/test_service_restart_manifest.py \
  tests/agent/subgraphs/test_service_restart_parser.py tests/agent/subgraphs/test_registry.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: service_restart sub-graph, manifest, RestartContext, 7 audit events"
git push origin main
```

## v1-action-opt-in commit 2.1 (2026-05-17)

```bash
# Created scripts/install-docker-wrappers.sh, collapsed SETUP.md Docker section, added drift tests
uv run pytest tests/scripts/ -v          # 18 passed
uv run pytest --tb=no -q                 # 1790 passed
uv run ruff check tests/scripts/         # All checks passed (after removing unused subprocess+pytest imports)
uv run mypy tests/scripts/               # Success: no issues found in 2 source files
git add scripts/install-docker-wrappers.sh SETUP.md tests/scripts/__init__.py \
  tests/scripts/test_install_docker_wrappers.py \
  STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: install-docker-wrappers.sh, SETUP.md collapse, wrapper drift test"
git push origin main
```

## v1-action-opt-in commit 1.3 (2026-05-17)

```bash
# Added TARGET_PREFLIGHT_FAILED event, BatchStatus enum, registry-driven wrapper check
# sudo_preflight_node uses BUILTIN_ACTIONS for wrappers, emits TARGET_PREFLIGHT_FAILED
# SETUP.md Docker section → Optional: Docker cleanup; CLAUDE.md v1 scope; README matrix
uv run pytest tests/agent/test_sudo_preflight.py tests/agent/test_vm_graph.py::TestTargetPreflightFailed tests/test_main.py::TestCheckTargetsRegistryDriven -v  # 21 passed
uv run pytest   # 1772 passed
git add errander/models/events.py errander/models/reports.py errander/execution/target_validation.py \
  errander/agent/vm_graph.py tests/agent/test_sudo_preflight.py tests/agent/test_vm_graph.py \
  tests/test_main.py SETUP.md CLAUDE.md README.md STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: registry-driven preflight, TARGET_PREFLIGHT_FAILED, BatchStatus, SETUP.md"
git push origin main
```

## v1-action-opt-in commit 1.2 (2026-05-17)

```bash
# Implemented migrate_inventory() + --migrate-inventory CLI + 28 new tests
uv run pytest tests/config/test_migrate.py tests/test_main.py -v  # 47 passed
uv run pytest                         # 1764 passed
uv run ruff check . 2>&1 | grep "errander/config/migrate"  # clean
uv run mypy . 2>&1 | grep "errander/config/migrate"        # clean
git add errander/config/migrate.py errander/main.py tests/config/test_migrate.py \
  tests/test_main.py STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: migration helper -- --migrate-inventory CLI + migrate_inventory()"
git push origin main
```

## v1-action-opt-in commit 1.1 (2026-05-17)

```bash
# Implemented ActionManifest, BUILTIN_ACTIONS, nested actions schema
# Updated example/inventory.yaml and tests/test_main.py to new nested format
uv run pytest                    # 1742 passed, 0 skipped
uv run ruff check . 2>&1 | grep "errander/config/schema"  # clean
uv run mypy . 2>&1 | tail -3     # 398 errors (all pre-existing, baseline was 425)
git add errander/models/manifest.py errander/agent/subgraphs/__init__.py \
  errander/agent/subgraphs/patching.py errander/agent/subgraphs/disk_cleanup.py \
  errander/agent/subgraphs/log_rotation.py errander/agent/subgraphs/docker_prune.py \
  errander/agent/subgraphs/backup_verify.py errander/config/schema.py \
  errander/agent/graph.py errander/main.py example/inventory.yaml \
  tests/test_main.py tests/models/test_manifest.py tests/agent/subgraphs/test_registry.py \
  tests/config/test_schema_actions.py STATUS.md docs/command-log.md tasks/todo.md tasks/lessons.md
git commit -m "feat: manifest model, per-action registry, nested actions schema"
git push origin main
```



## Glossary UI in production metrics server (2026-05-17)

```bash
# Verified route 404 → found routes are under /ui/ in metrics.py, not server.py
uv run python -c "from errander.observability.metrics import start_metrics_server; from errander.web.server import page_glossary; print('imports OK')"
# Added _ui_glossary handler, GLOSS_CSS export, /ui/glossary route, Glossary sidebar nav link
uv run python -c "import asyncio; from errander.safety.audit import AuditStore; from errander.observability.metrics import start_metrics_server; ..."  # started server on 9091
curl -s -o /dev/null -w "%{http_code}" http://localhost:9091/ui/glossary  # 200
# Verified in browser: 29-term glossary grid + animated workflow diagram + Plan Enrichment modal
git add errander/observability/metrics.py errander/web/server.py STATUS.md docs/command-log.md tasks/todo.md
git commit -m "feat: /ui/glossary in metrics server -- Glossary nav link + GLOSS_CSS injection"
git push origin main
```

## Per-env Prometheus/ELK URL overrides (2026-05-17)

```bash
uv run pytest tests/config/test_env_url_overrides.py -v   # 14 passed
uv run pytest --tb=short -q                                 # 1707 passed, 0 skipped
uv run ruff check errander/                                 # All checks passed
uv run mypy errander/                                       # 77 source files, no issues
git add errander/config/schema.py errander/main.py tests/config/test_env_url_overrides.py example/inventory.yaml scripts/configure.sh SETUP.md STATUS.md tasks/todo.md
git commit -m "feat: per-env Prometheus/ELK URL overrides -- env-level wins over global settings"
git push origin main
```

## P0-1 — Immutable Signed Plan Artifact (2026-05-16)

```bash
# Baseline
uv run pytest --tb=no -q   # 1452 passed, 111 skipped

# Commit 1: enrich_plan_node
uv run pytest tests/agent/test_enrich_plan.py -v   # 15 passed
uv run ruff check errander/agent/graph.py errander/agent/subgraphs/patching.py
uv run mypy errander/agent/graph.py errander/agent/subgraphs/patching.py
uv run pytest tests/agent/test_load.py::TestFleetBatchGraph::test_wave_abort_stops_fleet_at_boundary -v  # fixed regression

# Commit 2: approval message
uv run pytest tests/agent/test_approval_message_p01.py tests/agent/test_plan_apply_flow.py -v
uv run ruff check errander/agent/graph.py
uv run mypy errander/agent/graph.py
uv run pytest --tb=short -q   # 1480 passed

# Commits
git add errander/agent/graph.py errander/agent/subgraphs/patching.py tests/agent/test_enrich_plan.py tests/agent/test_load.py
git commit -m "feat: enrich_plan_node -- assessment at plan time, exact packages in hash"
git add errander/agent/graph.py docs/SPEC.md tests/agent/test_approval_message_p01.py
git commit -m "feat: P0-1 approval message -- exact packages per action, remove categories disclaimer"
```
**What**: P0-1 -- immutable signed plan artifact. `enrich_plan_node` runs SSH assessment at plan time (before hash), `_format_plan_for_approval` renders exact packages in Slack message.
**Why**: The "You are approving action categories" disclaimer was honest but weak. Operators now approve exact packages/versions, cryptographically committed by the plan hash.

## Phase C — Prometheus HTTP Adapter (2026-05-16)

```bash
# Baseline
uv run pytest --tb=no -q   # 1430 passed, 111 skipped

# Commit 1: PrometheusClient + model fields
uv run pytest tests/integrations/test_prometheus.py -v   # 10 passed
uv run ruff check errander/integrations/prometheus.py errander/config/settings.py
uv run mypy errander/integrations/prometheus.py errander/config/settings.py

# Commit 2: wiring
uv run pytest tests/agent/test_probe_prometheus.py tests/agent/test_operator_assistant_prometheus.py -v   # 12 passed
uv run ruff check errander/
uv run mypy errander/
uv run pytest --tb=short -q   # 1452 passed

# Commits
git add errander/integrations/prometheus.py errander/config/settings.py errander/models/analysis.py errander/models/reports.py tests/integrations/test_prometheus.py
git commit -m "feat: PrometheusClient adapter -- instant query, fetch_vm_metrics, best-effort"
git add errander/agent/probe.py errander/agent/operator_assistant.py errander/observability/reporting.py errander/main.py example/settings.yaml tests/agent/test_probe_prometheus.py tests/agent/test_operator_assistant_prometheus.py
git commit -m "feat: wire PrometheusClient into probe digest and --ask context"
```
**What**: Phase C -- thin Prometheus HTTP adapter enriching probe digests and --ask context with CPU/memory/load metrics.
**Why**: Phase B probe and Phase D --ask had no live time-series data; Prometheus fills the gap when deployed.

## Phase D — Operator Assistant Layer MVP (2026-05-15)

```bash
# Baseline before starting
uv run pytest --tb=no -q   # 1404 passed, 111 skipped

# Commit 1: core OperatorAssistant + models
uv run pytest tests/agent/test_operator_assistant.py -v   # 16 passed
uv run ruff check errander/agent/operator_assistant.py errander/models/analysis.py
uv run mypy errander/agent/operator_assistant.py errander/models/analysis.py

# Commit 2: CLI wiring
uv run pytest tests/test_main_ask.py -v   # 10 passed
uv run ruff check errander/main.py
uv run mypy errander/main.py

# Full suite after each commit
uv run ruff check errander/   # All checks passed
uv run mypy errander/         # 75 source files, no issues
uv run pytest --tb=short -q   # 1420 after Commit 1, 1430 after Commit 2

# Commits
git add errander/agent/operator_assistant.py errander/models/analysis.py tests/agent/test_operator_assistant.py
git commit -m "feat: OperatorAssistant Layer A -- context builder, LLM synthesis, deterministic fallback"
git add errander/main.py tests/test_main_ask.py
git commit -m "feat: --ask CLI -- Operator Assistant investigation from command line"
```
**What**: Phase D MVP -- Layer A Operator Assistant. `--ask "question"` CLI queries existing stores and calls LLM to synthesize fleet health findings and recommendations.
**Why**: Completes the two-layer architecture: Layer B executes, Layer A investigates and recommends.

## Phase B — Proactive Signals MVP (2026-05-15)

```bash
# Baseline before starting
uv run pytest --tb=no -q   # 1378 passed, 111 skipped

# Commit 1: core probe infrastructure
uv run pytest tests/agent/test_probe.py tests/observability/test_digest_reporting.py -v
uv run ruff check errander/agent/probe.py errander/models/reports.py errander/observability/reporting.py
uv run mypy errander/agent/probe.py errander/models/reports.py errander/observability/reporting.py

# Commit 2: scheduling + CLI + Slack
uv run pytest tests/test_main_probe.py -v
uv run ruff check errander/config/schema.py errander/integrations/slack.py errander/main.py
uv run mypy errander/config/schema.py errander/integrations/slack.py errander/main.py

# Full suite after each commit
uv run pytest --tb=short -q   # 1394 after Commit 1, 1403 after Commit 2
uv run ruff check errander/
uv run mypy errander/

# Commits
git add errander/agent/probe.py errander/models/reports.py errander/models/events.py errander/observability/reporting.py tests/agent/test_probe.py tests/observability/test_digest_reporting.py
git commit -m "feat: proactive signals core -- probe runner, DigestReport, render_digest_report"
git add errander/config/schema.py errander/integrations/slack.py errander/main.py example/settings.yaml tests/test_main_probe.py
git commit -m "feat: daily probe scheduling -- signals_cron config, --probe-now CLI, Slack digest posting"
```
**What**: Phase B MVP — standalone daily probe that runs independently of maintenance batches.
**Why**: Operators need daily visibility into fleet health (disk, drift, logins) without waiting for maintenance windows.

## Phase B fix — probe_vm discover_node (2026-05-15)

```bash
uv run pytest tests/agent/test_probe.py -v   # 9 passed (includes new test_probe_vm_returns_unreachable_when_discover_fails)
uv run pytest --tb=short -q                  # 1404 passed
uv run ruff check errander/agent/probe.py errander/main.py
uv run mypy errander/agent/probe.py errander/main.py
git add errander/agent/probe.py errander/main.py tests/agent/test_probe.py
git commit -m "fix: probe_vm calls discover_node first -- SSH pre-check + vm_info before signal nodes"
```
**What**: Added `discover_node` call at the start of `probe_vm()`, mirroring the vm_graph ordering.
**Why**: Without discover, signal nodes used inventory fallback values instead of runtime-detected VM state, and SSH failures weren't caught early.

## Phase A — Privilege Model Fixes (2026-05-15)

```bash
# Pre-flight checks
uv run pytest --tb=no -q   # 1343 passed, 111 skipped (baseline)
git status                 # clean main branch

# After each commit
uv run pytest --tb=short -q

# Acceptance checks
grep -r "/usr/bin/env" errander/safety/ errander/execution/
grep "PREFLIGHT_LOCK_DETECTED" errander/agent/vm_graph.py

# Opportunistic ruff auto-fix on touched files
uv run ruff check --fix errander/safety/rollback.py errander/execution/commands.py errander/agent/vm_graph.py errander/models/events.py
uv run ruff check --fix errander/config/schema.py errander/agent/subgraphs/docker_prune.py errander/agent/graph.py errander/execution/privilege.py
uv run ruff check --fix errander/execution/target_validation.py errander/main.py

# Commits
git add <files> && git commit -m "fix: close fifth-pass SRE residuals — env removal, simulate sudo, preflight event type"
git add <files> && git commit -m "feat: docker_command_mode (wrapper/direct_sudo/disabled) per environment"
git add <files> && git commit -m "feat: --check-targets CLI for pre-flight VM readiness validation"
```
**What**: Phase A — three commits closing SRE fifth-pass audit residuals + new Docker wrapper mode + pre-flight CLI.
**Why**: Privilege hygiene, production-hardened Docker wrapper default, operator tooling before maintenance windows.

## AI SRE Audit v2 Remediation (2026-05-14)

```bash
# Full test suite after each fix iteration
uv run pytest tests -q -p no:cacheprovider --basetemp=.pytest-tmp -x   # caught failing tests
uv run pytest tests -q -p no:cacheprovider --basetemp=.pytest-tmp       # 1305 passed, 111 skipped

# Ruff check on changed files (pre-existing errors only, no new violations)
uv run ruff check errander/agent/subgraphs/docker_prune.py errander/agent/subgraphs/disk_cleanup.py ...
```

## SRE Production Wiring Fix (2026-05-14)

```bash
# Run new wiring tests (10 tests)
uv run pytest tests/agent/test_sre_wiring.py -v   # 10 passed

# Full suite after all wiring changes
uv run pytest --tb=short -q   # 1303 passed, 111 skipped
```

## SRE Auditor Second Pass — Non-Blocking Items (2026-05-14)

```bash
uv run pytest --basetemp=.pytest-tmp -q   # 1303 passed, 111 skipped

git add errander/observability/metrics.py tests/ui/test_inventory_playwright.py
git commit -m "fix: URL-quote path segments in UI links, fix stale Playwright inventory test for new YAML fleet view"
git push origin main
```
**What**: (1) Added `_uq = urllib.parse.quote(safe="")` to `metrics.py`; applied to every URL path segment in batch/VM/approval links and form actions for defense in depth. (2) Updated `test_inventory_playwright.py` — `_start_server` now accepts `base_inventory`, seeded fixture passes `_YAML_FLEET` VMTargets so yaml_override rows render, stale empty-state text assertion updated.
**Why**: Auditor's second-pass non-blocking items: URL-quoting and stale Playwright test.

## Inventory UI — Full YAML Fleet (2026-05-14)

```bash
uv run pytest tests/observability/ tests/ui/ tests/test_main.py --basetemp=.pytest-tmp -q   # 104 passed
uv run pytest --basetemp=.pytest-tmp -q   # 1303 passed, 111 skipped

git add errander/observability/metrics.py errander/main.py
git commit -m "feat: inventory UI shows full YAML fleet merged with DB overrides — pass base_inventory to start_metrics_server"
git push origin main
```
**What**: `_ui_inventory_get` now reads `_BASE_INVENTORY_KEY` from the app to build the full fleet view. YAML VMs appear as the base with their DB-override disabled state; ad-hoc DB VMs are appended. `start_metrics_server` gains a `base_inventory` param; `main.py` loads the flat list via `load_inventory()` and passes it.
**Why**: Auditor's last open finding — inventory page only showed DB overrides, not the YAML fleet.

## SRE UI Revalidation — 3 Remaining Issues (2026-05-14)

```bash
# Verify remaining unescaped interpolations in metrics.py
grep -n "batch_id\|vm_id\|{title}" errander/observability/metrics.py | grep -v "_esc"

# Run affected test suites
uv run pytest tests/observability/ tests/ui/ tests/test_main.py --basetemp=.pytest-tmp -q   # 104 passed

# Full suite
uv run pytest --basetemp=.pytest-tmp -q   # 1303 passed, 111 skipped

git add errander/observability/metrics.py errander/main.py
git commit -m "fix: SRE UI revalidation — escape title/batch_id/vm_id, load DB overrides before building components"
git push origin main
```
**What**: Fixed 3 remaining issues from SRE revalidation: (1) Raw `title` in `_page()` `<title>` and `.tb-title` — now escaped; (2) Raw `batch_id`/`vm_id` in dashboard, batches, and approvals pages — all escaped; (3) `OverridesStore` initialized before `_build_components()` so DB-persisted LLM settings actually apply on restart.
**Why**: Auditor revalidated previous fixes and flagged these as still open.

## SRE UI Audit Remediation (2026-05-14)

```bash
# Locate all middleware definitions, CSRF helpers, and XSS-prone interpolations
grep -n "@web.middleware\|def _csrf_middleware\|def _inject_csrf\|html\.escape" errander/observability/metrics.py

# Run UI + observability tests after each fix
uv run pytest tests/ui/ tests/observability/ --basetemp=.pytest-tmp -q --tb=short

# Full type-check (15 pre-existing errors → 12 after fixes)
uv run mypy errander/observability/metrics.py --no-error-summary

# Full suite (1303 passed, 111 skipped)
uv run pytest --basetemp=.pytest-tmp -q

git add errander/observability/metrics.py
git commit -m "fix: SRE UI audit — CSRF decorator, CSRF injection wiring, XSS escaping, test-llm GET→POST, OS family validation"
git push origin main
```
**What**: Remediated all 7 findings from `ai_sre_ui_audit.md`: missing `@web.middleware` on CSRF middleware (→ 500 on all POSTs), `_inject_csrf` discarding modified HTML (→ forms rendered without CSRF tokens), no `html.escape` on DB/URL values (XSS), test-llm accepting API key via GET (secret leakage), `_VALID_OS_FAMILIES` containing unsupported OS types, settings page not warning about restart requirement.
**Why**: SRE audit flagged production `/ui/*` server in `metrics.py` as not production-ready.

## UI Nav Bug Fix (2026-05-13)

```bash
# Audit all routes + CSS classes used in new pages
grep -n "def handle_\|def page_\|NAV_ITEMS\|create_app\|add_route\|router.add" errander/web/server.py

# Check for missing CSS class definitions
for cls in inv-kpi filter-bar search-input ... admin-card ...; do grep -c ".${cls}" server.py; done

# Verify every route returns 200
for p in / /batches /approvals /audit /inventory /settings /admin /glossary; do
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8099$p; done

# Confirm exactly one active nav item per page
curl -s http://localhost:8099/batches | grep -o 'class="nav-item active">[^<]*'
# Before fix → "Active Batch" AND "Batch History" both highlighted
# After fix  → "Batch History" only

git add errander/web/server.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "fix: remove duplicate nav active highlight — drop Active Batch nav item, remove dead sidebar() and _sidebar_nav()"
git push origin main
```
**What**: Found and fixed two bugs in the Operations Hub UI: (1) "Active Batch" and "Batch History" both mapped to `/batches` causing dual active highlighting; (2) `sidebar()` and `_sidebar_nav()` were dead functions never invoked by `layout()`. Verified all 8 routes return 200 with exactly one active nav item.
**Why**: Team reported UI issues during testing.

## Plan Gap Closure Round 2 (2026-05-14)

```bash
# Run scheduled_jobs tests (20 tests — includes 6 new systemd timer tests)
uv run pytest tests/safety/drift_checks/test_scheduled_jobs.py -v   # 20 passed

# Full suite
uv run pytest --tb=short -q   # 1293 passed, 111 skipped
```

## PR-2 Gap Closure (2026-05-14)

```bash
# Run listening ports tests (17 tests — includes 4 new PID-stripping tests)
uv run pytest tests/safety/drift_checks/test_listening_ports.py -v   # 17 passed

# Full suite — verify no regressions
uv run pytest --tb=short -q   # 1287 passed, 111 skipped

# Type-check changed files
uv run mypy errander/safety/drift_checks/listening_ports.py errander/config/schema.py errander/agent/vm_graph.py errander/agent/graph.py errander/main.py
```

## SRE Monitoring — PR-2 Signal Aggregation + BatchReport Rendering (2026-05-13)

```bash
# Run new reporting tests (47 tests)
uv run pytest tests/observability/test_reporting.py -v --tb=short   # 47 passed

# Full suite
uv run pytest --tb=short -q   # 1283 passed, 111 skipped

# ruff on changed files (pre-existing warnings only, no new errors)
uv run ruff check errander/agent/graph.py errander/observability/reporting.py errander/agent/vm_graph.py

# mypy on graph.py (6 pre-existing errors, 0 new)
uv run mypy errander/agent/graph.py
```

## SRE Monitoring — PR-1.5 Drift Detection + Failed Logins (2026-05-13)

```bash
# Run PR-1.5 new tests (97 tests)
uv run pytest tests/safety/drift_checks/ tests/execution/test_failed_logins.py tests/agent/test_vm_graph_drift.py -q

# Full suite
uv run pytest --tb=short -q   # 1245 passed, 111 skipped

# mypy on new source files (all clean)
uv run mypy errander/safety/drift_checks/ errander/execution/failed_logins.py

# ruff on PR-1.5 files (all clean after E501+F401 fixes)
uv run ruff check errander/safety/drift_checks/ errander/execution/failed_logins.py tests/safety/drift_checks/ tests/execution/test_failed_logins.py tests/agent/test_vm_graph_drift.py
```

## SRE Monitoring — PR-1.4 Disk Growth Trend (2026-05-13)

```bash
# Run PR-1.4 unit tests
uv run pytest tests/execution/test_disk_trend.py -q   # 24 passed

# Full suite
uv run pytest --tb=short -q   # 1148 passed, 111 skipped

# mypy on new source file
uv run mypy errander/execution/disk_trend.py   # clean

# ruff on PR-1.4 new files (after fixing E501 on lines 161, 253, and vm_graph.py:1008)
uv run ruff check errander/execution/disk_trend.py tests/execution/test_disk_trend.py   # All checks passed
```

## SRE Monitoring — PR-1.3 Service Health Checks (2026-05-13)

```bash
# Run PR-1.3 tests
uv run pytest tests/execution/test_service_check.py tests/agent/subgraphs/test_patching.py -x --tb=short -q

# Full suite
uv run pytest --tb=short -q   # 1124 passed, 111 skipped

# mypy on new source file
uv run mypy errander/execution/service_check.py   # clean

# ruff on PR-1.3 files; auto-fix import sort
uv run ruff check --select I001 --fix tests/execution/test_service_check.py tests/agent/subgraphs/test_patching.py
```

## SRE Monitoring — PR-1.2 Reboot-Required Detection (2026-05-13)

```bash
# Full suite after writing all PR-1.2 tests
uv run pytest --tb=short -q   # 1077 passed, 111 skipped

# mypy on new PR-1.2 source files
uv run mypy errander/execution/reboot_check.py errander/observability/reporting.py tests/execution/test_reboot_check.py tests/observability/test_reporting.py

# ruff on PR-1.2 files (new files clean; 3 pre-existing issues in patching.py unchanged)
uv run ruff check errander/execution/reboot_check.py errander/observability/reporting.py errander/agent/subgraphs/patching.py tests/execution/test_reboot_check.py tests/agent/subgraphs/test_patching.py tests/observability/test_reporting.py

# ruff --fix import sort in test_patching.py (reboot_check_node alphabetical position)
uv run ruff check --select I001 --fix tests/agent/subgraphs/test_patching.py

# PR-1.2 targeted test run
uv run pytest tests/execution/test_reboot_check.py tests/agent/subgraphs/test_patching.py tests/observability/test_reporting.py -v --tb=short
```

## SRE Monitoring — PR-1.1 Package Lock Detection (2026-05-13)

```bash
# Run affected test files
uv run pytest tests/execution/test_commands.py tests/safety/test_validators.py tests/agent/subgraphs/test_patching.py -x --tb=short -q

# mypy on new source files
uv run mypy errander/execution/commands.py errander/safety/validators.py errander/agent/subgraphs/patching.py

# ruff on all PR-1.1 files
uv run ruff check errander/execution/commands.py errander/safety/validators.py errander/agent/subgraphs/patching.py tests/execution/test_commands.py tests/safety/test_validators.py tests/agent/subgraphs/test_patching.py

# Full suite
uv run pytest --tb=short -q   # 1031 passed, 111 skipped
```

## SRE Monitoring — PR-G Groundwork (2026-05-13)

```bash
# Verify mypy clean on all 12 PR-G files
uv run mypy errander/safety/migrations.py errander/safety/vm_state.py errander/safety/baselines.py errander/safety/disk_history.py errander/safety/audit.py errander/models/reports.py errander/models/actions.py errander/models/events.py errander/models/vm.py errander/config/schema.py errander/config/settings.py errander/config/inventory.py

# Verify ruff clean on all new files
uv run ruff check errander/safety/migrations.py errander/safety/vm_state.py errander/safety/baselines.py errander/safety/disk_history.py errander/models/reports.py tests/safety/test_migrations.py tests/safety/test_vm_state.py tests/safety/test_baselines.py tests/safety/test_disk_history.py tests/models/test_reports.py

# Full test suite
uv run pytest --tb=short -q   # 996 passed, 111 skipped
```

## Web UI — Operations Hub Pages (2026-05-13)

```bash
# Kill stale server on port 8099 (PowerShell)
Get-NetTCPConnection -LocalPort 8099 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# Start dev server in background (Git Bash)
uv run python -m errander.web.server &

# Verify all new routes respond
curl -s -o /tmp/inv.html http://localhost:8099/inventory   # inventory ok
curl -s -o /tmp/set.html http://localhost:8099/settings    # settings ok
curl -s -o /tmp/adm.html http://localhost:8099/admin       # admin ok

# Spot-check rendered content
grep -o "Total VMs\|LLM Configuration\|Agent Controls\|Admin Panel" inv.html set.html adm.html

git add errander/web/server.py STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: add Glossary, Inventory, Settings, and Admin pages to Operations Hub UI"
git push origin main
```
**What**: Built four full UI pages in the Operations Hub (`errander/web/server.py`): Glossary (animated LangGraph DAG + 18-term glossary + node-click modal), Inventory (VM fleet table with KPIs and filters), Settings (read-only config display), Admin (agent controls, health checks, lock manager, override toggles, danger zone). Wired route handlers and registered all routes.
**Why**: Placeholder routes for /inventory and /settings replaced with real pages; /admin and /glossary are new. All four accessible from the sidebar nav.

Developer reference for every command used in building this project.

## Project Setup

### 2026-03-21 — Initial Scaffolding

```bash
mkdir -p errander/agent/subgraphs errander/safety errander/execution errander/integrations errander/observability errander/config errander/models errander/scheduling tests/agent/subgraphs tests/safety tests/execution tests/integrations tests/observability tests/config tests/models tests/scheduling tasks
```
**What**: Created the full directory tree for Option C architecture (parent orchestrator + fan-out + sub-graphs).
**Why**: Scaffolding all modules upfront so every file has a home from day one.

```bash
ls "C:/PS/AI/Junior DevOps Engineer - Agent/"
```
**What**: Listed project root contents before scaffolding.
**Why**: Verified starting state — only CLAUDE.md and docs/ existed.

```bash
find errander tests tasks -type f -name "*.py" -o -name "*.md" -o -name "*.toml" | sort
```
**What**: Listed all scaffolded files after creation.
**Why**: Final verification that all 86 files were created in the correct locations.

## Dependencies

### 2026-03-21 — Initial Setup

```bash
where uv 2>/dev/null || where.exe uv 2>/dev/null || echo "uv not found"
```
**What**: Checked if `uv` package manager was installed.
**Why**: `uv` is the project's package manager (specified in CLAUDE.md). It wasn't found.

```bash
pip install uv
```
**What**: Installed `uv` (v0.10.12) via pip.
**Why**: Needed as the project's package manager. Installed globally since it's a CLI tool.
**Result**: `Successfully installed uv-0.10.12`

```bash
python -m uv sync
```
**What**: Installed all project dependencies from pyproject.toml into .venv.
**Why**: First run — creates the virtualenv and installs all runtime dependencies.
**Note**: Multiple parallel runs were triggered (bj0shnnzk, bbmwd6rmi, byyuvn4cf) due to timeout issues with background tasks. All completed successfully.
**Result**: Resolved 68 packages, created `.venv/` with Python 3.12.10.

```bash
ls .venv/Scripts/python.exe
```
**What**: Checked if the virtualenv was successfully created.
**Why**: After timeout issues with `uv sync`, needed to verify the venv existed before proceeding.
**Result**: File found — venv was created successfully.

```bash
python -m uv sync --group dev
```
**What**: Attempted to install dev dependencies using `--group`.
**Why**: Needed pytest, ruff, mypy for development.
**Result**: **FAILED** — `error: Group 'dev' is not defined in the project's 'dependency-groups' table`
**Fix**: Used `--extra dev` instead (pyproject.toml uses `[project.optional-dependencies]`, not `[dependency-groups]`).

```bash
python -m uv sync --extra dev
```
**What**: Installed dev dependencies (pytest, ruff, mypy, etc.).
**Why**: Needed for running tests and linting.
**Result**: Successfully installed pytest 9.0.2, pytest-asyncio 1.3.0, ruff 0.15.7, mypy 1.19.1, and related packages.

## Git

*(No git commands run yet — scaffolding not committed.)*

## Testing

### 2026-03-21 — Import Verification + First Test Run

```bash
.venv/Scripts/python.exe -c "import errander; print('errander OK')"
.venv/Scripts/python.exe -c "from errander.models.vm import VMTarget, OSFamily; print('models OK')"
.venv/Scripts/python.exe -c "from errander.agent.state import BatchState, VMMaintenanceState; print('state OK')"
.venv/Scripts/python.exe -c "from errander.execution.commands import get_package_manager; print('commands OK')"
.venv/Scripts/python.exe -c "from errander.config.policies import get_policy; print('policies OK')"
```
**What**: Verified all key modules import without errors.
**Why**: Ensuring the scaffold is importable before running tests.
**Result**: All 5 imports passed.

```bash
.venv/Scripts/python.exe -m pytest
```
**What**: Attempted to run pytest.
**Why**: First test run to verify scaffolding.
**Result**: **FAILED** — `No module named pytest` — dev dependencies weren't installed yet.
**Fix**: Ran `uv sync --extra dev` (see Dependencies section).

```bash
.venv/Scripts/python.exe -m pytest -v
```
**What**: Ran full test suite with verbose output.
**Why**: Verify all 40 tests pass after installing dev deps.
**Result**: **40 passed in 1.74s** — all placeholder tests + real assertion tests pass.
**Platform**: Python 3.12.10, pytest 9.0.2, plugins: anyio, langsmith, asyncio, cov.

### 2026-03-21 — Phase 1.2: Settings Loader

```bash
mkdir -p "C:/PS/AI/Junior DevOps Engineer - Agent/config"
```
**What**: Created `config/` directory for YAML configuration files.
**Why**: Needed a home for inventory.yaml, policies.yaml, settings.yaml.

```bash
uv run pytest tests/config/ -v
```
**What**: Ran all config tests (schema, settings, inventory, policies).
**Why**: Verify settings loader implementation — 58 tests, all passing.

```bash
uv run pytest -v
```
**What**: Full test suite run.
**Why**: Ensure no regressions — 91 tests passing.

```bash
uv sync --extra dev
```
**What**: Re-synced dependencies after adding `aiosqlite>=0.20` to pyproject.toml.
**Why**: Audit logging requires async SQLite access.
**Result**: Installed `aiosqlite==0.22.1`.

```bash
uv run pytest tests/safety/test_audit.py -v
```
**What**: Ran audit store tests.
**Why**: Verify SQLite audit logging — 20 tests, all passing.

```bash
uv run pytest tests/execution/ -v
```
**What**: Ran SSH + OS detection + sandbox tests.
**Why**: Verify SSH connection manager, OS detection parsing, dry-run wrapper — 44 tests passing.

```bash
uv run pytest tests/safety/test_locking.py -v
```
**What**: Ran file locking tests.
**Why**: Verify FileLocker TTL, stale detection, ownership — 22 tests passing.

```bash
uv run pytest -v
```
**What**: Full test suite.
**Why**: Final regression check — 179 tests, all passing.

### 2026-03-23 — Phase 1.3: Disk Cleanup Sub-Graph

```bash
uv run pytest tests/agent/subgraphs/test_disk_cleanup.py -v
```
**What**: Ran disk cleanup sub-graph tests.
**Why**: Verify LangGraph sub-graph implementation — 31 tests, all passing.
**Issues**: 2 failures on first run:
  1. Lambda wrapping async functions caused `InvalidUpdateError: Expected dict, got <coroutine>` — fixed by using `async def` wrappers.
  2. Mock at wrong level — SandboxExecutor dry-run mode adds `[DRY-RUN]` prefix, need to mock at executor level not SSH level.

```bash
uv run pytest -v
```
**What**: Full test suite.
**Why**: Regression check — 209 tests, all passing.

### 2026-04-03 — Phase 1.4 + 1.5: Per-VM Graph + Batch Orchestrator

```bash
uv run pytest tests/agent/test_decisions.py -v
```
**What**: Decisions module tests (23 tests).
**Why**: Verify hardcoded action prioritization + template report generation.
**Result**: 23 passed.

```bash
uv run pytest tests/agent/test_vm_graph.py -v
```
**What**: Per-VM graph tests (28 tests).
**Why**: Verify lock/discover/plan/dispatch/audit/unlock lifecycle.
**Issues**: None on first run — patterns from disk_cleanup subgraph were followed correctly.
**Result**: 28 passed.

```bash
uv run pytest tests/agent/test_graph.py -v
```
**What**: Batch orchestrator tests (21 tests).
**Why**: Verify init/window/validate/fan-out/collect/report flow.
**Issues**: `fan_out` node returning `list[Send]` caused `InvalidUpdateError: Expected dict, got [Send(...)]`. Fixed by moving Send() emission to a conditional edge routing function (`make_fan_out_router`), not a node. LangGraph nodes must return dicts; Send() objects must come from conditional edge functions.
**Result**: 21 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression test suite.
**Why**: Ensure 278 tests (all phases) pass with no regressions.
**Result**: 278 passed in 7.56s.

## vLLM / LLM

### 2026-04-03 — Phase 1.6: LLM Client

```bash
uv run pytest tests/integrations/test_llm.py -v
```
**What**: LLM client tests (23 tests).
**Why**: Verify complete(), health_check(), retry logic, fallback behavior, decisions.py integration.
**Issues**: First run took 57s — each test was creating a new `AsyncOpenAI` client with httpx transport initialization (~1.4s each). Fixed by using module-scoped pytest fixtures for shared clients. Reduced to 6.5s.
**Result**: 23 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 300 tests pass with LLM client added.
**Result**: 300 passed in 13.36s.

## Slack Integration

### 2026-04-03 — Phase 1.6: Slack Client + Approval Gate

```bash
uv run pytest tests/integrations/test_slack.py -v
```
**What**: Slack client tests (10 tests).
**Why**: Verify post_message, get_reactions, post_alert, rate limiting retry.
**Issues**:
  1. `session.post` is an async context manager (`async with session.post() as resp:`), not awaitable (`await session.post()`). Test mocks needed `_ctx()` wrapper returning an async CM. Fixed by switching implementation to `async with ctx as resp:` pattern.
  2. `get_reactions` called `_call(..., method="GET")` — but `_call`'s first param is the Slack API method name (e.g. `"reactions.get"`), not the HTTP method. Fixed by using `http_method="GET"`.
  3. Rate-limit retry used `break` instead of `continue`, which exited the loop instead of retrying. Fixed.
**Result**: 10 passed in 0.12s.

```bash
uv run pytest tests/safety/test_approval.py -v
```
**What**: Approval gate tests (21 tests).
**Why**: Verify request_approval message formatting and poll_approval reaction logic (approve, reject, priority, timeout, error recovery, delayed approval).
**Result**: 21 passed in 0.09s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 321 tests pass with Slack + approval added.
**Result**: 321 passed in 13.8s.

### 2026-04-03 — Phase 1.6: Prometheus Metrics

```bash
uv run pytest tests/observability/test_metrics.py -v
```
**What**: Metrics tests (20 tests).
**Why**: Verify metric registration, counter/histogram tracking, /metrics and /health handlers, server startup.
**Issues**: `web.Response(body=..., content_type=..., headers={"Content-Type": ...})` raises ValueError — aiohttp forbids passing both `content_type` param and `Content-Type` header. Fixed by removing the `content_type` kwarg and relying on the header alone.
**Result**: 20 passed in 1.02s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 338 tests pass with metrics added.
**Result**: 338 passed in 15.48s.

### 2026-04-03 — Pre-Phase 1.8: Window Wiring + main.py

```bash
uv run pytest tests/agent/test_graph.py::TestValidateWindowNode -v
```
**What**: Window node tests (6 tests including new window enforcement tests).
**Why**: Verify validate_window_node correctly blocks outside-window batches, passes with force=True, and is properly wired in build_batch_graph.
**Issues**: `SandboxExecutor(dry_run=True)` missing required `ssh_manager` arg — used `_make_executor()` helper instead.
**Result**: 6 passed.

```bash
uv run pytest tests/test_main.py -v
```
**What**: main.py tests (17 tests).
**Why**: Verify CLI arg parsing, _build_maintenance_window, and async_main error paths.
**Issues**:
  1. `FileLocker` doesn't accept `ttl_seconds` kwarg — fixed to `FileLocker(lock_dir=...)` only.
  2. `_make_env(days=[])` — test helper used `days or [...]` which treated empty list as falsy. Fixed to `days if days is not None else [...]`.
**Result**: 17 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 394 tests pass after wiring + main.py.
**Result**: 394 passed in 6.57s.

### 2026-04-03 — Phase 1.7: Scheduling + Windows

```bash
uv run pytest tests/scheduling/ -v
```
**What**: Scheduling tests (windows + scheduler, 36 tests).
**Why**: Verify maintenance window enforcement (normal, overnight, timezone) and APScheduler wrapper lifecycle.
**Issues**:
  1. `test_timezone_conversion_outside` — Europe/Paris in April is CEST (UTC+2), not CET (UTC+1). Test comment was wrong; fixed the UTC time to 01:00 (→ 03:00 CEST, outside [04:00, 06:00)).
  2. APScheduler 3.x `job.next_run_time` is a `__slots__` attribute — not initialized on pending jobs (scheduler not started). Fixed with `getattr(job, "next_run_time", None)`.
  3. `replace_existing=True` only deduplicates against jobstores, not the pending list. Removed that test (APScheduler internal, not our logic).
**Result**: 36 passed in 0.16s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 373 tests pass with scheduling added.
**Result**: 373 passed in 5.98s.

### 2026-04-03 — vLLM Deployment + LLM Health Check

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Verify 415 tests still pass after adding check_endpoint() to LLMClient and --check-llm to main.py.
**Result**: 415 passed in 6.74s.

### 2026-04-03 — Web UI

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Verify 415 tests still pass after adding UI routes to metrics server.
**Result**: 415 passed in 7.85s, no warnings (AppKey fix applied).

### 2026-04-03 — SQLite Audit Integration

```bash
uv run pytest tests/safety/test_audit.py tests/safety/test_audit_integration.py -v
```
**What**: Ran audit store tests (existing 20) + new integration tests (21).
**Why**: Verify action_type filter, get_recent_batches, VM graph audit trail, and audit CLI mode.
**Result**: 41 passed in 2.03s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 415 tests pass with audit integration added.
**Result**: 415 passed in 5.80s.

### 2026-04-10 — Dual-Channel Approval + Approval UI

```bash
uv run pytest tests/safety/test_approval.py tests/ui/test_approval_ui.py -x -q
```
**Why**: Run new approval tests after implementing ApprovalManager, await_dual_approval, and UI routes.
**Result**: First run — 1 failing (test_ui_approval_wins_race). Global asyncio.sleep patch caused lambda recursion.

```bash
uv run pytest tests/safety/test_approval.py tests/ui/test_approval_ui.py -x -q
```
**Why**: Rerun after fixing tests to use asyncio.Event().wait() blocking instead of patched sleep.
**Result**: 50 passed in 1.02s.

```bash
uv run pytest --ignore=tests/ui/test_web_ui.py -q
```
**Why**: Full suite after changes to metrics.py, approval.py, and main.py.
**Result**: 454 passed in 7.23s.

```bash
uv run pytest tests/ui/test_web_ui.py -q
```
**Why**: Verify Playwright tests still pass after nav changes in _page().
**Result**: 25 passed in 53.57s.

## Phase 3 — Hardening (Rolling Updates, Canary, Drift Detection)

### 2026-04-18 — Rolling updates, canary logic, drift detection

```bash
uv run pytest tests/config/test_settings.py tests/safety/test_drift.py -x -q
```
**What**: Run settings + drift module tests after Step 1 (schema/settings) and Step 2 (drift.py).
**Why**: Verify foundation before building on it.
**Result**: 35 passed in 0.32s.

```bash
uv run pytest tests/agent/test_vm_graph.py -x -q
```
**What**: Run VM graph tests after Step 3 (drift_check_node integration).
**Why**: Catch routing regression — route_after_discover now returns "drift_check" not "plan_actions".
**Result**: 1 failure (existing test expected "plan_actions"). Fixed test. 39 passed.

```bash
uv run pytest tests/agent/test_graph.py -x -q
```
**What**: Run batch graph tests after Step 4 (rolling updates / new topology).
**Why**: Verify new wave-based graph topology didn't break existing tests.
**Result**: 25 passed in 1.41s.

```bash
uv run pytest tests/agent/test_rolling_updates.py tests/agent/test_canary.py -x -q
```
**What**: Run new rolling update and canary tests.
**Why**: Step 4 + Step 5 verification.
**Result**: 31 passed in 1.09s.

```bash
uv run pytest -x -q
```
**What**: Full test suite after all 6 implementation steps.
**Why**: No regressions — all 652 tests must pass.
**Result**: 652 passed in 56.54s.

```bash
uv run ruff check errander/safety/drift.py errander/agent/vm_graph.py errander/agent/graph.py errander/config/schema.py errander/config/settings.py errander/models/events.py errander/observability/metrics.py errander/main.py
```
**What**: Lint the modified files.
**Why**: Confirm no new lint errors introduced.
**Result**: All errors are pre-existing (TC001 type-checking imports, UP017 datetime.UTC alias, etc.) — none introduced by Phase 3 changes.

## Phase 3 — Edge Case Hardening (2026-04-19)

```bash
uv run pytest -q
```
**What**: Full test suite after all Phase 3 hardening implementation steps.
**Why**: Verify 677 tests pass (25 new tests added) with no regressions.
**Result**: 677 passed in ~61s.

```bash
uv run ruff check errander/safety/audit.py errander/agent/vm_graph.py errander/agent/graph.py errander/execution/ssh.py errander/config/schema.py errander/safety/locking.py
```
**What**: Lint check on all files modified during Phase 3 edge case hardening.
**Why**: Confirm no new lint violations introduced (only pre-existing UP017/TC001 violations remain).
**Result**: No new errors from Phase 3 changes. Fixed UP041 (asyncio.TimeoutError alias), F401 (unused timezone), E402 (logger placement) issues found during lint.

### 2026-04-19 — Load tests + Playwright approvals tests

```bash
uv run pytest tests/agent/test_load.py tests/ui/test_approvals_playwright.py -v
```
**What**: Run the two new test files in isolation to catch failures early.
**Why**: Verify 20 load tests and 22 Playwright approvals tests all pass before running full suite.
**Result**: 2 failures — `ActionStatus.COMPLETED` (wrong enum value, fixed to `ActionStatus.SUCCESS`) and `test_report_excerpt_shown` (report inside collapsed `<details>`, fixed by clicking to expand first).

```bash
uv run ruff check --fix tests/agent/test_load.py tests/ui/test_approvals_playwright.py
```
**What**: Auto-fix lint in new test files.
**Why**: Caught I001 (unsorted imports), UP017 (timezone.utc → UTC), F541 (f-string without placeholders).
**Result**: 10 auto-fixed; remaining 8 (TC003, E501, SIM117) fixed manually.

```bash
uv run pytest -q
```
**What**: Full test suite after load test + Playwright approvals additions.
**Why**: Verify 719 tests pass with no regressions.
**Result**: 719 passed in ~89s.

## Phase 4 — LLM Flexibility + Secrets + UI Config (2026-04-19)

```bash
uv run pytest tests/integrations/test_llm.py tests/integrations/test_secrets.py tests/observability/test_redaction.py tests/config/test_secrets_loading.py -v
```
**What**: Run Phase A + A.5 new tests in isolation.
**Why**: Verify rewritten LLM tests, 24 secrets tests, 9 redaction tests, and 6 secrets-loading tests all pass before touching the full suite.
**Result**: All passed.

```bash
uv run ruff check --fix errander/integrations/llm.py errander/integrations/secrets.py errander/observability/redaction.py errander/config/schema.py errander/config/settings.py errander/agent/decisions.py errander/main.py
```
**What**: Auto-fix lint on all Phase A/A.5 modified files.
**Why**: Caught I001 (import ordering — two `from openai import ...` lines merged), F401 (unused imports).
**Result**: Fixed automatically. Pre-existing UP047/B905/SIM105 errors left untouched.

```bash
uv run pytest -q
```
**What**: Full test suite after Phase A + A.5 implementation.
**Why**: Verify no regressions from LLM client and secrets changes.
**Result**: All tests passing.

```bash
uv run pytest tests/safety/test_overrides.py tests/config/test_settings_precedence.py tests/agent/test_inventory_merge.py -v
```
**What**: Run Phase B new tests in isolation.
**Why**: Verify 18 overrides tests, 21 settings-precedence tests, and 9 inventory-merge tests before running full suite.
**Result**: All 49 passed after fixing patch target (`errander.agent.graph.build_batch_graph` not `errander.main.build_batch_graph` — local import inside function body).

```bash
uv run ruff check --fix tests/safety/test_overrides.py tests/config/test_settings_precedence.py tests/agent/test_inventory_merge.py
```
**What**: Auto-fix lint on Phase B test files.
**Why**: Caught F401 (unused `os`, `patch`, `pytest`), I001 (unsorted imports), B017 (blind Exception).
**Result**: 8 auto-fixed; `TC003` suppressed with `# noqa`; `B017` fixed by catching `aiosqlite.IntegrityError`.

```bash
uv run pytest --tb=short -q
```
**What**: Full test suite after Phase B implementation.
**Why**: Verify 799 tests pass with no regressions.
**Result**: 799 passed in ~103s.

```bash
uv run ruff check errander/ tests/
```
**What**: Full project lint check after Phase 4.
**Why**: Confirm no new violations — only pre-existing TC001/UP017/etc. remain.
**Result**: Only pre-existing errors; all Phase 4 files clean.

## Phase 4 — Playwright Tests T4-T6 (2026-04-20)

```bash
uv run pytest tests/ui/test_settings_playwright.py tests/ui/test_inventory_playwright.py tests/ui/test_ui_auth_playwright.py -v --tb=short
```
**What**: Run the three new Phase 4 Playwright test files in isolation.
**Why**: Debug 4 remaining failures from previous session before running full suite.
**Result**: Initially 41/45 passing; root cause found — nested `<form>` in settings page.

```bash
uv run ruff check tests/ui/ --fix
```
**What**: Auto-fix lint on all UI Playwright test files.
**Why**: Caught I001 (import ordering), F841 (unused variables), E501 (long lines in test_web_ui.py).
**Result**: 9 auto-fixed; remaining manually corrected.

```bash
uv run pytest tests/ui/ -v --tb=short
```
**What**: Run all 111 UI Playwright tests after nested-form fix.
**Why**: Verify all settings, inventory, auth, and existing UI tests pass.
**Result**: 111 passed.

```bash
uv run pytest --tb=short -q
```
**What**: Full test suite after all Phase 4 Playwright fixes.
**Why**: Confirm 844 tests pass with no regressions.
**Result**: 844 passed in ~146s.

## Entry Point Fix (2026-04-20)

```bash
uv run python -m errander --help
```
**What**: Test `python -m errander` invocation.
**Why**: User tried to run the agent and hit `No module named errander.__main__`.
**Fix**: Created `errander/__main__.py` that calls `errander.main.main()`.

## Deferred Execution (2026-04-27)

```bash
uv run pytest tests/safety/test_deferred.py tests/scheduling/test_windows.py tests/agent/test_graph.py::TestApprovalGateDeferred tests/test_main.py::TestWindowOpener -v
```
**What**: Run only the new deferred execution tests (59 tests across 4 files).
**Why**: Verify all new tests pass before running the full suite.
**Result**: 59 passed.

```bash
uv run pytest
```
**What**: Full test suite after deferred execution feature.
**Why**: Confirm 878 tests pass with no regressions.
**Result**: 878 passed in ~304s.

## SSH / Target VMs

### 2026-05-09 — Phase 1.8 E2E Validation (Azure VMs)

```bash
# On Master VM — install uv (pip-based, before bootstrap script existed)
pip3 install uv
```
**What**: Installed uv via pip3 on Ubuntu 22.04 Master VM.
**Why**: python3.12 not in default Ubuntu 22.04 apt repos; uv can manage its own Python.

```bash
# On Master VM — install Python 3.12 via uv
uv python install 3.12
```
**What**: Downloaded and installed Python 3.12.13 into uv's managed Python store.
**Why**: Agent requires Python 3.12+; uv installer avoids deadsnakes PPA.

```bash
# Add uv to PATH for current session and permanently
export PATH="/root/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```
**What**: Fixed missing PATH entry after `uv python install` warning.
**Why**: uv installs to ~/.local/bin which is not in PATH by default on Ubuntu 22.04.

## Deployment

*(No deployment commands run yet.)*

## Setup Scripts

### 2026-05-10 — doc audit and fixes

```bash
git rev-parse --short HEAD   # checked current HEAD hash for validation checklist update
```
**What**: Verified current HEAD commit hash.
**Why**: tasks/phase-1.8-validation-checklist.md had a stale commit hash (8a7c65e) — updated to current HEAD.

### 2026-05-10 — scripts/configure.sh (interactive setup)

```bash
# End users run this after bootstrap.sh + LLM setup
bash scripts/configure.sh
```
**What**: Interactive script that prompts for LLM provider/credentials, target VMs, SSH key path, optional Slack, then writes `.env` + `inventory.yaml` and verifies the LLM connection.
**Why**: Steps 4–6 of SETUP.md required users to manually construct .env and inventory.yaml — the script eliminates that and makes the flow sequential and prompting.

```bash
# Verify LLM inline (no .env needed) — used inside configure.sh
ERRANDER_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1/ \
ERRANDER_LLM_MODEL=<deployment> \
ERRANDER_LLM_API_KEY=<key> \
uv run python -m errander --check-llm
```
**What**: Tests LLM connection using inline env vars rather than loading from .env.
**Why**: .env doesn't exist yet during Step 4 — inline vars verify credentials before Step 5 creates the file.

### 2026-05-10 — configure.sh UX fix (VM prompt order)

```bash
# Ran configure.sh interactively to observe the UX issue
bash scripts/configure.sh
```
**What**: Fixed prompt ordering in `[2/5] Target VMs` — "Do you want to add VMs?" now appears before the section header on fresh installs; the header only renders if user says yes.
**Why**: Showing `[2/5] Target VMs` before asking whether to add VMs implied the step was mandatory — misleading UX.

### 2026-05-10 — configure.sh UX fix (SSH key step header)

**What**: Suppressed `[3/5] SSH key pair` step header when key already exists — replaced with a single `✓` line.
**Why**: Announcing a step header then immediately saying "already done" contradicted itself.

### 2026-05-10 — configure.sh remove SSH key generation

**What**: Removed `ssh-keygen` call from configure.sh entirely. Script now only checks if the key exists and prints a reminder pointing to SETUP.md Step 2 if it doesn't. Banner updated from "Generate an SSH key pair" to "Verify your SSH key exists".
**Why**: SSH key setup is a manual Step 2 concern in SETUP.md — users should own it themselves. By the time configure.sh runs (Steps 4–6), the key should already exist.

### 2026-05-10 — scripts/bootstrap.ps1 (Windows bootstrap)

```powershell
# Clone first, then run bootstrap
git clone https://github.com/psc0des/Errander-AI.git errander
powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
```
**What**: Windows equivalent of bootstrap.sh — installs git (winget), uv (official PS installer), Python 3.12, runs uv sync, verifies import. No admin required.
**Why**: Windows Step 1 was manual; Linux had a one-liner. Now both platforms have identical one-liner experience.

## Bootstrap Script

### 2026-05-10 — private repo fix

```bash
# Correct invocation for private repo (curl one-liner returns 404)
git clone https://github.com/psc0des/Errander-AI.git errander
bash errander/scripts/bootstrap.sh
```
**What**: Replaced `curl | bash` one-liner with clone-first approach.
**Why**: `raw.githubusercontent.com` returns 404 for private repos without a token.

### 2026-05-09 — scripts/bootstrap.sh

```bash
# End users run this one-liner to bootstrap the Master VM
curl -LsSf https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/bootstrap.sh | bash
```
**What**: Single command that detects distro, installs git/curl/uv/Python 3.12, clones repo, runs uv sync, verifies import.
**Why**: Manual step-by-step approach in SETUP.md had implicit steps that tripped up real users (PATH export, Python 3.12 not in apt on Ubuntu 22.04).

### 2026-05-10 — configure.sh UX fix (keep/add VM prompts)

**What**: Split "Keep existing VMs and just add more? (Y/n)" into two separate prompts: "Keep these VMs? (Y/n)" and "Add more VMs? (y/N)".
**Why**: A single question covering two distinct decisions (keep vs. add) was ambiguous — Y implied both, N implied neither, which is misleading.

### 2026-05-10 — configure.sh UX fix (final summary cleanup)

**What**: Removed "Complete SETUP.md Steps 2-3 on each target VM" from the final summary. Replaced "Before running the agent:" block with a direct "Next — run a dry-run:" line.
**Why**: configure.sh covers Steps 4–6; Steps 2-3 must be done before reaching configure.sh. Reminding users to do prior steps in the completion summary is noise and implies they may not have been done.

### 2026-05-10 — configure.sh UX fix (final summary step order)

**What**: Final summary now lists Step 6 (verify inventory + `uv run pytest`) before Step 7 (dry-run), matching SETUP.md order exactly.
**Why**: Previous summary jumped straight to dry-run, skipping the verify step — inconsistent with SETUP.md and confusing for users following the guide.

### 2026-05-10 — dry-run --force fix (configure.sh + SETUP.md Step 7)

**What**: Added `--force --force-reason "initial dry-run validation"` to the suggested dry-run command in configure.sh summary and SETUP.md Step 7.
**Why**: First dry-run blocked by maintenance window on a weekend — `--force` bypasses the window so first-run validation always works.

### 2026-05-10 — configure.sh auto-wire encryption key

**What**: After generating the encryption key, configure.sh now automatically:
1. Exports `ERRANDER_SECRETS_KEY` into the current shell session (so LLM verify works immediately)
2. Appends `source ~/.errander.key` to `~/.bashrc` or `~/.zshrc` (idempotent — guarded by marker comment)
3. Injects `EnvironmentFile=~/.errander.key` into `/etc/systemd/system/errander.service` if it exists, then runs `daemon-reload`
**Why**: Previous version printed manual instructions users had to follow themselves — no one reads those. Wiring it automatically is the only reliable path.

### 2026-05-10 — configure.sh secrets hardening

**What**: Three security improvements to configure.sh:
1. `chmod 600 .env` applied on every write — was missing entirely
2. Optional Fernet encryption: generates key to `~/.errander.key` (chmod 600, separate file), encrypts LLM_API_KEY / UI_PASSWORD / SLACK_BOT_TOKEN as `enc:v1:` blobs in .env; re-run safe (existing `enc:v1:` values passed through unchanged)
3. Web UI username + password now prompted explicitly on fresh install with confirmation loop; re-run shows existing values as defaults; `changeme` can no longer silently reach production
**Why**: Open source project — default security posture must be production-safe out of the box.

### 2026-05-10 — configure.sh + SETUP.md 9-bug audit and fix

**What**: Fixed 9 bugs found in deep audit of configure.sh and SETUP.md:
- A: Fresh install Enter default on "Add VMs?" was treated as no — fixed with `_add_vms="${_add_vms:-y}"`
- B: "Keep existing + Add more" silently dropped new VMs — fixed by appending TARGETS_YAML when KEEP_INVENTORY=true
- C: Re-run always reset UI password to `changeme` — fixed by reading existing creds from .env before writing; added production warning
- D: SSH key missing message unclear — improved to say setup is incomplete and re-run required
- E: Step 7/8 hardcoded `--env dev` — changed to `--env <your-env-name>` with substitution note
- F: Azure Foundry URL was `openai.azure.com` — fixed to `cognitiveservices.azure.com`
- G: systemd service used `User=errander` (target VM user) — rewritten to use `$(whoami)` and `$(pwd)`
- H: Quick path said "SSH key" (implies generation) — updated to "verify your SSH key path"
- I: No warning to change default password — added callout in Web UI section and .env template
**Why**: Project is targeting open source release — quality bar must be high for first-time users.

### 2026-05-10 — fix approval gate skipping for dry-run batches

```bash
uv run python -m pytest tests/agent/test_graph.py -q  # 33 passed
```
**What**: `approval_gate_node` in `errander/agent/graph.py` now auto-approves immediately when `dry_run=True`, skipping the `await_dual_approval` call entirely.
**Why**: Dry-run executes nothing on target VMs — requiring human approval blocked the first validation run indefinitely with no way to proceed without a web UI or Slack.

## Debugging

### 2026-03-21 — uv sync timeout issues

**Problem**: `uv sync` commands kept running in the background and timing out in the CLI tool output reader.
**What happened**: Three separate `uv sync` invocations were launched (direct `uv`, `uv sync`, `python -m uv sync`) because the first two appeared to hang.
**Root cause**: `uv` was downloading and installing 68 packages which took longer than the default tool timeout.
**Resolution**: All three completed successfully. The `.venv/` was created and all packages installed. Used `ls .venv/Scripts/python.exe` to verify the venv existed before proceeding.
**Lesson**: Use longer timeouts for package installation commands, or check for the venv's existence rather than waiting for the install command output.

## 2026-05-10 — SECRETS.md key rotation docs

```bash
git diff --stat               # confirm only docs/SECRETS.md changed
git status                    # verify branch state before commit
git add docs/SECRETS.md STATUS.md docs/command-log.md
git commit -m "docs: expand SECRETS.md with UI_PASSWORD example and key rotation steps"
git push origin main
```
**What**: Updated `docs/SECRETS.md` — added `ERRANDER_UI_PASSWORD` to `.env` example, expanded key rotation into two procedures (key available vs. lost), added per-secret runtime notes.
**Why**: Users asked how encryption/decryption works for these two variables and whether docs cover key loss recovery.

### 2026-05-10 — --check-inventory CLI flag

```bash
# Smoke-test the new flag against the example inventory
uv run python -m errander --check-inventory --inventory example/inventory.yaml

# Verify error path (missing file)
uv run python -m errander --check-inventory --inventory nonexistent.yaml

git add errander/main.py scripts/configure.sh STATUS.md
git commit -m "fix: replace long inventory one-liner with --check-inventory CLI flag"
git push origin main

git status && git log --oneline -4   # post-push verification
```
**What**: Added `--check-inventory` CLI flag to `main.py` + `run_inventory_check()`. Replaced 200-char `python -c` one-liner in `configure.sh` Step 6 with the new short command.
**Why**: Long `echo` one-liners wrap in terminals; users copy the truncated visible text and get an open `>` shell prompt because the string isn't closed.

### 2026-05-10 — configure.sh set -e grep fixes

```bash
git add scripts/configure.sh STATUS.md tasks/lessons.md docs/command-log.md
git commit -m "fix: guard all bare grep calls with || true in configure.sh"
git push origin main
```
**What**: Added `|| true` to every bare `grep` call inside `$()` subshells in `configure.sh` — lines 159, 161, 163, 169, 302, 303, 349. Also fixed the key-line grep in the encryption section (primary bug).
**Why**: `set -euo pipefail` is active at the top of the script. `grep` exits 1 on no-match, which `set -e` treats as fatal — silently killing the script with no error message. The encryption section failed immediately after "Generating encryption key..." because the `grep "^ERRANDER_SECRETS_KEY="` pipe had no `|| true`.

### 2026-05-10 — fix MasterKeyMissingError in --check-llm

```bash
git add scripts/configure.sh errander/main.py STATUS.md tasks/lessons.md docs/command-log.md
git commit -m "fix: pass ERRANDER_SECRETS_KEY to --check-llm call and move early-exit modes before load_settings"
git push origin main
```
**What**: Two fixes — (1) configure.sh LLM verify now passes `ERRANDER_SECRETS_KEY` inline; (2) `--generate-secrets-key`, `--encrypt`, `--check-inventory` moved before `load_settings()` in `async_main`; `load_settings()` wrapped with `MasterKeyMissingError` catch printing a clear actionable message.
**Why**: `load_settings()` decrypts all env var values including `ERRANDER_UI_PASSWORD`. When `.env` contains `enc:v1:` blobs but `ERRANDER_SECRETS_KEY` isn't in the subprocess environment, it crashes with a Python traceback instead of a helpful message.

### 2026-05-11 — Phase 0 SRE audit remediation

```bash
uv run pytest tests -q        # baseline: 1 failed (test_disk_cleanup mock signature)
```
**What**: Ran full test suite to identify failures introduced by Phase 0 source changes.
**Why**: Phase 0 was implemented in the previous session; this session picked up the test fix.

```bash
# Fixed test_disk_cleanup.py capture_execute mock: added dry_run: bool | None = None param
# Fixed test_patching.py: route_after_execute(FAILED) now routes to "rollback", not "__end__"
# Fixed test_audit.py: swallow tests use dry_run=True (strict mode raises in live mode)
# Fixed test_rollback.py: patching rollback is now implemented; updated assertions
# Fixed test_graph.py (4 tests): deferred logic inverted — dry-run never deferred, live outside window IS deferred
# Fixed test_load.py: wave abort SSH call count 15→27 (12 validate + 12 plan_vm + 3 wave-0 health)
```
**What**: Fixed 9 test failures caused by Phase 0 architectural changes.
**Why**: Phase 0 changed: (1) executor.execute() signature, (2) patching rollback routing, (3) audit strict mode, (4) rollback implementation, (5) deferred semantics inversion, (6) new planning SSH calls in the graph.

```bash
uv run pytest tests -q        # result: 767 passed, 111 skipped, 0 failed
```

```bash
git add errander/agent/graph.py errander/agent/subgraphs/patching.py errander/agent/subgraphs/disk_cleanup.py errander/agent/subgraphs/docker_prune.py errander/agent/subgraphs/log_rotation.py errander/execution/sandbox.py errander/main.py errander/models/plans.py errander/safety/audit.py errander/safety/rollback.py errander/config/settings.py tests/agent/subgraphs/test_disk_cleanup.py tests/agent/subgraphs/test_patching.py tests/agent/test_graph.py tests/agent/test_load.py tests/safety/test_audit.py tests/safety/test_rollback.py STATUS.md tasks/todo.md tasks/lessons.md
git commit -m "feat: Phase 0 SRE audit remediation — plan/apply, rollback, audit fail-closed"
git push origin main
```

### 2026-05-11 — Phase 1: Security hardening (5 findings)

```bash
uv run pytest tests -q   # 787 passed before; 846 passed after Phase 1
```
**What**: Implemented all 5 Phase 1 security hardening items from ai_sre_remediation_plan.md.
**Why**: Shell injection (finding #10) is RCE on target VMs. SSH TOFU (finding #9) is MITM exposure. Docker prune -a (finding #12) destroys running-image layers. UI on 0.0.0.0 with opt-in auth (finding #14) is exposure on any public-facing server. Glob apt-mark (finding #11) silently fails to hold kernel packages.

```bash
git add errander/execution/command_builder.py errander/execution/ssh.py errander/execution/commands.py errander/agent/subgraphs/backup_verify.py errander/agent/subgraphs/log_rotation.py errander/agent/subgraphs/docker_prune.py errander/safety/rollback.py errander/config/settings.py errander/observability/metrics.py errander/main.py tests/execution/test_command_builder.py tests/execution/test_ssh_host_keys.py tests/agent/subgraphs/test_docker_prune_scope.py tests/observability/test_ui_security.py STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: Phase 1 security hardening — injection fix, SSH host keys, kernel exclusion, docker prune scope, UI security"
git push origin main
```

### 2026-05-11 — Phase 4: E2E verification (chaos suite, staging soak, Windows fix)

```bash
uv run pytest tests/chaos/ tests/ai_evals/ -q   # 51 passed (after 3 fix rounds)
uv run pytest tests -q                           # 918 passed, 111 skipped, 0 failed
```
**What**: Phase 4 from ai_sre_remediation_plan.md — chaos/fault-injection tests (4.2), staging soak checklist (4.1), Windows temp path fix (4.3).
**Why**: (4.1) No runbook existed for validating the agent against real VMs before production. (4.2) No tests verified behavior under fault conditions — SSH drop, DB lock, dpkg lock, LLM unavailable, etc. (4.3) Hardcoded `/tmp/test-locks` breaks on Windows; test at `test_graph.py:181` used it.

```bash
git add tests/chaos/__init__.py tests/chaos/test_fault_injection.py tests/staging/__init__.py tests/staging/soak_checklist.md tests/agent/test_graph.py STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: Phase 4 E2E verification — chaos suite, staging soak checklist, Windows path fix"
git push origin main
```

### 2026-05-11 — Phase 3: Honest AI integration

```bash
uv run pytest tests -q                      # 867 before; 899 after Phase 3
uv run pytest tests/ai_evals/ -v            # 32 eval tests all passing
```
**What**: Implemented all 4 Phase 3 items from ai_sre_remediation_plan.md.
**Why**: (3.1) LLMClient existed but was never passed into the graph — `plan_actions_node` always used hardcoded ordering. (3.2) LLM output had no injection guard or policy enforcement — raw strings went to `_parse_action_types` unvalidated. (3.3) No eval harness to verify safety properties of LLM output. (3.4) No per-decision audit — impossible to reconstruct why the agent chose a plan.

```bash
git add errander/safety/ai_audit.py errander/agent/decisions.py errander/agent/vm_graph.py errander/agent/graph.py errander/main.py tests/ai_evals/__init__.py tests/ai_evals/test_golden_plans.py tests/agent/test_inventory_merge.py STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: Phase 3 honest AI integration — LLM threading, injection guard, AI eval harness, per-decision audit"
git push origin main
```

### 2026-05-11 — Phase 2: Policy enforcement + fleet safety

```bash
uv run pytest tests -q   # 846 passed before; 867 passed after Phase 2
```
**What**: All three Phase 2 items from ai_sre_remediation_plan.md.
**Why**: (2.1) `validate_action` silently ignored its `policy` param — CRITICAL reason now includes policy name, `env_policy` threaded into VMGraphState. (2.2) `fleet_failure_threshold` setting existed but nothing ever checked it pre-flight — `check_fleet_health_node` now aborts with FLEET_ABORT audit event when exceeded. (2.3) `echo ok` in validate_targets told us nothing about OS — replaced with `/etc/os-release` + `parse_os_release()` + `verify_os_match()`; OS_MISMATCH audit event on mismatch.

```bash
git add errander/models/events.py errander/safety/validators.py errander/agent/vm_graph.py errander/agent/graph.py tests/safety/test_audit.py tests/agent/test_graph.py tests/agent/test_load.py tests/agent/test_phase2_policy.py STATUS.md tasks/todo.md tasks/lessons.md
git commit -m "feat: Phase 2 policy enforcement — fleet abort, OS verification, policy-aware validation"
git push origin main
```

### 2026-05-11 — Phase 0 gaps: hash verification + policy thresholds + plan/apply tests

```bash
uv run pytest tests/agent/test_plan_apply_flow.py -v   # new tests: 19/20 initial, 20/20 after router fix
uv run pytest tests -q                                  # 787 passed, 111 skipped, 0 failed
```
**What**: Closed two Phase 0 gaps: (1) `verify_plan_hash_node` re-verifies SHA-256 before dispatching execution waves; (2) `env_policy` threaded from `EnvironmentSchema` into `BatchGraphState`, approval gate now enforces strict/moderate/relaxed thresholds; (3) `tests/agent/test_plan_apply_flow.py` with 20 tests.
**Why**: Phase 0.2 specified hash re-verification at execution time and policy-based approval — both were absent. Test file was called for in the remediation plan but never created.

```bash
git add errander/agent/graph.py errander/main.py tests/agent/test_plan_apply_flow.py STATUS.md tasks/todo.md docs/command-log.md
git commit -m "feat: Phase 0 gaps — hash verify at execution, policy-based approval thresholds, plan/apply tests"
git push origin main
```

### 2026-05-12 — Re-audit: 7 production blockers remediated

```bash
uv run pytest tests/agent/test_vm_graph.py::TestBuildVMGraph::test_full_dry_run_disk_cleanup tests/agent/test_graph.py::TestBuildBatchGraph::test_full_dry_run_single_vm -v   # 2 failures before fix
uv run python -c "from errander.agent.subgraphs.disk_cleanup import ALLOWED_CLEANUP_PATHS; print(list(ALLOWED_CLEANUP_PATHS))"   # audit frozenset order and SSH call count
uv run python -c "...counting_execute script..."   # confirm 11 SSH calls for disk_cleanup
uv run pytest tests/agent/ -v --tb=no              # 2 failed, 331 passed before fix
uv run pytest tests/agent/test_vm_graph.py::TestBuildVMGraph::test_full_dry_run_disk_cleanup tests/agent/test_graph.py::TestBuildBatchGraph::test_full_dry_run_single_vm -v   # 2 passed after fix
uv run pytest --tb=no -q                           # 918 passed, 111 skipped, 0 failed
```
**What**: Fixed all 7 production blockers from the 2026-05-11 re-audit of ai_sre_audit.md. (1) Blocker 1 — VM graph bypasses re-planning when `planned_actions` pre-populated; (2) Blocker 2 — LLM called during batch planning in `plan_vm_node`; (3) Blocker 3 — `--unsafe-legacy-live` removed, live mode unblocked; (4) Blocker 4 — all assess/snapshot/verify nodes use `dry_run=False`; (5) Blocker 5 — `verify_node` in patching sets FAILED + `route_after_verify` routes to rollback; (6) Blocker 6 — `_rollback_patching_dnf` added for RHEL/CentOS; (7) Blocker 7 — `AuditStore` constructed with `strict_mode=(settings.audit_mode == "strict")`.
**Why**: 7 blockers identified in re-audit were not yet addressed; production safety required all 7 fixed before staging soak.

**Root cause of 2 test failures**: disk_cleanup iterates all 5 ALLOWED_CLEANUP_PATHS (including both `apt-cache` AND `yum-cache`) — 6 assess SSH calls (df + 5 paths) + 5 execute simulate calls = 11 total. Tests only provided 9. Plus `drift_check` conditional edges didn't include `"dispatch_action"` as a valid target (needed for pre-approved plan bypass).

```bash
git add errander/agent/graph.py errander/agent/vm_graph.py errander/agent/subgraphs/patching.py errander/agent/subgraphs/disk_cleanup.py errander/agent/subgraphs/docker_prune.py errander/agent/subgraphs/log_rotation.py errander/agent/subgraphs/backup_verify.py errander/main.py errander/safety/rollback.py tests/agent/test_graph.py tests/agent/test_vm_graph.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: re-audit 7 blockers — approved plan enforcement, LLM planning, live mode, dry_run=False reads, verify→rollback, DNF rollback, audit strict mode"
git push origin main
```

### 2026-05-12 — Fourth-round audit: action params in plan artifact

```bash
uv run pytest tests/agent/test_plan_apply_flow.py -v          # 24 passed
uv run pytest --basetemp=.pytest-tmp -q                       # 929 passed, 111 skipped
git add errander/agent/graph.py tests/agent/test_plan_apply_flow.py README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: fourth-round audit — include action params in plan artifact, hash, and approval summary"
git push origin main
```
**What**: Fixed the last remaining medium risk from the fourth-round SRE audit. `plan_vm_node` now serializes `params` in the batch-level plan so they're covered by the plan hash and visible in the operator Slack approval message. `_format_plan_for_approval` surfaces up to 3 key=value pairs per action. 4 regression tests prove params affect the hash, survive to wave dispatch, and appear in the approval summary.
**Why**: Plan hash did not cover action params — two plans identical except for params (e.g., different package lists) had the same hash. Operator approved one set of params but execution could run a different set.

### 2026-05-12 — Third-round audit: 2 blockers + 2 high risks

```bash
uv run pytest tests/agent/test_vm_graph.py::TestRoutingDriftCheck tests/agent/subgraphs/test_log_rotation.py::TestVerifyNode tests/safety/test_rollback.py -v   # 19 passed
uv run pytest --basetemp=.pytest-tmp -q                                                                                                                            # 925 passed, 111 skipped
git add errander/agent/vm_graph.py errander/agent/graph.py errander/agent/subgraphs/log_rotation.py errander/safety/rollback.py tests/agent/test_vm_graph.py tests/agent/subgraphs/test_log_rotation.py tests/safety/test_rollback.py README.md STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md
git commit -m "feat: third-round audit — empty plan sentinel, live fail-closed, log rotation verify dry_run=False, DNF rollback version comparison"
git push origin main
```
**What**: Fixed 2 hard blockers + 2 high risks from the 2026-05-12 third-round SRE re-audit: (1) Blocker 1 — `pre_approved_plan_set` sentinel distinguishes empty approved plan from no plan; (2) Blocker 2 — live mode VM missing from approved plan fails closed instead of re-planning; (3) High Risk 1 — `log_rotation.verify_node` passes `dry_run=False`; (4) High Risk 2 — `_rollback_patching_dnf` compares every rpm version against snapshot.
**Why**: Audit identified that empty approved plan was falsy → re-planned after approval (plan/apply violation); missing VM in live mode silently re-planned; verify_node could return synthetic data; DNF rollback declared success without verifying versions.

## Phase A — Privilege Model Fixes (2026-05-15)

## Phase A.5 — Static gates cleanup (2026-05-15)

```bash
# Diagnostic baseline
uv run ruff check errander/ --statistics    # 382 errors
uv run mypy errander/ 2>&1 | grep "error:" | sed 's/.*\[\(.*\)\]$/\1/' | sort | uniq -c | sort -rn  # 112 errors

# 6-commit cleanup sequence
uv run ruff check errander/ --fix           # Commit 1: auto-fixes
uv run ruff check errander/ --statistics    # track burn-down
uv run mypy errander/                       # check mypy after each commit
uv run pytest --tb=short -q                 # verify 1378 passing after each commit

# Final state
uv run ruff check errander/   # All checks passed
uv run mypy errander/         # Success: no issues found in 72 source files
```
**What**: Phase A.5 — closed the SRE audit's persistent ruff (~382) and mypy (~112) findings. Zero errors in both linters.
**Why**: README/CLAUDE.md claimed "strict mypy" but it didn't pass. Closes the honesty gap before Phase B.

## Phase E + F (2026-05-16)

```bash
# Phase E3 — journalctl + systemctl enrichment
uv run pytest tests/agent/test_probe_live_enrich.py -x -q
uv run ruff check errander/ tests/agent/test_probe_live_enrich.py
uv run mypy errander/
git add errander/agent/probe.py errander/models/reports.py tests/agent/test_probe_live_enrich.py
git commit -m "feat: Phase E3 journalctl + systemctl --failed enrichment in probe_vm"

# Phase E4 — data source transparency
uv run pytest tests/agent/test_operator_assistant_sources.py -x -q
uv run pytest -x -q   # full suite: 1570 passed
git commit -m "feat: Phase E4 data source transparency -- sources_used in FleetContext, --ask prints Sources consulted"

# Phase F1 — stored signals
uv run pytest tests/agent/test_plan_vm_stored_signals.py -x -q
git commit -m "feat: Phase F1 stored signals feed into plan_vm_node -- StoredSignalContext, _load_stored_signals, prioritize_actions gets history"

# Phase F2 — early readiness check
uv run pytest tests/agent/test_validate_targets_readiness.py -x -q
uv run pytest -x -q   # full suite: verified
git commit -m "feat: Phase F2 validate_targets_node adds sudo/wrapper readiness check early -- TARGET_READINESS_BLOCKED event"

# Phase F3 — probe escalation
uv run pytest tests/agent/test_probe_escalation.py -x -q   # 14 passed
uv run pytest -x -q   # 1582 passed, 111 skipped
uv run ruff check errander/   # All checks passed
uv run mypy errander/          # 77 source files, no issues
git add errander/main.py errander/agent/probe.py errander/models/reports.py errander/observability/reporting.py tests/agent/test_probe_escalation.py
git commit -m "feat: Phase F3 probe escalation -- critical signals trigger Slack alert, DigestReport.escalation_needed"

# Phase F4 — post-cleanup disk gate
uv run pytest tests/agent/test_disk_gate.py -x -q   # 12 passed
uv run pytest -x -q   # 1582 passed, 111 skipped
uv run ruff check errander/ tests/agent/test_disk_gate.py   # All checks passed
uv run mypy errander/   # 77 source files, no issues
git add errander/agent/vm_graph.py errander/models/events.py tests/agent/test_disk_gate.py
git commit -m "feat: Phase F4 post_cleanup_disk_gate_node -- re-check disk after cleanup before patching, block at 95%"

# P0-1 immutable execution artifact fix (2026-05-19)
uv run pytest tests/agent/subgraphs/test_patching.py tests/execution/test_commands.py tests/agent/test_deferred_replay.py -x -q   # 109 passed
uv run pytest -x -q   # 1982 passed, 0 failures
git add errander/execution/commands.py errander/agent/subgraphs/patching.py errander/agent/vm_graph.py errander/agent/graph.py errander/main.py tests/agent/subgraphs/test_patching.py tests/execution/test_commands.py tests/agent/test_deferred_replay.py tests/chaos/test_fault_injection.py STATUS.md docs/learning/37-immutable-plan-artifact.md
git commit -m "fix: P0-1 true immutable execution artifact — pinned patching + deferred replay age check"

# P0-1 complete closure — second SRE pass (2026-05-19)
uv run pytest tests/agent/subgraphs/test_patching.py tests/agent/test_deferred_replay.py -x -q   # 96 passed
uv run pytest -x -q   # 1989 passed, 0 failures
git add errander/agent/subgraphs/patching.py errander/agent/graph.py tests/agent/subgraphs/test_patching.py tests/agent/test_deferred_replay.py STATUS.md tasks/todo.md tasks/lessons.md docs/command-log.md README.md
git commit -m "fix: P0-1 complete closure — assess artifact path, verify exact match, approved_at required"
```

# Docker hygiene v1.1 Session 1 (2026-05-21)
uv run pytest tests/agent/subgraphs/test_docker_hygiene.py -x -q   # 40 passed (new file)
uv run pytest -x -q   # 2215 passed (+43 new from docker_hygiene + 3 registry updates)
uv run ruff check errander/models/docker_hygiene.py errander/agent/subgraphs/docker_hygiene.py errander/agent/subgraphs/__init__.py errander/models/actions.py errander/execution/target_validation.py tests/agent/subgraphs/test_docker_hygiene.py tests/agent/subgraphs/test_registry.py tests/agent/subgraphs/test_service_restart_manifest.py   # All checks passed!
uv run ruff check tests/agent/subgraphs/test_docker_hygiene.py --fix   # 1 import-order fix applied
uv run mypy errander/   # 9 pre-existing errors in unrelated files, none in new code

# Docker hygiene v1.1 Session 2a (2026-05-22)
uv run pytest tests/agent/subgraphs/test_docker_hygiene.py -x -q   # 62 passed (40 Session 1 + 22 Session 2a)
uv run pytest -x -q   # 2237 passed (+22 net new)
uv run ruff check errander/models/docker_hygiene.py errander/agent/subgraphs/docker_hygiene.py errander/models/events.py errander/agent/vm_graph.py tests/agent/subgraphs/test_docker_hygiene.py   # All checks passed!
uv run mypy errander/   # 9 pre-existing errors in unrelated files; no new errors in changed source files

# Defense-in-depth for LLM continuity (2026-05-22)
# No behavior change — pytest sanity only
uv run pytest -x -q   # 2237 passed (no regressions; INVARIANT comments are pure additions)

# Docker hygiene v1.1 Session 2b-i (2026-05-22)
uv run pytest tests/integrations/test_signed_url.py tests/safety/test_hygiene_approval.py -x -q   # 52 passed (17 signed-URL + 35 hygiene approval)
uv run pytest -x -q   # 2289 passed (+52 net new)
uv run ruff check errander/integrations/signed_url.py errander/safety/hygiene_approval.py tests/integrations/test_signed_url.py tests/safety/test_hygiene_approval.py   # All checks passed!
uv run mypy errander/integrations/signed_url.py errander/safety/hygiene_approval.py   # Success: no issues found in 2 source files

# Docker hygiene v1.1 Session 2b-ii (2026-05-22)
uv run pytest tests/safety/test_hygiene_web_approve.py tests/safety/test_hygiene_reply_polling.py -x -q   # 20 passed (11 web + 9 polling)
uv run pytest -x -q   # 2309 passed (+20 net new; resolved pytest-asyncio runner pollution from tests/ui by moving web tests under tests/safety + using manual event-loop driver)
uv run ruff check errander/web/server.py errander/safety/hygiene_approval.py errander/integrations/slack.py tests/safety/test_hygiene_web_approve.py tests/safety/test_hygiene_reply_polling.py   # All clean on new code; pre-existing N814/UP037/no-any-return errors in unrelated lines of server.py

# Docker hygiene v1.5 — volume + build_cache deletion (2026-05-22)
uv run pytest tests/agent/subgraphs/test_docker_hygiene.py tests/safety/test_hygiene_approval.py tests/config/test_schema_actions.py -x -q   # targeted run (new test classes)
uv run pytest -x -q   # 2295 passed (+37 net new; decisions.py semantic debt fix +5 net new committed separately)
uv run ruff check errander/agent/subgraphs/docker_hygiene.py errander/config/schema.py errander/safety/hygiene_approval.py errander/agent/vm_graph.py scripts/install-docker-wrappers-v2.sh tests/agent/subgraphs/test_docker_hygiene.py tests/safety/test_hygiene_approval.py tests/config/test_schema_actions.py   # All checks passed!
uv run mypy errander/agent/subgraphs/docker_hygiene.py errander/config/schema.py errander/safety/hygiene_approval.py errander/agent/vm_graph.py   # Success: no issues found in 4 source files

# AI Trust Layer Phase 5 — Source Citation (2026-05-24)
uv run pytest tests/agent/test_operator_assistant.py tests/agent/test_operator_assistant_facts.py -v --tb=short  # 43 passed
uv run ruff check errander/models/analysis.py errander/agent/operator_assistant.py tests/agent/test_operator_assistant.py tests/agent/test_operator_assistant_facts.py  # 1 fixable I001
uv run ruff check --fix tests/agent/test_operator_assistant.py  # fixed import sort
uv run pytest --tb=short -q  # 2485 passed

# AI Trust Layer Phase 6a — Provider Prefix/Input Caching (2026-05-24)
uv run pytest tests/integrations/test_llm.py -v --tb=short  # 35 passed
uv run ruff check --fix tests/integrations/test_llm.py  # fixed I001 import sort
uv run pytest --tb=short -q  # 2496 passed

# Web UI demo screenshots for README (2026-05-29)
.venv/Scripts/python -c "import playwright"   # confirm Playwright available before writing capture script
uv run python scripts/capture_ui_screenshots.py   # seed in-memory stores + serve UI on loopback + headless-Chromium screenshot 6 pages into docs/images/ (with baked-in DEMO banner)
ls -lh docs/images/   # verify sizes — 6 PNGs, ~950 KB total
