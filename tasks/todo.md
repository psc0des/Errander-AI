# Errander-AI — Task Tracking

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
