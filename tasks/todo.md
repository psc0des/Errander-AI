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
- [ ] Run `uv sync` and verify all imports work
- [ ] Run `uv run pytest` and verify passing tests

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
- [ ] Implement disk_cleanup sub-graph (validate → snapshot → execute → verify)
- [ ] Implement whitelist enforcement (only /tmp, apt/yum cache, journal, orphaned deps)
- [ ] Implement dry-run simulation for disk cleanup
- [ ] Write tests for disk cleanup sub-graph
- [ ] Test against a real VM (dry-run mode)

### 1.4 Per-VM Graph
- [ ] Implement vm_graph (lock → discover → plan → dispatch → audit → unlock)
- [ ] Implement discovery node (SSH gather system state)
- [ ] Implement action dispatch with conditional routing to sub-graphs
- [ ] Implement LLM-powered action prioritization (with hardcoded fallback)
- [ ] Write tests for per-VM graph

### 1.5 Batch Orchestrator
- [ ] Implement batch graph (load_config → validate_window → validate_targets → fan_out)
- [ ] Implement Send() fan-out to per-VM graphs
- [ ] Implement result collection and aggregation
- [ ] Implement report generation (template-based first, LLM later)
- [ ] Write tests for batch orchestrator

### 1.6 Integrations
- [ ] Implement LLM client (OpenAI SDK → vLLM) with fallback
- [ ] Implement Slack client (post message, poll reactions)
- [ ] Implement approval gate (post plan → poll → approve/reject/timeout)
- [ ] Implement Prometheus metrics and /health endpoint

### 1.7 Config & Scheduling
- [ ] Implement inventory YAML loader and validator
- [ ] Implement maintenance window enforcement
- [ ] Implement APScheduler setup
- [ ] Create example inventory.yaml

### 1.8 End-to-End Validation
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
