# AutoMaint — Project Status

## Last Updated
2026-04-10

## Current Phase
**Dual-channel approval complete (Slack reactions + Web UI buttons). 479 tests passing. Next: Phase 1.8 End-to-End Validation (needs real VM).**

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
- **LLM client** (`automaint/integrations/llm.py`): Full `LLMClient` — `complete()` with thinking/no_think modes, structured JSON via Pydantic, retry on transient errors, `health_check()`. Wired into `decisions.py` — all three decision functions now accept optional `llm_client` parameter and fall back to hardcoded logic when `None` or LLM unreachable.
- **Slack client** (`automaint/integrations/slack.py`): Full `SlackClient` — `post_message()` returns `ts`, `get_reactions()` polls by `ts`, `post_alert()` convenience wrapper. Rate limiting handled with one automatic retry respecting `Retry-After`. All I/O via outbound HTTPS, no inbound webhooks.
- **Approval gate** (`automaint/safety/approval.py`): `request_approval()` formats and posts dry-run plan to Slack. `poll_approval()` polls every N seconds — ❌ takes priority over ✅, timeout auto-rejects, transient Slack errors skip the poll without aborting.
- **Prometheus metrics** (`automaint/observability/metrics.py`, `tracking.py`): `REGISTRY` with 7 metrics (actions_total, action_duration_seconds, batch_duration_seconds, ssh_errors_total, llm_requests_total, approval_wait_seconds, vm_lock_held_seconds). `start_metrics_server()` launches aiohttp app serving `/metrics` and `/health`. `tracking.py` provides `record_action_result()`, `record_ssh_error()`, `record_llm_outcome()`.
- **Tests**: 74 tests — 23 LLM, 10 Slack client, 21 approval gate, 20 metrics

### Pre-Phase 1.8: Wiring + Entry Point
- **`validate_window_node` wired** (`automaint/agent/graph.py`): No longer a stub. Calls `check_window_from_config()` — blocks batch if outside window (sets `error` → short-circuits to `generate_report`). `force=True` bypasses with warning. `build_batch_graph()` now accepts optional `window: MaintenanceWindow | None`.
- **`main.py` implemented**: Full entry point — CLI args, config loading, component wiring (SSH, executor, locker, Slack, LLM), `--run-now` mode, scheduler loop with per-env cron jobs, graceful shutdown on SIGTERM/SIGINT.
- **`EnvironmentSchema`** extended with `maintenance_timezone: str = "UTC"`.
- **`_build_maintenance_window()`** helper parses `"HH:MM-HH:MM"` window strings from inventory.
- **Tests**: 21 graph tests (now 25), 17 main.py tests

### Phase 1.7: Config & Scheduling
- **Maintenance windows** (`automaint/scheduling/windows.py`): `is_within_window()` handles normal and overnight windows, timezone-aware via `zoneinfo`. `MaintenanceWindow` dataclass with validation. `check_window_from_config()` convenience wrapper.
- **Scheduler** (`automaint/scheduling/scheduler.py`): `MaintenanceScheduler` wraps `AsyncIOScheduler` — `add_maintenance_job()` registers cron-triggered async callbacks, `list_jobs()` summarises registered jobs, `start()`/`stop()` manage lifecycle. Misfire grace: 600s, coalesce enabled.
- **Example configs** (`example/inventory.yaml`, `example/settings.yaml`): Reference configuration files covering production/staging/dev environments with annotated comments.
- **Tests**: 36 tests — 25 windows, 11 scheduler

### vLLM Deployment
- **`deploy/vllm/docker-compose.yml`** — production Docker Compose for vLLM: NVIDIA GPU passthrough, the exact serve command from CLAUDE.md, `restart: unless-stopped`, model volume mount, healthcheck (180s start period for model load), 7-day log retention
- **`deploy/vllm/.env.example`** — all tunable vars: `MODEL_ID`, `HF_TOKEN`, `MAX_MODEL_LEN`, `GPU_MEM_UTIL`, `VLLM_PORT`, `MODEL_CACHE_DIR`
- **`LLMClient.check_endpoint()`** — detailed health check: reachability, model list, test completion with round-trip latency
- **`--check-llm` CLI flag** in `main.py` — `uv run python -m automaint --check-llm` prints status, model IDs, and latency without starting the agent

### Dual-Channel Approval (Slack + Web UI)
- **`ApprovalManager`** (`automaint/safety/approval.py`): In-memory store for pending approvals. `PendingApproval` dataclass with `asyncio.Event` for signalling. `register()`, `decide()` (idempotent), `wait_for_decision()` (timeout auto-rejects), `get_pending()`, `get_history()`.
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
- **`--audit` CLI mode** in `main.py`: `uv run python -m automaint --audit [--batch-id X] [--vm-id Y] [--action-type Z] [--event-type T] [--last N] [--batches]`
- **Integration tests** (`tests/safety/test_audit_integration.py`, 21 tests):
  - `action_type` filter correctness (6 tests)
  - `get_recent_batches()` correctness (7 tests)
  - VM graph audit trail written correctly via full graph run with mocked SSH (3 tests)
  - Audit CLI query functions via `run_audit_query()` (5 tests)
- **Tests**: 415 total passing

## In Progress
None — dual-channel approval complete. Phase 1.8 requires real VM infrastructure.

## Next Up
- **Phase 1.8: End-to-End Validation** — dry-run disk_cleanup on a real VM through the full pipeline



## Decisions Made
- **LangGraph Send() pattern**: `Send()` objects must come from conditional edge routing functions, NOT from nodes. Nodes return dicts. Routing functions return strings or `list[Send]`. Discovered via `InvalidUpdateError` during Phase 1.5.
- **Pre-compiled VM graph**: The per-VM graph is compiled once in `make_fan_out_router()` and reused for all fan-out invocations via closure. Avoids N graph compilations.
- **Routing-only nodes**: `check_more_actions` is a pass-through node (returns `{}`) that exists only to give the conditional edge a named source. LangGraph requires conditional edges to be attached to nodes.
- **State serialisation at boundaries**: Sub-graph states use TypedDict; the VM graph stores results as `list[dict]`. ActionResult objects are serialised when written, deserialised when read (e.g., report generator). Avoids Pydantic/dataclass serialisation across graph boundaries.
- **Hardcoded fallbacks first**: All LLM-powered functions (`prioritize_actions`, `generate_report`, `analyze_failure`) are fully implemented with hardcoded logic before LLM integration. The agent is fully functional without LLM.
- **Module-scoped test fixtures for expensive clients**: `AsyncOpenAI` initialises an httpx transport (~1.4s). Using `pytest.fixture(scope="module")` reduces LLM test suite from 57s to 6.5s.
- **Custom Prometheus registry**: Using `CollectorRegistry()` instead of the library default for test isolation and explicit ownership — `generate_latest(REGISTRY)` only outputs AutoMaint metrics.
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
- **Port bind before validation caused test failures**: `start_metrics_server()` was called before `--env` validation checks. When two `async_main` calls ran in the same process (test suite), the second bind on port 9090 failed. Fix: validate `--env` / unknown env BEFORE creating the audit store or starting the server.

## Blockers
None.

## Files Changed (This Session)
### Modified
- `automaint/agent/decisions.py` — LLM wired in: all decision functions accept optional llm_client, fall back to hardcoded
- `automaint/integrations/llm.py` — Full LLMClient implementation
- `tests/integrations/test_llm.py` — 23 tests
- `automaint/agent/vm_graph.py` — Full per-VM graph implementation
- `automaint/agent/graph.py` — Full batch orchestrator implementation
- `tests/agent/test_decisions.py` — 23 tests (updated for new llm_client signature)
- `tests/agent/test_vm_graph.py` — 28 tests
- `tests/agent/test_graph.py` — 21 tests
- `automaint/main.py` — full entry point implementation
- `automaint/agent/graph.py` — validate_window_node wired + build_batch_graph accepts window
- `automaint/config/schema.py` — EnvironmentSchema.maintenance_timezone field added
- `example/inventory.yaml` — maintenance_timezone field added to all environments
- `tests/agent/test_graph.py` — 4 new window node tests (25 total)
- `tests/test_main.py` — 17 tests for CLI parsing and helper functions
- `automaint/scheduling/windows.py` — is_within_window, MaintenanceWindow dataclass
- `automaint/scheduling/scheduler.py` — MaintenanceScheduler wrapping AsyncIOScheduler
- `tests/scheduling/test_windows.py` — 25 tests
- `tests/scheduling/test_scheduler.py` — 11 tests
- `example/inventory.yaml` — annotated reference inventory
- `example/settings.yaml` — annotated reference settings
- `automaint/integrations/slack.py` — Full SlackClient implementation
- `automaint/safety/approval.py` — request_approval + poll_approval
- `automaint/observability/metrics.py` — Prometheus registry + HTTP server
- `automaint/observability/tracking.py` — record_action_result, record_ssh_error, record_llm_outcome
- `tests/integrations/test_slack.py` — 10 tests
- `tests/safety/test_approval.py` — 21 tests
- `tests/observability/test_metrics.py` — 20 tests
- `docs/SETUP.md` — full setup guide: prerequisites, vLLM, SSH, Slack, config, first run, systemd service, monitoring, troubleshooting
- `docs/learning/13-vllm-setup.md` — learning doc: GPU passthrough, host volumes, healthcheck start_period, check_endpoint design
- `deploy/vllm/docker-compose.yml` — production vLLM container with GPU passthrough
- `deploy/vllm/.env.example` — configurable deployment vars
- `automaint/integrations/llm.py` — check_endpoint() method with model list + latency
- `automaint/main.py` — --check-llm flag + run_llm_check()
- `automaint/observability/metrics.py` — UI routes + handlers, typed AppKey, AuditStore import
- `docs/learning/12-web-ui.md` — learning doc: AppKey, slash URL matching, same-server architecture, Pico.css
- `automaint/main.py` — pass audit_store to start_metrics_server()
- `automaint/safety/audit.py` — action_type filter in get_events(), get_recent_batches() method added
- `automaint/main.py` — --audit CLI mode, run_audit_query(), EventType import
- `tests/safety/test_audit_integration.py` — 21 integration tests (created)
- `docs/learning/11-sqlite-audit.md` — learning doc: GROUP_CONCAT aggregation, CLI short-circuit pattern, integration test strategy
- `tasks/todo.md` — Phases 1.4/1.5/1.6/1.7 items checked off
- `docs/command-log.md` — Phase 1.6 + 1.7 commands added
- `tasks/lessons.md` — aiohttp async CM, rate-limit retry, APScheduler __slots__, DST offset, web.Response lessons added

### Created
- `docs/learning/05-vm-graph.md` — LangGraph action loop, lock patterns
- `docs/learning/06-batch-orchestrator.md` — Send() fan-out, reducers, compiled graph reuse
- `docs/learning/07-llm-client.md` — LLM client patterns, thinking modes, fallback design
- `docs/learning/08-slack-approval.md` — aiohttp pattern, Slack reaction polling, approval gate design
- `docs/learning/09-metrics.md` — Prometheus registry, counter vs histogram, aiohttp server
- `docs/learning/10-scheduling.md` — maintenance windows, APScheduler, timezone handling

## Test Count
479 tests passing (454 unit/integration + 25 Playwright UI tests).
