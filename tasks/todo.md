# AutoMaint — Task Tracking

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
- [ ] Dry-run disk_cleanup on a test VM via the full graph pipeline
- [ ] Verify audit trail captures all events
- [ ] Verify Slack notification works
- [ ] Verify metrics are exposed

## Phase 2: Remaining Action Types
- [ ] Implement patching sub-graph (with kernel exclusion + rollback)
- [ ] Implement docker_prune sub-graph
- [ ] Implement log_rotation sub-graph
- [ ] Implement backup_verify sub-graph
- [ ] Implement AptManager commands
- [ ] Implement DnfManager commands

## Phase 3: Hardening
- [ ] Rolling updates (percentage-based fleet caps)
- [ ] Canary logic (run on 1 VM first, then fleet)
- [ ] Drift detection (pre-flight check before live execution)
- [ ] Comprehensive error handling and edge cases
- [ ] Load testing with multiple VMs
