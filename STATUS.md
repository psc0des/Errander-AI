# Errander-AI — Project Status

## Last Updated
2026-05-11

## Current Phase
**SRE Remediation — Phases 0, 1, and 2 complete. Phase 3 (honest AI integration) is next.**

## Completed

### Phase 1.1: Project Foundation
- Full project scaffold — Option C architecture (Parent Orchestrator + Fan-Out + Sub-Graphs)
- Data models, state dataclasses, strategy pattern stubs, policy system
- All module stubs created, test structure mirroring src

### Phase 1.2: Core Infrastructure
- Settings loader (env vars + YAML), schema validation, inventory loader with inheritance
- Audit logging (async SQLite), SSH execution (connection pooling + retry)
- OS detection, sandbox/dry-run wrapper, file-based VM locking

### Phase 1.3: Disk Cleanup (sub-graph complete)
- **Sub-graph**: LangGraph StateGraph with 4 nodes: validate → assess → execute → verify
- **Whitelist enforcement**: Hardcoded `ALLOWED_CLEANUP_PATHS` — `/tmp`, `apt-cache`, `yum-cache`, `journal`, `orphaned-deps`. Non-whitelisted paths are BLOCKED immediately.
- **Dry-run mode**: Uses simulate commands (e.g., `apt-get autoremove --simulate`) or synthetic `[DRY-RUN]` results
- **Live mode**: Real cleanup commands — `find /tmp -delete`, `apt-get clean`, `journalctl --vacuum-time`, `autoremove`
- **OS-aware**: AptManager for Ubuntu/Debian, DnfManager for RHEL — command generation fully implemented
- **Verification**: Post-cleanup `df -h` comparison against pre-cleanup baseline
- **Tests**: 31 tests covering whitelist, validation, routing, assess, execute, verify, sub-graph integration
- Pending: real VM dry-run test (needs infrastructure)

### Phase 1.4: Per-VM Graph
- **vm_graph.py**: Full LangGraph lifecycle — lock → discover → plan → dispatch → check_more → audit → unlock
- **Lock node**: FileLocker.acquire/release with graceful error handling — always releases
- **Discovery node**: detect_os() via SSH — populates VMInfo (OS, disk, docker, packages, uptime)
- **Plan node**: prioritize_actions() with optional LLM, hardcoded fallback
- **Dispatch node**: Action loop with index cursor — currently dispatches disk_cleanup sub-graph, skips others (Phase 2)
- **Audit node**: Writes ActionResult events to AuditStore (SQLite)
- **Tests**: 28 tests covering all nodes, routing, error paths, full integration

### Phase 1.5: Batch Orchestrator
- **graph.py**: Full LangGraph batch graph — init → window → validate_targets → fan_out → run_vm → collect → report
- **Send() fan-out**: Each healthy target dispatched independently via LangGraph `Send()` from conditional edge function
- **Target validation**: SSH connectivity check (echo ok) — partitions into healthy/failed
- **Result aggregation**: Append-only reducer (`Annotated[list, _merge_vm_results]`) for concurrent writes
- **Report generation**: Template-based with optional LLM; `validate_window_node` is a stub (wired to real window check before Phase 1.8)
- **Tests**: 21 tests covering all nodes, routing, fan-out, full integration

### Phase 1.4/1.5 Support: Decisions Module
- **decisions.py**: All three decision functions accept optional `llm_client` — LLM tried first, hardcoded fallback on `None`/failure
  - `prioritize_actions()`: filter by VM state (docker, pending packages) + sort by risk tier
  - `analyze_failure()`: heuristic-based retry/rollback/escalate recommendation
  - `generate_report()`: template-based Slack-ready report, `/no_think` mode with LLM
- **Tests**: 23 tests covering filtering, prioritisation, failure analysis, report generation

### Phase 1.6: Integrations
- **LLM client** (`errander/integrations/llm.py`): Full `LLMClient` — `complete()` with thinking/no_think modes, structured JSON via Pydantic, retry on transient errors, `health_check()`. Wired into `decisions.py` — all three decision functions now accept optional `llm_client` parameter and fall back to hardcoded logic when `None` or LLM unreachable.
- **Slack client** (`errander/integrations/slack.py`): Full `SlackClient` — `post_message()` returns `ts`, `get_reactions()` polls by `ts`, `post_alert()` convenience wrapper. Rate limiting handled with one automatic retry respecting `Retry-After`. All I/O via outbound HTTPS, no inbound webhooks.
- **Approval gate** (`errander/safety/approval.py`): `request_approval()` formats and posts dry-run plan to Slack. `poll_approval()` polls every N seconds — ❌ takes priority over ✅, timeout auto-rejects, transient Slack errors skip the poll without aborting.
- **Prometheus metrics** (`errander/observability/metrics.py`, `tracking.py`): `REGISTRY` with 7 metrics (actions_total, action_duration_seconds, batch_duration_seconds, ssh_errors_total, llm_requests_total, approval_wait_seconds, vm_lock_held_seconds). `start_metrics_server()` launches aiohttp app serving `/metrics` and `/health`. `tracking.py` provides `record_action_result()`, `record_ssh_error()`, `record_llm_outcome()`.
- **Tests**: 74 tests — 23 LLM, 10 Slack client, 21 approval gate, 20 metrics

### Pre-Phase 1.8: Wiring + Entry Point
- **`validate_window_node` wired** (`errander/agent/graph.py`): No longer a stub. Calls `check_window_from_config()` — blocks batch if outside window (sets `error` → short-circuits to `generate_report`). `force=True` bypasses with warning. `build_batch_graph()` now accepts optional `window: MaintenanceWindow | None`.
- **`main.py` implemented**: Full entry point — CLI args, config loading, component wiring (SSH, executor, locker, Slack, LLM), `--run-now` mode, scheduler loop with per-env cron jobs, graceful shutdown on SIGTERM/SIGINT.
- **`EnvironmentSchema`** extended with `maintenance_timezone: str = "UTC"`.
- **`_build_maintenance_window()`** helper parses `"HH:MM-HH:MM"` window strings from inventory.
- **Tests**: 21 graph tests (now 25), 17 main.py tests

### Phase 1.7: Config & Scheduling
- **Maintenance windows** (`errander/scheduling/windows.py`): `is_within_window()` handles normal and overnight windows, timezone-aware via `zoneinfo`. `MaintenanceWindow` dataclass with validation. `check_window_from_config()` convenience wrapper.
- **Scheduler** (`errander/scheduling/scheduler.py`): `MaintenanceScheduler` wraps `AsyncIOScheduler` — `add_maintenance_job()` registers cron-triggered async callbacks, `list_jobs()` summarises registered jobs, `start()`/`stop()` manage lifecycle. Misfire grace: 600s, coalesce enabled.
- **Example configs** (`example/inventory.yaml`, `example/settings.yaml`): Reference configuration files covering production/staging/dev environments with annotated comments.
- **Tests**: 36 tests — 25 windows, 11 scheduler

### vLLM Deployment
- **`deploy/vllm/docker-compose.yml`** — production Docker Compose for vLLM: NVIDIA GPU passthrough, the exact serve command from CLAUDE.md, `restart: unless-stopped`, model volume mount, healthcheck (180s start period for model load), 7-day log retention
- **`deploy/vllm/.env.example`** — all tunable vars: `MODEL_ID`, `HF_TOKEN`, `MAX_MODEL_LEN`, `GPU_MEM_UTIL`, `VLLM_PORT`, `MODEL_CACHE_DIR`
- **`LLMClient.check_endpoint()`** — detailed health check: reachability, model list, test completion with round-trip latency
- **`--check-llm` CLI flag** in `main.py` — `uv run python -m errander --check-llm` prints status, model IDs, and latency without starting the agent

### Dual-Channel Approval (Slack + Web UI)
- **`ApprovalManager`** (`errander/safety/approval.py`): In-memory store for pending approvals. `PendingApproval` dataclass with `asyncio.Event` for signalling. `register()`, `decide()` (idempotent), `wait_for_decision()` (timeout auto-rejects), `get_pending()`, `get_history()`.
- **`await_dual_approval()`**: Races Slack reaction polling against UI button click using `asyncio.wait(FIRST_COMPLETED)`. If Slack post fails, falls back gracefully to UI-only mode. Cancels the slower channel when either decides.
- **`GET /ui/approvals`**: Lists pending approvals with report excerpt and Approve/Reject buttons. Shows recent decision history table. Auto-refreshes every 15s. Red badge count in nav when pending > 0.
- **`POST /ui/approvals/{id}/approve|reject`**: Form submit handler — calls `manager.decide()`, redirects back to list. Returns 503 if manager not connected. Idempotent for unknown batch IDs.
- **Dashboard** updated: new "Pending approvals" card (red highlight when > 0, links to `/ui/approvals`). "Approvals" link added to nav bar across all pages.
- **Fixed `main.py`**: `--env` and `--unknown-env` validation now happens BEFORE the metrics server starts (port binding). Previously, 2 tests failed with port 10048 binding error.
- **Tests**: 50 approval tests (27 unit + 23 UI route tests), all 479 tests passing.

### Playwright UI Tests (25 tests)
- `pytest-playwright` added to dev dependencies, Chromium browser installed
- Server fixture: aiohttp server starts in a background thread with its own event loop + seeded `:memory:` SQLite — one server for all 25 tests
- **Dashboard** (6): page loads, Running status, event count heading, both batches visible, batch link navigates, all nav links present
- **Batch list** (3): page loads, both batches listed, link navigates to detail
- **Batch detail** (8): page loads, event count, completed/failed event types visible, detail text, VM link, back link, nonexistent batch
- **VM history** (6): page loads, event count, action detail, back-to-batch link, VM ID with slash in URL, nonexistent VM
- **Endpoints** (2): `/health` returns "ok", `/metrics` serves Prometheus format

### Web UI (built into aiohttp server)
- Extended `start_metrics_server()` with optional `audit_store` parameter
- 4 new routes on the same port 9090 (no new process, no new port):
  - `GET /ui` — Dashboard: running status, total event count, recent batches table, auto-refresh 30s
  - `GET /ui/batches` — Full batch history table (last 100), each row links to detail
  - `GET /ui/batches/{batch_id}` — All events for one batch with colour-coded event types
  - `GET /ui/vms/{vm_id}` — Full VM history across all batches (vm_id supports slashes e.g. `dev/web-01`)
- Styled with Pico.css (CDN) — zero custom CSS, pure semantic HTML
- Event types colour-coded: green (completed), red (failed), blue (started)
- All pages link to each other: batch → VM, VM → batch, nav bar everywhere
- `web.AppKey` typed key for `audit_store` on aiohttp app (no string key warnings)
- Tests: 415 passing (no new tests — user testing manually)

### SQLite Audit Integration (native, no MCP)
- **`AuditStore.get_events()`** extended with `action_type` filter — all four filters (batch_id, vm_id, event_type, action_type) can be combined freely
- **`AuditStore.get_recent_batches(limit)`** — returns batch summaries: batch_id, started_at, event_count, vm_ids (distinct)
- **`--audit` CLI mode** in `main.py`: `uv run python -m errander --audit [--batch-id X] [--vm-id Y] [--action-type Z] [--event-type T] [--last N] [--batches]`
- **Integration tests** (`tests/safety/test_audit_integration.py`, 21 tests):
  - `action_type` filter correctness (6 tests)
  - `get_recent_batches()` correctness (7 tests)
  - VM graph audit trail written correctly via full graph run with mocked SSH (3 tests)
  - Audit CLI query functions via `run_audit_query()` (5 tests)
- **Tests**: 415 total passing

### Design Review (10 fixes)
- Hardened kernel exclusion (frozenset + fnmatch, reject attempts to weaken)
- Disk cleanup whitelist enforcement in validate node (not just assess)
- Approval gate enforces risk tiers per policy (not blanket auto-approve)
- Rollback architecture: version snapshot before patching, batch rollback on failure
- Backup verify uses NEEDS_MANUAL status (not just SUCCESS/FAILED)
- Docker prune validates docker availability before proceeding
- Log rotation rejects paths outside `/var/log`
- All sub-graphs use ActionStatus enum consistently
- Dispatch handles unknown action types gracefully (ValueError catch)
- Audit integration tests patched for all 5 action types

### Phase 2: All Action Sub-Graphs (complete)
- **Log rotation** (`errander/agent/subgraphs/log_rotation.py`): Path validation → find oversized files → logrotate or manual gzip+truncate → verify. Idempotent via `nothing_to_do`. 28 tests.
- **Docker prune** (`errander/agent/subgraphs/docker_prune.py`): Docker availability check → count dangling images + stopped containers → `docker system prune -af` → verify df. Idempotent. 18 tests.
- **Patching** (`errander/agent/subgraphs/patching.py`): Kernel exclusion (mandatory frozenset + fnmatch) → list upgradable → version snapshot → upgrade → verify versions. Idempotent. 24 tests.
- **Backup verify** (`errander/agent/subgraphs/backup_verify.py`): Read-only — no execute node. Check exists/recent/non-zero for each backup path. Flags MISSING/STALE/EMPTY. 14 tests.
- **VM graph wiring** (`errander/agent/vm_graph.py`): All 5 sub-graphs compiled once and dispatched via `_run_*` helpers. Unknown action types handled gracefully.
- **README.md**: Comprehensive project README with architecture, how-it-works, safety gates, quick start, configuration, observability, vLLM deployment.

### Phase 3: Hardening (complete)

- **Rolling updates** (`errander/agent/graph.py`): Wave-based fleet dispatch. `_partition_into_waves()` splits healthy targets by `rolling_update_percentage`. New graph topology: `validate_targets → prepare_waves → dispatch_wave → run_vm → check_wave_health → (loop|collect)`. Defaults to 100% (single wave) — backward-compatible.
- **Canary logic** (`errander/agent/graph.py`): When `canary_enabled=True`, `prepare_waves_node` forces wave 0 = 1 VM. `check_wave_health_node` uses the stricter `canary_health_check_command` for wave 0; any failure aborts the entire rollout (`canary_passed=False`).
- **Drift detection** (`errander/safety/drift.py`, `errander/agent/vm_graph.py`): New `drift_check` node inserted between `discover` and `plan_actions`. Compares discovered VM state against SQLite-stored baseline (OS version, disk usage >20%, docker availability, reboot detection, package count >5). Saves baseline after each successful run. Disabled by default.
- **New metrics**: `errander_wave_health_checks_total` counter (labeled by wave index and outcome).
- **Settings wired**: 7 new fields in `AgentSettingsSchema` and `Settings` dataclass, env var overrides, and `config/settings.yaml` + `example/settings.yaml` updated.
- **Phase 3 edge-case hardening** (5 steps, 25 new tests, 677 total):
  - **Sub-graph exception safety** (`errander/agent/vm_graph.py`): All 5 `_run_*` helpers wrap `ainvoke()` in try/except — `(ConnectionError, OSError, TimeoutError)` + bare `Exception # noqa: BLE001`. Exceptions return a FAILED result dict; the `release_lock_node` always executes. `audit_results_node` wraps `save_baseline()` so a drift DB error never aborts the batch.
  - **Batch orchestrator exception safety** (`errander/agent/graph.py`): `run_vm_node`'s `ainvoke()` wrapped in bare `Exception` guard returning a FAILED vm_results entry.
  - **Audit resilience** (`errander/safety/audit.py`): `log_event()` retries once on `aiosqlite.OperationalError` (with 100ms backoff), then swallows persistent `OperationalError` and `aiosqlite.Error` so audit failures never abort a live batch.
  - **Atomic file locking** (`errander/safety/locking.py`): `acquire()` uses `os.O_CREAT | os.O_EXCL` for race-free creation; stale-lock overwrites use `os.replace()` (atomic on same filesystem). `_write_lock_atomic()` helper writes to `.tmp` then renames atomically.
  - **Settings bounds validation** (`errander/config/schema.py`): `@field_validator` on `rolling_update_percentage` [1–100], `wave_failure_threshold`/`fleet_failure_threshold` [0.0–1.0], and all timeout fields [1–86400].
  - **SSH hardening** (`errander/execution/ssh.py`): Timeout handler clears stale connection from pool; `None` exit_status maps to 255 (SSH convention).
  - **Sub-graph empty-output guards**: `snapshot_node` (patching) fails on empty package snapshot; `assess_node` (disk_cleanup, docker_prune) fails on empty `df`/`wc -l` stdout; `assess_node` (log_rotation) fails on non-zero `find` exit code.

### Load Testing + Playwright Approvals (complete)
- **`tests/agent/test_load.py`** (20 tests): `TestLargeFleetPartitioning` (7 pure-unit wave math tests at 100–200 VMs), `TestFleetBatchGraph` (7 integration tests — 10-VM fleet, rolling waves, wave abort at boundary, canary abort, crash recovery), `TestConcurrentLockOperations` (6 tests — 50-coroutine race, 20-VM lifecycle, stale lock recovery, force release, serial waves).
- **`tests/ui/test_approvals_playwright.py`** (22 Playwright tests): Module-scoped aiohttp server with `ApprovalManager` pre-seeded with 5 pending approvals. `TestApprovalsPage` (7), `TestApprovalsNavigation` (4), `TestDashboardWithPendingApprovals` (4), `TestApproveAction` (2), `TestRejectAction` (2), `TestApprovalsBadgeAcrossPages` (3).
- **Total tests: 719** — all passing, lint clean.

### Phase 4: LLM Flexibility + Secrets Encryption + UI Config (complete)

- **Phase A — LLM provider flexibility**: Removed hardcoded `Qwen/Qwen3-8B-AWQ` and `/no_think` prefix. `LLMClient` now accepts `model: str` and `temperature: float`. Works with any OpenAI-compatible API (vLLM, Ollama, OpenAI, Anthropic via proxy, Groq). `decisions.py` updated — no more `thinking=True/False`. Provider docs in `docs/LLM-PROVIDERS.md`.

- **Phase A.5 — Secrets encryption foundation**:
  - `SecretsManager` with Fernet AES-128-CBC + HMAC-SHA256, `enc:v1:<token>` format
  - `--generate-secrets-key` and `--encrypt VALUE` CLI commands
  - YAML config decryption on load (`_decrypt_yaml_strings`)
  - `SecretsRedactingFilter` log filter scrubs API keys, Slack tokens, `enc:v1:` blobs from all log output
  - 24 `test_secrets.py`, 9 `test_redaction.py`, 6 `test_secrets_loading.py` tests

- **Phase B — UI settings + inventory management**:
  - `OverridesStore` (SQLite) — two tables: `settings_overrides` and `inventory_overrides`
  - Settings precedence: env > DB (UI) > YAML > default. `load_settings()` accepts pre-fetched `db_overrides`.
  - `GET/POST /ui/settings` — runtime LLM/approval setting changes. Source indicators (env=locked, db=blue, yaml=green). "Test Connection" button validates LLM endpoint.
  - `GET/POST /ui/inventory` — disable YAML VMs or add ad-hoc VMs. Changes take effect on next batch run.
  - HTTP Basic Auth middleware on all `/ui/*` routes (`secrets.compare_digest`, timing-safe)
  - Inventory merge in `run_env_batch()`: YAML → filter disabled → append db_additions
  - All audit-change events logged as `SETTINGS_CHANGED` / `INVENTORY_CHANGED`
  - New tests: 18 `test_overrides.py` (T1), 21 `test_settings_precedence.py` (T2), 9 `test_inventory_merge.py` (T3)
  - Learning doc: `docs/learning/22-ui-settings-and-inventory.md`
  - SETUP.md updated: Step 5b — Secure the Web UI

- **Phase 4 Playwright tests (T4-T6 — 45 tests)**:
  - `tests/ui/test_settings_playwright.py` (15 tests): page load, save+persist, reset, env-var lock / source labels
  - `tests/ui/test_inventory_playwright.py` (17 tests): page load, VM display, toggle, add ad-hoc VM, delete
  - `tests/ui/test_ui_auth_playwright.py` (13 tests): 401 without creds, 200 with creds, wrong user/pass, WWW-Authenticate header, /metrics+/health open
  - **Bug fixed**: Nested `<form>` inside the main settings `<form>` caused Chromium to implicitly close the outer form — Save button ended up orphaned. Fixed via HTML5 `form="reset-{key}"` attribute pattern (out-of-band form + form-attr button).

### Deferred Execution — Window-Gated Approval (complete)

The approval flow is now fully decoupled from execution. A dry-run scan can happen at 10 AM, the operator approves at 1 PM, and live execution only fires when the maintenance window opens (e.g., 11 PM).

- **`errander/models/events.py`**: Added `EXECUTION_DEFERRED` and `DEFERRED_EXECUTION_STARTED` to `EventType`
- **`errander/safety/deferred.py`** (NEW): `DeferredExecutionStore` — SQLite table `deferred_executions`; `save()`, `get_pending()`, `mark_executing()`, `mark_done()`, `expire_old()` (7-day auto-expiry)
- **`errander/scheduling/windows.py`**: Added `next_window_open()` (next future window start, skips current open window) and `window_start_cron()` (converts window config to APScheduler cron string)
- **`errander/agent/graph.py`**: `BatchGraphState` extended with `env_name` and `deferred` fields; `approval_gate_node` defers approved dry-runs made outside window; `build_batch_graph()` accepts `deferred_store`
- **`errander/main.py`**: `DeferredExecutionStore` initialised alongside `AuditStore`; `_window_opener()` function executes pending deferred batches at window start; window-opener cron jobs registered per environment; `env_name` threaded into initial batch state
- **Tests**: 34 new tests — `tests/safety/test_deferred.py` (15), `tests/scheduling/test_windows.py` (+9), `tests/agent/test_graph.py` (+6), `tests/test_main.py` (+3 `_window_opener` tests)
- **Total: 878 tests passing**

## In Progress
- Nothing actively in flight.

## Next Up
- Phase 3: Honest AI integration (finding #1)
  - 3.1 Thread LLMClient into build_batch_graph → build_vm_graph → plan_actions_node
  - 3.2 Constrained plan schema — LLM returns action selections over fixed vocabulary; Pydantic validated
  - 3.3 AI eval harness — golden VM states → expected plan shapes; injection corpus; schema-violation corpus
  - 3.4 Per-decision AI audit table
- Phase 4: E2E verification (staging soak, chaos suite, Windows test infra fix)



## Decisions Made
- **LangGraph Send() pattern**: `Send()` objects must come from conditional edge routing functions, NOT from nodes. Nodes return dicts. Routing functions return strings or `list[Send]`. Discovered via `InvalidUpdateError` during Phase 1.5.
- **Pre-compiled VM graph**: The per-VM graph is compiled once in `make_fan_out_router()` and reused for all fan-out invocations via closure. Avoids N graph compilations.
- **Routing-only nodes**: `check_more_actions` is a pass-through node (returns `{}`) that exists only to give the conditional edge a named source. LangGraph requires conditional edges to be attached to nodes.
- **State serialisation at boundaries**: Sub-graph states use TypedDict; the VM graph stores results as `list[dict]`. ActionResult objects are serialised when written, deserialised when read (e.g., report generator). Avoids Pydantic/dataclass serialisation across graph boundaries.
- **Hardcoded fallbacks first**: All LLM-powered functions (`prioritize_actions`, `generate_report`, `analyze_failure`) are fully implemented with hardcoded logic before LLM integration. The agent is fully functional without LLM.
- **Module-scoped test fixtures for expensive clients**: `AsyncOpenAI` initialises an httpx transport (~1.4s). Using `pytest.fixture(scope="module")` reduces LLM test suite from 57s to 6.5s.
- **Custom Prometheus registry**: Using `CollectorRegistry()` instead of the library default for test isolation and explicit ownership — `generate_latest(REGISTRY)` only outputs Errander-AI metrics.
- **Scheduler does not enforce windows**: The APScheduler cron triggers runs at the configured time; the graph's `validate_window_node` is the authoritative safety gate. Separates scheduling concerns from safety concerns.
- **Outbound-only Slack**: No webhooks, no inbound endpoints, no nginx. Agent polls `reactions.get` every 30s. Zero infra overhead for approval flow.
- **Web UI on same port as /metrics**: The `/ui` routes run on the same aiohttp server as `/metrics` and `/health` (port 9090). No new process, no new port, direct in-process access to `AuditStore`. Separate server warranted only if UI needs auth or WebSockets.
- **`web.AppKey` for typed app data**: Used `web.AppKey[AuditStore | None]` to store the audit store on the aiohttp app. Silences `NotAppKeyWarning` and gives the type checker a typed handle — avoids string key collisions.
- **Pico.css classless via CDN**: Zero custom CSS — Pico.css styles standard semantic HTML elements without class names. One `<link>` tag is the entire styling solution for Phase 1.
- **Native SQLite queries, not MCP**: SQLite query capability built directly into `AuditStore` and `main.py --audit` mode. No external MCP server needed — the agent owns its own audit data and can query it natively.
- **`GROUP_CONCAT(DISTINCT vm_id)`**: Used for `get_recent_batches()` — SQLite supports this natively, deduplicates VM IDs per batch in a single query without a subquery.
- **Docker Compose for production vLLM**: Single-VM GPU deployment via Docker Compose is production-grade — reproducible environment, `restart: unless-stopped`, model weights cached on host volume. Bare metal adds no meaningful benefit for a single dedicated T4 VM.
- **`asyncio.wait(FIRST_COMPLETED)` for dual-channel racing**: `await asyncio.wait({slack_task, ui_task}, return_when=FIRST_COMPLETED)` is the right primitive — cleaner than `asyncio.gather` with cancellation tokens or manual flags. The losing task is explicitly cancelled with `await t` to drain any pending cleanup.
- **`asyncio.Event()` as the signalling primitive**: `PendingApproval._event` is set by `decide()` and waited on by `_wait_ui()`. No queues, no locks — the event is the direct channel between the HTTP handler coroutine and the approval waiter. Works because all coroutines share the same event loop.
- **Idempotent `decide()`**: Uses `self._pending.pop(batch_id, None)` — returns None silently if already decided. This makes dual-channel racing safe: the slower channel can call `decide()` after the faster one without raising.
- **Canary as wave 0**: Canary is not a separate mechanism — it's just wave 0 with exactly 1 VM and a stricter health check command. `prepare_waves_node` inserts the canary target as `waves[0]` before the percentage-based remaining waves. Zero new nodes or state machines.
- **Drift stored as audit events**: Baselines are stored as `DRIFT_BASELINE_SAVED` events in the existing SQLite audit trail (JSON blob in metadata). No new table, no schema migration. Queried via `get_events(vm_id=..., event_type=..., limit=1)` — most recent entry is the current baseline.
- **`dispatch_wave` as no-op node**: In the wave-based graph, `dispatch_wave` is a pass-through node (`lambda state: {}`) whose only role is to be the named source for the conditional edge that emits `Send()` objects. The routing function does the real work. Same pattern as `check_more_actions` in the VM graph.
- **`make_fan_out_router` kept for backward compat**: New `make_wave_dispatcher` handles production use. `make_fan_out_router` still exists and is still imported in tests — removing it would break existing test assertions without any benefit.
- **Port bind before validation caused test failures**: `start_metrics_server()` was called before `--env` validation checks. When two `async_main` calls ran in the same process (test suite), the second bind on port 9090 failed. Fix: validate `--env` / unknown env BEFORE creating the audit store or starting the server.
- **`load_settings()` stays synchronous (Phase 4)**: Accepts pre-fetched `db_overrides: dict[str, str]` instead of `OverridesStore` directly — keeps the sync call chain intact and makes testing simple.
- **`enc:v1:` prefix for encrypted values (Phase 4)**: Prefix-tagged format makes it easy to detect encrypted vs plaintext at any layer (env var, YAML, DB) without needing a separate `is_secret` flag.
- **Basic Auth on `/ui/*` only (Phase 4)**: `/metrics` and `/health` remain open (Prometheus scrapers don't support auth). Auth scoped to human-facing routes only.
- **Inventory merge in `run_env_batch()` (Phase 4)**: Merge happens at batch invocation time — operators can change inventory via UI and the next scheduled run picks it up without restart.

## Blockers
None.

## Files Changed (2026-05-10 — fix MasterKeyMissingError in --check-llm)
### Modified
- `scripts/configure.sh` — LLM verify call now passes `ERRANDER_SECRETS_KEY` inline alongside the other env vars
- `errander/main.py` — moved `--generate-secrets-key`, `--encrypt`, `--check-inventory` before `load_settings()`; wrapped `load_settings()` in try/except for `MasterKeyMissingError` with a clear actionable error message

## Files Changed (2026-05-10 — configure.sh set -e grep fixes)
### Modified
- `scripts/configure.sh` — added `|| true` to all bare `grep` calls inside `$()` subshells; `set -euo pipefail` was silently killing the script when `grep` found no match (exit 1 treated as fatal)

## Files Changed (2026-05-10 — --check-inventory CLI flag)
### Modified
- `errander/main.py` — added `--check-inventory` flag + `run_inventory_check()`: validates inventory.yaml and prints env/target summary; wired into `async_main` early-exit path
- `scripts/configure.sh` — Step 6 verify command replaced with `uv run python -m errander --check-inventory` (was a 200-char one-liner that wrapped in terminals and broke on copy-paste)

## Files Changed (2026-05-10 — SECRETS.md key rotation docs)
### Modified
- `docs/SECRETS.md` — added `ERRANDER_UI_PASSWORD` to `.env` example; split key rotation into two sections (old key available vs. key lost); added per-variable notes explaining runtime behaviour

## Files Changed (2026-05-10 — SETUP.md continued)

### Modified
- `SETUP.md` — Step 2: rewrote SSH key section with diagram and Master VM / Target VM labels on every substep; Step 3: added backup → visudo validate → rollback safety sequence, labeled all substeps (Target VM); Step 4: full rewrite — decision table, Azure Foundry as first featured option, verify step per option, Master VM labels throughout; merged old Step 5 (Slack) into Step 6 as a subsection — Step 5 is now the single "Configure the agent" step with Slack as an optional sub-section at the bottom; steps 6-10 renumbered to 5-9; added Steps 4-6 quick path section (configure.sh one-liner); Windows Step 1 rewritten to use bootstrap.ps1 one-liner (was manual steps)
- `scripts/bootstrap.ps1` — new Windows bootstrap script: installs git via winget, uv via official PowerShell installer, Python 3.12 via uv, clones repo, runs uv sync, verifies import. No admin required.
- `scripts/configure.sh` — new interactive setup script: prompts for LLM, VMs, SSH key, Slack; writes .env + inventory.yaml; verifies LLM connection
- `CLAUDE.md` — expanded doc sync rule to two tiers: always-update (STATUS, command-log, todo, lessons) and update-when-relevant (SETUP, README, RUN, learning docs, etc.)
- `README.md` — fixed hardcoded Qwen3/vLLM references → generic; test count 587 → 878; V2 roadmap removed already-shipped Phase 3 items; Quick Start fixed clone URL + directory; added configure.sh reference
- `tasks/phase-1.8-validation-checklist.md` — updated stale commit hash to aa32f48

## Files Changed (2026-05-09 — E2E Validation Prep + Docs)

### Created
- `scripts/bootstrap.sh` — distro-agnostic bootstrap script (Ubuntu/Debian/RHEL/CentOS/Oracle/Fedora): detects pkg manager, installs git + curl + uv + Python 3.12, clones repo, runs uv sync, verifies import
- `.gitattributes` — enforce LF line endings for .sh, .py, .yaml, .md files

### Modified
- `SETUP.md` — major overhaul: added Prerequisites section (software, network ports table, Azure NSG note for port 9090, SSH tunnel alternative); updated architecture diagram to reflect Azure VNet topology; fixed git clone URL placeholders; marked Step 5 (Slack) as optional with web UI fallback; updated .env templates (added ERRANDER_LLM_MODEL, commented out Slack, added UI auth); fixed env var table (Slack Required: Yes → No); replaced Linux Step 1 manual commands with bootstrap script one-liner; fixed Python 3.12 apt install for Ubuntu 22.04; fixed private repo bootstrap (clone first, then run script)
- `CLAUDE.md` — added commit message format rule (one line, type: description, under 72 chars)

## Files Changed (2026-04-27 — Deferred Execution)

### Modified
- `errander/models/events.py` — added `EXECUTION_DEFERRED`, `DEFERRED_EXECUTION_STARTED` to `EventType`
- `errander/scheduling/windows.py` — added `next_window_open()`, `window_start_cron()`, `_CRON_DAY_ABBR` map
- `errander/agent/graph.py` — `BatchGraphState` extended; `approval_gate_node` with deferred logic; `build_batch_graph()` new `deferred_store` param; imports updated
- `errander/main.py` — `DeferredExecutionStore` import + init; `deferred_store` param in `run_env_batch()`; `_window_opener()` function; window-opener cron job registration; `env_name` in initial state; `deferred_store.close()` in finally
- `tests/scheduling/test_windows.py` — 9 new tests for `next_window_open` and `window_start_cron`
- `tests/agent/test_graph.py` — 6 new `TestApprovalGateDeferred` tests
- `tests/test_main.py` — 3 new `TestWindowOpener` tests; `SSHConnectionManager` import added
- `docs/SETUP.md` — updated test count to 878 (from SETUP.md step 6)
- `config/inventory.yaml` — approval_policy strict for all envs
- `example/inventory.yaml` — approval_policy strict for all envs
- `errander/models/actions.py` — Docker prune risk tier raised from LOW to MEDIUM
- `CLAUDE.md` — Risk Tiers table updated (Docker prune → Medium)

### Created
- `errander/safety/deferred.py` — `DeferredExecutionStore` + `DeferredExecution` dataclass
- `tests/safety/test_deferred.py` — 15 tests for `DeferredExecutionStore`
- `docs/learning/24-deferred-execution.md` — learning doc

## Files Changed (2026-05-10 — configure.sh UX fix)
### Modified
- `scripts/configure.sh` — moved "Do you want to add target VMs?" prompt before section header on fresh install; section header only shown after user confirms; re-run path (existing inventory.yaml) unchanged
- `scripts/configure.sh` — suppress `[3/5] SSH key pair` step header when key already exists; show single ok line instead
- `scripts/configure.sh` — removed SSH key generation entirely; script now only verifies key exists and points to SETUP.md Step 2 if missing; banner updated to reflect verify-only behaviour
- `scripts/configure.sh` — split combined "Keep existing VMs and just add more?" into two separate prompts: "Keep these VMs? (Y/n)" and "Add more VMs? (y/N)"
- `scripts/configure.sh` — removed stale "Complete SETUP.md Steps 2-3" reminder from final summary; replaced with direct "Next — run a dry-run:" line
- `scripts/configure.sh` — final summary now shows Step 6 (verify inventory + pytest) before Step 7 (dry-run), matching SETUP.md order
- `scripts/configure.sh` — Step 7 dry-run command now includes `--force --force-reason "initial dry-run validation"` to bypass maintenance window on first run
- `SETUP.md` — Step 7 commands updated with `--force --force-reason`; added note explaining `--force` bypasses the window for first-run validation
- `errander/agent/graph.py` — approval gate now auto-approves dry-run batches immediately; approval only required for live runs with HIGH/CRITICAL risk tier

## Files Changed (2026-05-10 — configure.sh + SETUP.md 9-bug audit)
### Modified
- `scripts/configure.sh` — A: fixed fresh install Enter default (added `_add_vms="${_add_vms:-y}"`)
- `scripts/configure.sh` — B: fixed "keep + add more" silently dropping new VMs (append TARGETS_YAML when KEEP_INVENTORY=true)
- `scripts/configure.sh` — C: fixed re-run resetting UI password (read existing creds from .env before writing)
- `scripts/configure.sh` — C: added warning when UI password is still 'changeme'
- `scripts/configure.sh` — D: improved SSH key missing message — explicit "setup is incomplete, re-run after creating key"
- `SETUP.md` — E: Step 7 and Step 8 `--env dev` → `--env <your-env-name>` with substitution note
- `SETUP.md` — F: Azure Foundry URL fixed from `openai.azure.com` → `cognitiveservices.azure.com`
- `SETUP.md` — G: systemd service rewritten to use `$(whoami)` and `$(pwd)` — no more hardcoded `errander` user
- `SETUP.md` — H: quick path description updated — "SSH key" → "verify your SSH key path"
- `SETUP.md` — I: added password change warning in Web UI section and `.env` template comment

## Files Changed (2026-05-10 — secrets hardening + UI credential prompt)
### Modified
- `scripts/configure.sh` — `chmod 600 .env` applied on every write (was missing entirely)
- `scripts/configure.sh` — optional Fernet encryption: generates key to `~/.errander.key` (chmod 600, separate from .env), encrypts LLM_API_KEY / UI_PASSWORD / SLACK_BOT_TOKEN as `enc:v1:` blobs; re-run safe (already-encrypted values passed through unchanged)
- `scripts/configure.sh` — web UI username + password prompted explicitly on fresh install (with confirmation loop); re-run shows existing values as defaults; `changeme` can never silently reach production
- `scripts/configure.sh` — encryption key auto-wired: exported into current session, appended to `~/.bashrc`/`~/.zshrc` (idempotent), and injected into systemd service EnvironmentFile if service already installed — no manual steps required
- `scripts/bootstrap.sh` — completion message corrected: step numbers updated, configure.sh quick path surfaced

## Files Changed (2026-05-10 — fix --check-llm decrypts enc:v1: API key)
### Modified
- `errander/main.py` — `run_llm_check()` now runs LLM env vars through `SecretsManager.decrypt_if_needed()` so encrypted API keys (enc:v1:...) are decrypted before use; previously the raw ciphertext was sent to the LLM provider causing 401

## Files Changed (2026-05-10 — fix --check-llm needs env vars in Step 6)
### Modified
- `scripts/configure.sh` — Step 6 output: removed `--check-llm` (configure.sh already ran it); replaced with note "(LLM already verified above)"
- `SETUP.md` — Step 6: `--check-llm` moved to optional re-verify block with explicit `source ~/.errander.key` + `export .env` instructions before it

## Files Changed (2026-05-10 — separate end-user and developer setup steps)
### Modified
- `scripts/configure.sh` — Step 6 output trimmed to end-user steps only: `--check-inventory` and `--check-llm`
- `scripts/bootstrap.sh` — reverted to bare `uv sync` (no `--extra dev`, no playwright — dev tools not needed for deployment)
- `SETUP.md` — Step 6 is now end-user only (inventory check + LLM check); pytest/playwright/ruff/mypy moved to new "For developers" section at the bottom

## Files Changed (2026-05-11 — Phase 2 policy enforcement + fleet safety)

### Modified (source)
- `errander/models/events.py` — added `FLEET_ABORT` and `OS_MISMATCH` to `EventType`
- `errander/safety/validators.py` — `validate_action` now uses `get_policy()`/`requires_approval()`; CRITICAL reason includes policy name; removed "unused" docstring note
- `errander/agent/vm_graph.py` — `VMGraphState.env_policy` field added; passed to `validate_action` in `dispatch_action_node`
- `errander/agent/graph.py` — `check_fleet_health_node` between validate_targets and plan fan-out; `route_after_fleet_check`; `validate_targets_node` replaces `echo ok` with `cat /etc/os-release` + `parse_os_release()` + `verify_os_match()`; OS_MISMATCH audit events; `env_policy` threaded into Send payloads; `plan_vms` no-op node as fan-out entry; `check_fleet_health` node wired in graph

### Modified (tests)
- `tests/safety/test_audit.py` — `test_all_event_types_stored` uses dynamic limit
- `tests/agent/test_graph.py` — `validate_targets` tests updated to mock os-release response
- `tests/agent/test_load.py` — `_ssh_ok()` default stdout is valid os-release; SSH call counts updated for validate (1 os-release) + plan_vm (5 detect_os) pattern

### Created (tests)
- `tests/agent/test_phase2_policy.py` — 21 tests: 5 policy validation, 8 fleet abort, 8 OS verification

## Files Changed (2026-05-11 — Phase 1 security hardening)

### Created
- `errander/execution/command_builder.py` — `safe_path`, `safe_pkg`, `safe_ver`, `pkg_version_spec`, `build_cmd`; `CommandBuildError`
- `tests/execution/test_command_builder.py` — 22 tests; injection corpus covering `;`, `$()`, backtick, `|`, `>`, null byte, spaces
- `tests/execution/test_ssh_host_keys.py` — 6 tests for known_hosts modes (strict, TOFU, missing config)
- `tests/agent/subgraphs/test_docker_prune_scope.py` — 4 tests for dangling-only vs aggressive prune
- `tests/observability/test_ui_security.py` — 7 tests for bind address enforcement, CSRF middleware, CSRF injection helper

### Modified (source)
- `errander/execution/ssh.py` — `SSHConnectionManager.__init__` accepts `known_hosts_path`/`strict_host_keys`; `_connect` enforces three modes (verified/TOFU/refuse); TOFU logs WARNING per connection
- `errander/execution/commands.py` — `AptManager.upgrade_all` uses dpkg-query + Python filter + exact hold names (no glob apt-mark); `DnfManager.upgrade_all` uses rpm + dnf versionlock; both `list_installed_versions` / `install_version` use `safe_pkg`/`safe_ver`
- `errander/agent/subgraphs/backup_verify.py` — `assess_node` uses `safe_path()`; unsafe paths skipped with error logged
- `errander/agent/subgraphs/log_rotation.py` — manual rotation f-strings replaced with `safe_path()`; unsafe paths skipped
- `errander/agent/subgraphs/docker_prune.py` — `DockerPruneGraphState.docker_prune_aggressive` field; `execute_node` defaults to dangling-only commands; aggressive=True uses `system prune -af`
- `errander/safety/rollback.py` — `shlex.quote` replaced with `pkg_version_spec()` from command_builder
- `errander/config/settings.py` — `ssh_known_hosts_path`, `ssh_strict_host_keys`, `ui_bind_address` fields + env var loading
- `errander/observability/metrics.py` — `bind_address` param; mandatory auth guard on non-loopback; `_CSRF_SECRET_KEY` AppKey; `_csrf_middleware`, `_csrf_verify`, `_inject_csrf`, `_re_inject_csrf` helpers; CSRF middleware wired into app
- `errander/main.py` — `SSHConnectionManager` constructed with `known_hosts_path`/`strict_host_keys` from settings; `--bootstrap-known-hosts <env>` CLI; `run_bootstrap_known_hosts()` function; `start_metrics_server` called with `bind_address`

## Files Changed (2026-05-11 — Phase 0 SRE audit remediation)

### Modified (source)
- `errander/agent/graph.py` — new plan/apply flow: `plan_vm` fan-out, `collect_plans`, `generate_plan_artifact`, `approval_gate` before execution; ImmutableBatchPlan with SHA-256 hash; deferred logic inverted (live runs outside window defer, dry-run always immediate); `_route_plan_vms` fan-out; `vm_plans` reducer
- `errander/agent/subgraphs/patching.py` — `execute_node` reads `dry_run` from state; `rollback_node` with real dpkg rollback; `route_after_execute` routes FAILED → rollback; graph wired with rollback node
- `errander/agent/subgraphs/disk_cleanup.py` — `execute_node` reads `dry_run` from state, passes per-call override to `executor.execute()`
- `errander/agent/subgraphs/docker_prune.py` — same `dry_run` state read fix
- `errander/agent/subgraphs/log_rotation.py` — same `dry_run` state read fix
- `errander/execution/sandbox.py` — `execute()` accepts `dry_run: bool | None = None` per-call override; `effective_dry_run` logic
- `errander/main.py` — `--unsafe-legacy-live` guard blocks live mode until Phase 0 complete
- `errander/models/plans.py` — `ImmutablePlan` dataclass with SHA-256 `plan_hash` and `short_hash()`
- `errander/safety/audit.py` — `AuditWriteError`, `strict_mode: bool = True`, `log_event(dry_run=False)` fail-closed in strict mode
- `errander/safety/rollback.py` — full Option A patching rollback: dpkg snapshot → apt-get --allow-downgrades → verify versions
- `errander/config/settings.py` — `audit_mode: str = "strict"` field

### Modified (tests)
- `tests/agent/subgraphs/test_disk_cleanup.py` — `capture_execute` mock updated with `dry_run` param
- `tests/agent/subgraphs/test_patching.py` — `test_route_after_execute_finishes_on_failure` → `test_route_after_execute_routes_failure_to_rollback`
- `tests/agent/test_graph.py` — 4 deferred tests updated to reflect new behavior (dry-run never deferred; live outside window IS deferred)
- `tests/agent/test_load.py` — wave abort SSH mock count updated (12 validate + 12 plan_vm + 3 health = 27)
- `tests/safety/test_audit.py` — swallow tests use `dry_run=True` (best-effort mode)
- `tests/safety/test_rollback.py` — patching rollback tests updated to reflect implemented behavior

## Files Changed (2026-05-10 — fix SETUP.md Step 6: remove env export before pytest, add sync/playwright)
### Modified
- `SETUP.md` — Step 6 rewritten: removed `export $(grep -v '^#' .env | xargs)` (poisons pytest), replaced long one-liner with `--check-inventory`, added `uv sync --extra dev` + `playwright install chromium` steps, added warning note; Step 7 Linux/Windows blocks aligned — both now show the load-env step explicitly before `--run-now`

## Files Changed (2026-05-10 — fix test failures on VM: stale dates, env leakage, Playwright)
### Modified
- `tests/safety/test_deferred.py` — WINDOW_START changed from hardcoded 2026-04-26 to `now+30d`; expiry_at was already in the past on the VM, causing get_pending() to return nothing
- `tests/test_main.py` — same fix for two TestWindowOpener tests using `datetime(2026, 4, 27, ...)`
- `tests/conftest.py` — added autouse fixture `clean_errander_env` that clears all ERRANDER_* env vars before each test; prevents real .env values exported to shell from polluting settings/secrets tests
- `scripts/bootstrap.sh` — added `uv run playwright install chromium` after uv sync so browser binary is available for UI tests
- `scripts/configure.sh` — added `playwright install chromium` line to Step 6 verify instructions
### Created
- `tests/ui/conftest.py` — `pytest_collection_modifyitems` hook that skips all UI tests with a clear message when Chromium binary is absent, instead of ERRORing

## Files Changed (2026-05-10 — add --extra dev to uv sync in bootstrap and docs)
### Modified
- `scripts/bootstrap.sh` — `uv sync` → `uv sync --extra dev` so pytest/ruff/mypy are installed during bootstrap
- `scripts/configure.sh` — Step 6 output now includes `uv sync --extra dev` as the first verify command
- `SETUP.md` — both manual-clone code blocks updated to `uv sync --extra dev`

## Files Changed (2026-05-10 — move --check-llm before load_settings)
### Modified
- `errander/main.py` — `run_llm_check()` now reads LLM env vars directly (no Settings param); moved before `load_settings()` in `async_main` so a decryption error in `ERRANDER_UI_PASSWORD` never blocks LLM connectivity verification
- `docs/learning/13-vllm-setup.md` — updated code snippet to reflect new early-exit placement

## Files Changed (2026-05-10 — fix DecryptionError on configure.sh re-run)
### Modified
- `scripts/configure.sh` — reuse existing `~/.errander.key` on re-run instead of generating a new key; new key generated only when the file is absent; prevents `enc:v1:` blobs in `.env` becoming unreadable after re-run
- `errander/integrations/secrets.py` — improved `DecryptionError` message to explain the key-mismatch cause and tell the user to re-run configure.sh and re-enter the affected secret
- `tasks/lessons.md` — added lesson: configure.sh must reuse existing key, not regenerate on every run

## Files Changed (2026-05-10 — patching: run apt-get update before listing upgrades)
### Modified
- `errander/execution/commands.py` — added `refresh_package_lists()` abstract method to `PackageManager`; `AptManager` returns `apt-get update -qq`, `DnfManager` returns `dnf makecache --quiet 2>/dev/null || true`
- `errander/agent/subgraphs/patching.py` — `assess_node` now calls `refresh_package_lists()` before `list_upgradable()`; refresh failure is non-fatal (logs warning, continues with stale index)
- `tests/agent/subgraphs/test_patching.py` — all `assess_node` and integration tests updated to mock refresh call (now 2 executor calls in assess: refresh + list); 34/34 passing

## Files Changed (This Session)
### Modified
- `errander/agent/decisions.py` — LLM wired in: all decision functions accept optional llm_client, fall back to hardcoded
- `errander/integrations/llm.py` — Full LLMClient implementation
- `tests/integrations/test_llm.py` — 23 tests
- `errander/agent/vm_graph.py` — Full per-VM graph implementation
- `errander/agent/graph.py` — Full batch orchestrator implementation
- `tests/agent/test_decisions.py` — 23 tests (updated for new llm_client signature)
- `tests/agent/test_vm_graph.py` — 28 tests
- `tests/agent/test_graph.py` — 21 tests
- `errander/main.py` — full entry point implementation
- `errander/agent/graph.py` — validate_window_node wired + build_batch_graph accepts window
- `errander/config/schema.py` — EnvironmentSchema.maintenance_timezone field added
- `example/inventory.yaml` — maintenance_timezone field added to all environments
- `tests/agent/test_graph.py` — 4 new window node tests (25 total)
- `tests/test_main.py` — 17 tests for CLI parsing and helper functions
- `errander/scheduling/windows.py` — is_within_window, MaintenanceWindow dataclass
- `errander/scheduling/scheduler.py` — MaintenanceScheduler wrapping AsyncIOScheduler
- `tests/scheduling/test_windows.py` — 25 tests
- `tests/scheduling/test_scheduler.py` — 11 tests
- `example/inventory.yaml` — annotated reference inventory
- `example/settings.yaml` — annotated reference settings
- `errander/integrations/slack.py` — Full SlackClient implementation
- `errander/safety/approval.py` — request_approval + poll_approval
- `errander/observability/metrics.py` — Prometheus registry + HTTP server
- `errander/observability/tracking.py` — record_action_result, record_ssh_error, record_llm_outcome
- `tests/integrations/test_slack.py` — 10 tests
- `tests/safety/test_approval.py` — 21 tests
- `tests/observability/test_metrics.py` — 20 tests
- `docs/SETUP.md` — full setup guide: prerequisites, vLLM, SSH, Slack, config, first run, systemd service, monitoring, troubleshooting
- `docs/learning/13-vllm-setup.md` — learning doc: GPU passthrough, host volumes, healthcheck start_period, check_endpoint design
- `deploy/vllm/docker-compose.yml` — production vLLM container with GPU passthrough
- `deploy/vllm/.env.example` — configurable deployment vars
- `errander/integrations/llm.py` — check_endpoint() method with model list + latency
- `errander/main.py` — --check-llm flag + run_llm_check()
- `errander/observability/metrics.py` — UI routes + handlers, typed AppKey, AuditStore import
- `docs/learning/12-web-ui.md` — learning doc: AppKey, slash URL matching, same-server architecture, Pico.css
- `errander/main.py` — pass audit_store to start_metrics_server()
- `errander/safety/audit.py` — action_type filter in get_events(), get_recent_batches() method added
- `errander/main.py` — --audit CLI mode, run_audit_query(), EventType import
- `tests/safety/test_audit_integration.py` — 21 integration tests (created)
- `docs/learning/11-sqlite-audit.md` — learning doc: GROUP_CONCAT aggregation, CLI short-circuit pattern, integration test strategy
- `tasks/todo.md` — Phases 1.4/1.5/1.6/1.7 items checked off; Phase 4 tasks added and checked off
- `docs/command-log.md` — Phase 1.6 + 1.7 + 4 commands added
- `tasks/lessons.md` — aiohttp async CM, rate-limit retry, APScheduler __slots__, DST offset, web.Response, Phase 4 lessons added
- `tasks/phase4-llm-flexibility-and-ui-config.md` — status updated to Complete

### Phase 4 — Modified
- `errander/integrations/llm.py` — removed hardcoded model/thinking-mode, added model+temperature params
- `errander/integrations/secrets.py` — rewritten: SecretsManager with Fernet enc:v1: format
- `errander/config/schema.py` — added _decrypt_yaml_strings, LLMSettingsSchema.model+temperature, validators
- `errander/config/settings.py` — added llm_model, llm_temperature, ui_user, ui_password, sources, db_overrides param
- `errander/agent/decisions.py` — removed thinking= kwarg from all complete() calls
- `errander/main.py` — added --generate-secrets-key, --encrypt flags; OverridesStore init; inventory merge; overrides_store wired to scheduler loop
- `errander/observability/metrics.py` — Basic Auth middleware, /ui/settings and /ui/inventory routes + handlers
- `errander/models/events.py` — added SETTINGS_CHANGED and INVENTORY_CHANGED to EventType
- `config/settings.yaml` — added llm.model and llm.temperature fields
- `example/settings.yaml` — added llm.model and llm.temperature fields
- `tests/integrations/test_llm.py` — rewritten: removed thinking tests, added verbatim/temp/model tests
- `docs/SETUP.md` — added Step 5b: Secure the Web UI
- `docs/learning/README.md` — added entries 20, 21, 22

### Phase 4 — Created
- `errander/safety/overrides.py` — OverridesStore: settings_overrides + inventory_overrides SQLite tables
- `errander/observability/redaction.py` — SecretsRedactingFilter log filter
- `tests/integrations/test_secrets.py` — 24 SecretsManager tests
- `tests/observability/test_redaction.py` — 9 redaction filter tests
- `tests/config/test_secrets_loading.py` — 6 YAML/env decryption integration tests
- `tests/safety/test_overrides.py` — 18 OverridesStore tests (T1)
- `tests/config/test_settings_precedence.py` — 21 settings precedence tests (T2)
- `tests/agent/test_inventory_merge.py` — 9 inventory merge tests (T3)
- `docs/learning/22-ui-settings-and-inventory.md` — learning doc: precedence chain, DB schema, merge algo, Basic Auth
- `docs/LLM-PROVIDERS.md` — provider config reference (vLLM, Ollama, OpenAI, Anthropic, Groq)
- `docs/SECRETS.md` — encryption setup guide, threat model, key rotation

## Decisions Made (Phase 4)
- **`load_settings()` stays synchronous**: Accepts pre-fetched `db_overrides: dict[str, str]` instead of `OverridesStore` directly — keeps the sync call chain intact and makes testing simple.
- **`enc:v1:` prefix for encrypted values**: Prefix-tagged format makes it easy to detect encrypted vs plaintext at any layer (env var, YAML, DB) without needing a separate `is_secret` flag.
- **Basic Auth on `/ui/*` only**: The `/metrics` and `/health` endpoints remain open (Prometheus scrapers don't support auth by default). Auth is scoped to the human-facing routes.
- **`secrets.compare_digest()` for password check**: Constant-time comparison prevents timing oracle attacks — critical for a network-exposed auth check.
- **Inventory merge in `run_env_batch()`**: The merge happens at batch invocation time, not at startup — operators can change inventory via the UI and the very next scheduled run picks it up without restart.
- **`_name` temporary field pattern**: YAML target dicts get `_name` injected for filter lookup, then `del`-ed before the list reaches the graph. Avoids passing unknown fields into graph state.

## Test Count
878 tests passing (853 unit/integration + 25 Playwright UI tests).

### Phase 0: SRE Audit Remediation (complete)

Implemented all Phase 0 fixes from `ai_sre_remediation_plan.md`:

- **Finding #2 (dry_run single source of truth)**: `SandboxExecutor.execute()` now accepts per-call `dry_run` override. All sub-graphs read `state["dry_run"]` instead of `executor.dry_run`.
- **Finding #3 (plan/apply before execution)**: New planning phase fan-out (`plan_vm` → `collect_plans` → `generate_plan_artifact`) between `validate_targets` and execution. Approval gate acts on the plan hash BEFORE any execution. `ImmutablePlan` with SHA-256 `plan_hash`.
- **Finding #3 (hash re-verification)**: `verify_plan_hash_node` re-computes SHA-256 from current state at execution time. Any drift between approval and execution aborts the batch and routes to `generate_report`. Wired between `approval_gate` and `prepare_waves`.
- **Finding #5 (patching rollback — Option A)**: `rollback_node` in patching sub-graph implements real dpkg snapshot + `apt-get install --allow-downgrades` + post-rollback verification. Activated on `FAILED` execution status.
- **Finding #6 (policy-based approval thresholds)**: `env_policy` threaded from `EnvironmentSchema.approval_policy` → `initial_state` → `BatchGraphState`. `approval_gate_node` now enforces: strict = MEDIUM/HIGH/CRITICAL require approval; moderate = HIGH/CRITICAL; relaxed = CRITICAL only.
- **Finding #13 (audit fail-closed)**: `AuditWriteError` raised after retry exhaustion in strict mode for live actions. Dry-run always best-effort.
- **Phase 0 gate**: `--unsafe-legacy-live` guard blocks live mode until Phase 0 is marked complete.

All 787 unit/integration tests pass (111 skipped = Playwright UI tests, excluded without Chromium). Includes 20 new `test_plan_apply_flow.py` tests.
