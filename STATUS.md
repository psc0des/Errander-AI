# AutoMaint — Project Status

## Last Updated
2026-03-21

## Current Phase
**Phase 1.2 — Core Infrastructure** (COMPLETE)

## Completed

### Phase 1.1: Project Foundation
- Full project scaffold — Option C architecture (Parent Orchestrator + Fan-Out + Sub-Graphs)
- `pyproject.toml` with all runtime + dev dependencies
- Data models: VMTarget, VMInfo, Action, ActionResult, VMPlan, BatchPlan, AuditEvent
- State dataclasses: BatchState, VMMaintenanceState, 5 per-action states
- Strategy pattern: PackageManager ABC with AptManager + DnfManager stubs
- Policy system: relaxed/moderate/strict with risk tier auto-approval rules
- All module stubs created, test structure mirroring src

### Phase 1.2: Core Infrastructure
- **Settings loader**: `load_settings()` — env vars (AUTOMAINT_ prefix) + settings.yaml, layered precedence
- **Schema validation**: Pydantic models for inventory.yaml, policies.yaml, settings.yaml
- **Inventory loader**: `load_inventory()` — environment→host inheritance for ssh_user, ssh_key_path, policy
- **Audit logging**: `AuditStore` — async SQLite writer/reader with batch_id/vm_id/event_type filters
- **SSH execution**: `SSHConnectionManager` — persistent connections, exponential backoff retry, command timeout
- **OS detection**: Parse /etc/os-release, df -h, docker info, /proc/uptime with graceful degradation
- **Sandbox/dry-run**: `SandboxExecutor` — wraps SSH, simulate_command or synthetic result, command logging
- **File-based VM locking**: `FileLocker` — JSON lock files, TTL auto-expiry, stale lock cleanup, ownership checks
- **Config files**: Example inventory.yaml, policies.yaml, settings.yaml in config/
- **Tests**: 179 total, all passing

## In Progress
Nothing — Phase 1.2 complete.

## Next Up
- **Phase 1.3: First Action — Disk Cleanup** (lowest risk action, end-to-end)
  - disk_cleanup sub-graph (validate → snapshot → execute → verify)
  - Whitelist enforcement (/tmp, apt/yum cache, journal, orphaned deps)
  - Dry-run simulation
  - Tests

## Decisions Made
- **Architecture**: Option C — Parent Orchestrator + Fan-Out with Sub-Graphs per Action Type
- **Package manager**: uv
- **Config inheritance**: Global defaults → Environment settings → Host overrides
- **VM ID format**: `{env_name}/{target_name}` (e.g., "production/web-prod-01")
- **Settings layering**: Secrets from env vars, tuning from YAML, env vars override YAML
- **Audit storage**: aiosqlite with ISO timestamps for PostgreSQL migration compatibility
- **SSH pooling**: Per-VM persistent connections, reused within a batch run
- **Lock format**: JSON files with TTL, auto-cleanup of stale/corrupt locks

## Blockers
None.

## Files Changed (This Session)
### Modified
- `automaint/config/settings.py`, `schema.py`, `inventory.py` — Full implementations
- `automaint/safety/audit.py` — AuditStore with SQLite backend
- `automaint/safety/locking.py` — FileLocker with TTL
- `automaint/execution/ssh.py` — SSHConnectionManager
- `automaint/execution/os_detection.py` — OS detection + parsing
- `automaint/execution/sandbox.py` — SandboxExecutor
- `pyproject.toml` — Added aiosqlite dependency

### Created
- `tests/config/test_settings.py` — 12 tests
- `config/inventory.yaml`, `config/policies.yaml`, `config/settings.yaml`
- `docs/learning/02-settings-loader.md`
