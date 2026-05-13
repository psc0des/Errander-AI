# Errander-AI — Task Tracking

## SRE Monitoring — PR-G Groundwork (2026-05-13)

- [x] G1 — `ActionStatus.BLOCKED` enum value
- [x] G2 — 8 new `EventType` values (preflight, reboot, service health, disk, drift, failed logins)
- [x] G3 — `VMTarget.critical_services` field + inventory inheritance
- [x] G4 — migrations framework (`errander/safety/migrations.py`, 4 migrations); `AuditStore.initialize()` delegates to `run_migrations()`
- [x] G5 — `VMStateStore` (vm_state.py), `BaselineStore` + `DriftCheck` Protocol (baselines.py), `VMDiskHistoryStore` (disk_history.py)
- [x] G6 — `BatchReport` model + supporting dataclasses (models/reports.py); `SRESignalSettings` config block
- [x] All 84 new tests passing (996 total); mypy strict clean; ruff clean

## SRE Phase 1 — Signal Collection

- [x] 1.1 — Package lock detection pre-flight: `PackageManager.detect_lock()` + `validate_no_pkg_lock()` + `preflight_lock_node` in patching subgraph; 35 new tests; 1031 total passing
- [x] 1.2 — Reboot-required detection (post-patch): `reboot_check.py` with `RebootStatus`, `reboot_required_command()`, `parse_reboot_status()`, `detect_reboot_required()`; `reboot_check_node` after verify; `format_reboot_required_section()` in reporting; 46 new tests; 1077 total passing
- [x] 1.3 — Service health checks (pre/post action snapshot): `service_check.py` with `ServiceStatus`, `service_status_command()`, `parse_service_statuses()`, `find_regressions()`, `check_services()`; `service_health_pre_node` + `service_health_post_node`; `SERVICE_HEALTH_REGRESSION` audit event; `sre_service_check` flag; 47 new tests; 1124 total passing
- [x] 1.4 — Disk growth trend: `disk_trend.py` with `disk_bytes_command()`, `parse_df_bytes()`, `compute_growth_alert()`, `detect_growth_alerts()`, `record_and_detect_disk_growth()`; `disk_snapshot_node` in vm_graph wired between discover and drift_check; 24 new tests; 1,148 total passing

## Operations Hub UI — Glossary + Inventory + Settings + Admin (2026-05-13)

- [x] Glossary page: 18-term glossary grid (CORE/SAFETY/ACTIONS/INFRA categories) + animated LangGraph DAG workflow diagram with node-click modal popups
- [x] Inventory page: KPI tiles (Total VMs, OS Types, Reachable), filter bar (search + env/os/status dropdowns), full VM table
- [x] Settings page: 4 read-only config cards (LLM, Slack, Scheduling, Safety & Audit) in 2-col grid
- [x] Admin page: Agent controls card, system health checks, lock manager (empty state), override toggles (CSS), danger zone
- [x] Wired handle_inventory(), handle_settings(), handle_admin() route handlers
- [x] NAV_ITEMS updated with ADMIN section + Admin Panel link
- [x] All routes registered in create_app() — /inventory, /settings, /admin, /glossary all live

## Fourth-Round Audit: Action Params in Plan Artifact (2026-05-12)

### From ai_sre_audit.md fourth re-audit (2026-05-12)
- [x] Medium Risk — Batch plan includes action params: `plan_vm_node` now serializes `"params": a.params`; plan hash covers params; Slack summary shows non-empty params; 4 regression tests added

## Third-Round Audit: 2 Blockers + 2 High Risks (2026-05-12)

### From ai_sre_audit.md third re-audit (2026-05-12)
- [x] Blocker 1 — Empty approved plan distinguisher: added `pre_approved_plan_set: bool` sentinel to `VMGraphState`; `route_after_drift_check` routes empty approved plan → audit_results (not re-plan)
- [x] Blocker 2 — Missing approved plan fail-closed: live mode VM not in `vm_id_to_approved_actions` → injects `error` + `pre_approved_plan_set=True`; never falls back to re-planning after approval
- [x] High Risk 1 — Log rotation verify real read: `verify_node` in `log_rotation.py` now passes `dry_run=False`
- [x] High Risk 2 — DNF rollback version comparison: `_rollback_patching_dnf` now parses rpm output and compares each package version against snapshot; returns failure on any mismatch

## Re-Audit: 7 Production Blockers (2026-05-12)

### From ai_sre_audit.md re-audit (2026-05-11)
- [x] Blocker 1 — Pre-approved plan enforced: `route_after_drift_check` skips re-planning when `planned_actions` populated; `dispatch_current_wave` passes `vm_id_to_approved_actions` to each VM; `drift_check` conditional edges include `"dispatch_action"`
- [x] Blocker 2 — LLM in planning: `plan_vm_node` passes `llm_client`, `ai_decision_store`, `env_policy`, `batch_id`, `vm_id` to `prioritize_actions()`
- [x] Blocker 3 — Live mode unblocked: `--unsafe-legacy-live` removed from `main.py`; live guard block removed; `--live` flag works directly
- [x] Blocker 4 — Read-only always live: all assess/snapshot/verify nodes across all 5 subgraphs use `dry_run=False`
- [x] Blocker 5 — Verify → rollback: `verify_node` in patching sets `status=FAILED`; `route_after_verify` conditional edge routes FAILED to rollback; graph wired with `add_conditional_edges("verify", route_after_verify, ["rollback", END])`
- [x] Blocker 6 — DNF rollback: `_rollback_patching_dnf` added; `rollback_action` dispatches by `os_family`; `rollback_node` passes `os_family`
- [x] Blocker 7 — Audit mode wiring: `AuditStore(strict_mode=(settings.audit_mode == "strict"))` in both `async_main` and `run_audit_query`
- [x] Fix test failures: disk_cleanup needs 11 SSH calls (6 assess + 5 execute); added missing responses; fixed `drift_check` conditional edges missing `"dispatch_action"`

## Phase 4: E2E Verification (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 4.1 Staging soak — `tests/staging/soak_checklist.md`: 8-step checklist covering OS verification, dry-run/live run per action type, fleet abort, SSH host key pinning, chaos DB lock, AI audit; destroy VMs after each run
- [x] 4.2 Chaos suite — `tests/chaos/test_fault_injection.py`: 19 fault-injection tests covering SSH drop, patching rollback trigger, dpkg lock, audit strict/best-effort modes, LLM timeout/malformed/unavailable, approval manager rejection, fleet abort node
- [x] 4.3 Windows test infra fix — hardcoded `/tmp/test-locks` → `tmp_path / "locks"` in `test_graph.py:181`; confirmed no bare `TemporaryDirectory()` usage in test suite; all 918 tests pass on Windows

## Phase 3: Honest AI Integration (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 3.1 Thread LLMClient into graph — `build_batch_graph` → `make_wave_dispatcher` → `build_vm_graph` → `plan_actions_node`; `run_env_batch` accepts `llm_client`; all 3 call sites in main.py updated
- [x] 3.2 Constrained plan schema — injection guard (`_INJECTION_RE`) rejects shell metacharacters in LLM action type strings; policy enforcement filters LLM output; `_parse_action_types` validates against allow-list; policy name logged on every call
- [x] 3.3 AI eval harness — `tests/ai_evals/test_golden_plans.py`: 32 tests across golden plans, injection corpus (8 payloads), schema-violation corpus, per-decision audit capture
- [x] 3.4 Per-decision AI audit — `errander/safety/ai_audit.py`: `AIDecisionStore` + `AIDecision` dataclass; `ai_decisions` SQLite table; logs model, base_url, prompt_template_id, prompt_hash, response, outcome, latency_ms, token counts per call; integrated into `prioritize_actions()`

## Phase 2: Policy Enforcement + Fleet Safety (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 2.1 Wire `requires_approval()` into validate_action — policy param now used; CRITICAL always blocked; policy name in rejection reasons; `env_policy` threaded from BatchGraphState → VMGraphState via Send payload
- [x] 2.2 Enforce `fleet_failure_threshold` — `check_fleet_health_node` between validate_targets and planning fan-out; FLEET_ABORT audit event; routing aborts to generate_report when threshold exceeded
- [x] 2.3 Strict OS verification — `validate_targets_node` replaced `echo ok` with `cat /etc/os-release` + `parse_os_release()` + `verify_os_match()`; mismatches emit OS_MISMATCH audit event; detected os_family stored in target dict for downstream
- [x] Added `FLEET_ABORT` and `OS_MISMATCH` to EventType enum
- [x] `tests/agent/test_phase2_policy.py` — 21 tests covering all three items

## Phase 1: Security Hardening (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 1.1 SSH host key verification — `known_hosts_path` + `strict_host_keys` in settings + SSHConnectionManager; TOFU logs WARNING; strict mode refuses connection; `--bootstrap-known-hosts <env>` CLI; 6 tests
- [x] 1.2 Shell injection — `errander/execution/command_builder.py` with `safe_path`, `safe_pkg`, `safe_ver`, `pkg_version_spec`, `build_cmd`; injection sites fixed in `backup_verify.py`, `log_rotation.py`, `commands.py`, `rollback.py`; 22 injection corpus tests
- [x] 1.3 Kernel exclusion fix — `AptManager.upgrade_all` / `DnfManager.upgrade_all` now query exact installed kernel names via dpkg-query/rpm, filter in Python, hold/versionlock by exact name; no more glob-based apt-mark
- [x] 1.4 Docker prune scope — default uses `docker image prune -f && docker container prune -f` (dangling-only); `docker_prune_aggressive=True` reclassified HIGH runs `system prune -af`; 4 tests
- [x] 1.5 UI security — bind default `127.0.0.1`; mandatory auth when non-loopback; CSRF double-submit cookie middleware on all POST /ui/*; `_inject_csrf` helper adds hidden token to all form tags; `ui_bind_address` setting

## Phase 0: SRE Audit Remediation (2026-05-11)

### Ship-stopper fixes from ai_sre_remediation_plan.md
- [x] Finding #2 — dry_run single source of truth: `SandboxExecutor.execute()` per-call override; all sub-graphs read `state["dry_run"]`
- [x] Finding #3 — plan/apply before execution: planning fan-out → ImmutablePlan with SHA-256 hash → approval gate BEFORE execution
- [x] Finding #3 (gap) — `verify_plan_hash_node` re-verifies hash at execution time; tampered hash aborts cleanly
- [x] Finding #5 — patching rollback (Option A): dpkg snapshot + apt-get --allow-downgrades + verification in `rollback_node`
- [x] Finding #6 (gap) — `env_policy` threaded from inventory → `BatchGraphState` → approval gate; strict policy gates MEDIUM tier
- [x] Finding #13 — audit fail-closed: `AuditWriteError` raised in strict mode; dry-run stays best-effort
- [x] Phase 0 gate: `--unsafe-legacy-live` guard in main.py
- [x] Fix all 9 test failures from Phase 0 changes (mock sigs, routing, deferred logic, audit mode, rollback assertions, SSH call counts)
- [x] `tests/agent/test_plan_apply_flow.py` — 20 tests for plan/apply integrity (hash verify, routing, policy thresholds)

## Phase 1.8 — End-to-End Validation + configure.sh Polish (2026-05-10)

### configure.sh UX fixes
- [x] Ask "Do you want to add VMs?" before showing section header (fresh install)
- [x] Suppress SSH key step header when key already exists
- [x] Remove SSH key generation — script now verifies only; users own key creation (SETUP.md Step 2)
- [x] Split "Keep existing VMs and add more?" into two separate prompts
- [x] Remove stale "Complete Steps 2-3" reminder from final summary
- [x] Final summary shows Step 6 verify before Step 7 dry-run, matching SETUP.md order
- [x] Add --force --force-reason to dry-run command in summary and SETUP.md Step 7
- [x] Fix approval gate — dry-run batches auto-approved, no human gate needed
- [x] Fix "Add more VMs? (y/N)" defaulting to yes on Enter — flip case branches
- [x] Fix fresh install Enter on "Add VMs?" silently adding no VMs
- [x] Fix "Keep + Add more" silently dropping new VMs (append TARGETS_YAML)
- [x] Fix re-run resetting UI password — read existing creds from .env first
- [x] Prompt for web UI username + password explicitly (with confirmation loop)
- [x] Add optional Fernet encryption — key to ~/.errander.key, enc:v1: blobs in .env
- [x] chmod 600 .env always on write
- [x] Auto-wire encryption key to shell RC and systemd — no manual steps

### SETUP.md fixes
- [x] Step 7 and Step 8 --env dev → --env <your-env-name>
- [x] Azure Foundry URL: openai.azure.com → cognitiveservices.azure.com
- [x] systemd service: hardcoded errander user → $(whoami) + $(pwd)
- [x] Quick path description: "SSH key" → "verify your SSH key path"
- [x] Add UI password change warning

### bootstrap.sh
- [x] Correct step numbers in completion message; surface configure.sh quick path

## Phase 1: Scaffold + First Action End-to-End (disk_cleanup)

### 1.1 Project Foundation
- [x] Scaffold project structure (Option C: Parent + Fan-Out + Sub-Graphs)
- [x] Create pyproject.toml with all dependencies
- [x] Define data models (VM, Action, Plan, Event)
- [x] Define state dataclasses (BatchState, VMMaintenanceState, per-action states)
- [x] Define strategy pattern stubs (PackageManager, AptManager, DnfManager)
- [x] Define policy system (relaxed/moderate/strict)
- [x] Create test structure mirroring src
- [x] Run `uv sync` and verify all imports work
- [x] Run `uv run pytest` and verify passing tests

### 1.2 Core Infrastructure
- [x] Implement Settings loading from env vars + YAML (settings.py, schema.py, inventory.py)
- [x] Implement inventory YAML loading with environment→host inheritance
- [x] Implement config schema validation (Pydantic models)
- [x] Implement audit logging to SQLite (AuditStore with write/query/count)
- [x] Implement SSH execution layer (SSHConnectionManager with pooling + retry)
- [x] Implement OS detection via SSH (parse /etc/os-release, df, docker, uptime)
- [x] Implement sandbox/dry-run execution wrapper (SandboxExecutor + CommandRecord)
- [x] Implement file-based VM locking (FileLocker with TTL + stale detection)

### 1.3 First Action: Disk Cleanup (lowest risk)
- [x] Implement disk_cleanup sub-graph (validate → assess → execute → verify)
- [x] Implement whitelist enforcement (hardcoded, never LLM-decided)
- [x] Implement dry-run simulation for disk cleanup
- [x] Implement AptManager + DnfManager command generation (clean_cache, autoremove, etc.)
- [x] Write tests for disk cleanup sub-graph (31 tests)
- [ ] Test against a real VM (dry-run mode)

### 1.4 Per-VM Graph
- [x] Implement vm_graph (lock → discover → plan → dispatch → check_more → audit → unlock)
- [x] Implement discovery node (SSH gather system state via detect_os)
- [x] Implement action dispatch with conditional routing to sub-graphs (disk_cleanup; others SKIPPED)
- [x] Implement hardcoded action prioritization (LLM deferred to Phase 1.6)
- [x] Write tests for per-VM graph (28 tests)

### 1.5 Batch Orchestrator
- [x] Implement batch graph (init_batch → validate_window → validate_targets → fan_out → collect → report)
- [x] Implement Send() fan-out to per-VM graphs (via conditional edge routing function)
- [x] Implement result collection and aggregation (append-only reducer)
- [x] Implement report generation (template-based; LLM Phase 1.6)
- [x] Write tests for batch orchestrator (21 tests)

### 1.6 Integrations
- [x] Implement LLM client (OpenAI SDK → vLLM) with fallback (23 tests)
- [x] Implement Slack client (post message, poll reactions)
- [x] Implement approval gate (post plan → poll → approve/reject/timeout)
- [x] Implement Prometheus metrics and /health endpoint
- [x] Implement dual-channel approval (Slack reactions + UI buttons racing via asyncio.wait)

### 1.7 Config & Scheduling
- [x] Implement inventory YAML loader and validator
- [x] Implement maintenance window enforcement
- [x] Implement APScheduler setup
- [x] Create example inventory.yaml

### Playwright UI Tests
- [x] Add pytest-playwright to dev dependencies + install Chromium
- [x] Write server fixture (aiohttp in background thread, seeded :memory: SQLite)
- [x] Dashboard tests (6): loads, status, event count, batches, nav links, navigation
- [x] Batch list tests (3): loads, both batches, link navigation
- [x] Batch detail tests (8): loads, count, event types, detail text, VM link, back link, empty state
- [x] VM history tests (6): loads, count, detail, back link, slash URL, empty state
- [x] Endpoint smoke tests (2): /health, /metrics

### Documentation
- [x] Write docs/SETUP.md (end-to-end setup: prerequisites, vLLM, SSH, Slack, config, first run, systemd, monitoring, troubleshooting)

### vLLM Deployment
- [x] Create deploy/vllm/docker-compose.yml (GPU passthrough, exact serve command, healthcheck)
- [x] Create deploy/vllm/.env.example (MODEL_ID, HF_TOKEN, GPU_MEM_UTIL, VLLM_PORT, MODEL_CACHE_DIR)
- [x] Add LLMClient.check_endpoint() (reachability + model list + test completion latency)
- [x] Add --check-llm CLI flag to main.py

### Web UI (built into aiohttp server)
- [x] Extend start_metrics_server() with audit_store parameter
- [x] Implement /ui dashboard (status, event count, recent batches, auto-refresh)
- [x] Implement /ui/batches batch history page
- [x] Implement /ui/batches/{id} batch detail page
- [x] Implement /ui/vms/{vm_id} VM history page (slash-safe URL matching)
- [x] Implement /ui/approvals page (pending approvals with Approve/Reject buttons)
- [x] Implement POST /ui/approvals/{id}/approve and /reject endpoints
- [x] Add pending approvals count card to dashboard

### SQLite Audit Integration (native)
- [x] Add action_type filter to AuditStore.get_events()
- [x] Add AuditStore.get_recent_batches() method
- [x] Add --audit CLI mode to main.py (--batch-id, --vm-id, --action-type, --event-type, --last, --batches)
- [x] Write integration tests: action_type filter, get_recent_batches, vm_graph audit trail, CLI queries (21 tests)

### 1.8 End-to-End Validation
- [x] Wire validate_window_node in graph.py to real is_within_window() call
- [x] Implement main.py entry point (load config, start scheduler + metrics server, wire graph)
- [x] Overhaul SETUP.md — prerequisites, network ports, Azure NSG, architecture diagram, step labels
- [x] Create scripts/bootstrap.sh — distro-agnostic Linux bootstrap (Ubuntu/Debian/RHEL/CentOS/Oracle/Fedora)
- [x] Create scripts/bootstrap.ps1 — Windows bootstrap (winget git, official uv installer, no admin)
- [x] Create scripts/configure.sh — interactive setup (LLM, VMs, SSH key, Slack, writes .env + inventory.yaml)
- [x] Create .gitattributes — enforce LF line endings for .sh files
- [x] Fix Step 4: verify LLM inline (no .env), collect values → Step 5 creates .env
- [x] Fix Step 5 + old Step 6 confusion: merged Slack into Configure step, steps 6-9 renumbered
- [x] Add Steps 4-6 quick path section (configure.sh one-liner)
- [ ] Dry-run disk_cleanup on a test VM via the full graph pipeline
- [ ] Verify audit trail captures all events
- [ ] Verify Slack notification works
- [ ] Verify metrics are exposed

## Phase 2: Remaining Action Types
- [x] Implement log_rotation sub-graph (path validation, logrotate + manual fallback, idempotency)
- [x] Implement docker_prune sub-graph (docker availability check, dangling/stopped detection, idempotency)
- [x] Implement patching sub-graph (kernel exclusion via fnmatch, version snapshot, rollback, idempotency)
- [x] Implement backup_verify sub-graph (read-only: exists/recent/non-zero checks, no execute node)
- [x] Wire all sub-graphs into vm_graph.py dispatch (all 5 action types now dispatched)
- [x] Idempotency via pre-check skipping in all assess nodes (nothing_to_do flag)
- [x] 28 log_rotation tests, 18 docker_prune tests, 24 patching tests, 14 backup_verify tests
- [x] Design review — 10 issues found and fixed (kernel exclusion, whitelist, approval, rollback, etc.)
- [x] All 587 tests passing, lint clean

## Phase 3: Hardening
- [x] Rolling updates (percentage-based fleet caps)
- [x] Canary logic (run on 1 VM first, then fleet)
- [x] Drift detection (pre-flight check before live execution)
- [x] Comprehensive error handling and edge cases (25 new tests, 677 total)
- [x] Load testing with multiple VMs (20 tests: wave partitioning, fleet batch graph, concurrent locks)
- [x] Playwright approvals UI tests (22 tests: page content, navigation, approve/reject actions, badge cross-page)

## Phase 4: LLM Flexibility + Secrets Encryption + UI Config

### Phase A — LLM Provider Flexibility
- [x] Remove hardcoded `Qwen/Qwen3-8B-AWQ` from `llm.py`; add `model: str` + `temperature: float` to `LLMClient.__init__`
- [x] Remove `thinking: bool` param and `/no_think` prefix from `complete()`
- [x] Add `llm_model` + `llm_temperature` to `Settings` dataclass + `load_settings()`
- [x] Add `model` + `temperature` fields to `LLMSettingsSchema` with validator (0.0–2.0)
- [x] Update `_build_components()` and `run_llm_check()` in `main.py` to pass model/temperature
- [x] Update `decisions.py` — remove `thinking=` kwarg from all `client.complete()` calls
- [x] Add `model` + `temperature` to `config/settings.yaml` and `example/settings.yaml`
- [x] Write `docs/LLM-PROVIDERS.md` (vLLM, Ollama, OpenAI, Anthropic, Groq configs)
- [x] Rewrite `tests/integrations/test_llm.py` (removed thinking tests, added verbatim/temp/model tests)

### Phase A.5 — Secrets Encryption
- [x] Implement `SecretsManager` with Fernet (`enc:v1:<token>` format) in `errander/integrations/secrets.py`
- [x] Add `--generate-secrets-key` and `--encrypt VALUE` CLI flags to `main.py`
- [x] Add YAML decryption (`_decrypt_yaml_strings`) to all `validate_*` functions in `schema.py`
- [x] Update `_load_env_str()` in `settings.py` to decrypt `enc:v1:` env var values
- [x] Implement `SecretsRedactingFilter` log filter in `errander/observability/redaction.py`
- [x] Attach redaction filter to root logger in `main.py`
- [x] Write `docs/SECRETS.md` (setup, threat model, key rotation)
- [x] Write `tests/integrations/test_secrets.py` (24 tests)
- [x] Write `tests/observability/test_redaction.py` (9 tests)
- [x] Write `tests/config/test_secrets_loading.py` (6 tests)

### Phase B — UI Settings + Inventory Management
- [x] Implement `OverridesStore` (SQLite) in `errander/safety/overrides.py` — `settings_overrides` + `inventory_overrides` tables
- [x] Extend `load_settings()` with `db_overrides: dict[str, str]` param — env > DB > YAML > default precedence
- [x] Add `SETTINGS_CHANGED` + `INVENTORY_CHANGED` to `EventType` enum in `events.py`
- [x] Add `GET/POST /ui/settings` routes with source indicators, model presets, reset buttons, LLM test
- [x] Add `GET/POST /ui/inventory` routes — disable YAML VMs, add/delete ad-hoc VMs
- [x] Add HTTP Basic Auth middleware on `/ui/*` (via `secrets.compare_digest`)
- [x] Implement inventory merge in `run_env_batch()` (YAML → filter disabled → append db_additions)
- [x] Wire `overrides_store` into scheduler loop `_run()` closure
- [x] Add `ui_user`, `ui_password`, `sources` fields to `Settings`
- [x] Update `async_main()`: init `OverridesStore`, pass to `start_metrics_server()`
- [x] Write `tests/safety/test_overrides.py` (18 tests — T1)
- [x] Write `tests/config/test_settings_precedence.py` (21 tests — T2)
- [x] Write `tests/agent/test_inventory_merge.py` (9 tests — T3)
- [x] Write `docs/learning/22-ui-settings-and-inventory.md`
- [x] Update `docs/SETUP.md` — Step 5b: Secure the Web UI
- [x] Update `STATUS.md` and `tasks/todo.md`
- [x] 799 tests passing, lint clean

### Phase B — Playwright Tests (T4-T6)
- [x] Write `tests/ui/test_settings_playwright.py` (15 tests): page load, save+persist, reset, env-lock source labels
- [x] Write `tests/ui/test_inventory_playwright.py` (17 tests): page load, VM display, toggle, add ad-hoc VM, delete
- [x] Write `tests/ui/test_ui_auth_playwright.py` (13 tests): 401 without creds, 200 with creds, wrong user/pass, WWW-Authenticate, /metrics+/health open
- [x] Bug fix: nested `<form>` inside main settings form broke Save button (Chromium closes outer form). Fixed via HTML5 `form="reset-{key}"` out-of-band pattern.
- [x] Create `errander/__main__.py` so `python -m errander` works
- [x] 844 tests passing, lint clean

### Deferred Execution — Window-Gated Approval
- [x] Add `EXECUTION_DEFERRED` + `DEFERRED_EXECUTION_STARTED` to `EventType` (`errander/models/events.py`)
- [x] Create `DeferredExecutionStore` with SQLite table `deferred_executions` (`errander/safety/deferred.py`)
- [x] Add `next_window_open()` and `window_start_cron()` to `errander/scheduling/windows.py`
- [x] Extend `BatchGraphState` with `env_name` and `deferred` fields (`errander/agent/graph.py`)
- [x] Add deferral logic to `approval_gate_node` — saves to `DeferredExecutionStore`, logs audit event, Slack notification
- [x] Add `deferred_store` param to `build_batch_graph()`
- [x] Add `_window_opener()` function to `errander/main.py`
- [x] Register window-opener cron jobs in scheduler loop
- [x] Initialize `DeferredExecutionStore` alongside `AuditStore` in `async_main()`
- [x] Thread `env_name` into batch initial state
- [x] Write `tests/safety/test_deferred.py` (15 tests)
- [x] Write 9 new window helper tests in `tests/scheduling/test_windows.py`
- [x] Write 6 new approval gate deferred tests in `tests/agent/test_graph.py`
- [x] Write 3 new `_window_opener` tests in `tests/test_main.py`
- [x] Update `STATUS.md`, `todo.md`, `command-log.md`, `docs/learning/24-deferred-execution.md`
- [x] 878 tests passing
