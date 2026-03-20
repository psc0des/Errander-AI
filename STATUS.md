# AutoMaint — Project Status

## Last Updated
2026-03-21

## Current Phase
**Phase 1.2 — Core Infrastructure** (Settings loader complete, audit logging next)

## Completed

### Phase 1.1: Project Foundation
- Full project scaffold — Option C architecture (Parent Orchestrator + Fan-Out + Sub-Graphs)
- `pyproject.toml` with all runtime + dev dependencies
- Data models: VMTarget, VMInfo, Action, ActionResult, VMPlan, BatchPlan, AuditEvent
- State dataclasses: BatchState, VMMaintenanceState, 5 per-action states (Patching, DockerPrune, LogRotation, DiskCleanup, BackupVerify)
- Strategy pattern: PackageManager ABC with AptManager + DnfManager stubs
- Policy system: relaxed/moderate/strict with risk tier auto-approval rules
- Safety module stubs: validators, rollback, approval gate, VM locking, audit
- Execution layer stubs: SSH, OS detection, sandbox/dry-run
- Integration stubs: Slack client, LLM client (OpenAI SDK → vLLM), secrets (env vars)
- Observability stubs: Prometheus metrics, action tracking, report generation
- Config stubs: inventory loader, YAML schema validation, settings
- Scheduling stubs: APScheduler, maintenance windows
- Test structure: 28 test files mirroring src, 40 tests passing
- Task tracking: tasks/todo.md (Phase 1-3 checklist), tasks/lessons.md
- Documentation: command-log.md, learning/01-project-scaffold.md, STATUS.md
- All modules import without errors

### Phase 1.2: Core Infrastructure (in progress)
- **Settings loader**: `load_settings()` loads from env vars (AUTOMAINT_ prefix) + optional settings.yaml
- **Schema validation**: Pydantic models for inventory.yaml, policies.yaml, settings.yaml
- **Inventory loader**: `load_inventory()` with environment→host inheritance (ssh_user, ssh_key_path, policy)
- **Config YAML files**: Example inventory.yaml, policies.yaml, settings.yaml in config/
- **Tests**: 58 config tests (schema, settings, inventory, policies) — all passing
- **Total tests**: 91 passing

## In Progress
- Audit logging to SQLite (next up)

## Next Up
- SSH execution layer (asyncssh wrapper)
- OS detection via SSH
- Sandbox/dry-run execution wrapper
- File-based VM locking

## Decisions Made
- **Architecture**: Option C — Parent Orchestrator + Fan-Out with Sub-Graphs per Action Type
- **Package manager**: uv (installed via pip, uses `[project.optional-dependencies]` not `[dependency-groups]`)
- **State design**: Dataclasses with Annotated reducers for parallel result merging
- **Command abstraction**: Strategy pattern — PackageManager generates command strings, SSH layer executes
- **Policy system**: Three built-in tiers (relaxed/moderate/strict) controlling auto-approval thresholds
- **Config inheritance**: Global defaults → Environment settings → Host overrides (matching spec)
- **VM ID format**: `{env_name}/{target_name}` (e.g., "production/web-prod-01")
- **Settings layering**: Secrets from env vars, tuning from YAML, env vars override YAML for overlapping fields

## Blockers
None.

## Files Changed (This Session)
### Modified
- `automaint/config/settings.py` — Full implementation of `load_settings()` with env var + YAML loading
- `automaint/config/schema.py` — Pydantic models for inventory, policies, settings YAML validation
- `automaint/config/inventory.py` — Full implementation of `load_inventory()` with inheritance resolution
- `tests/config/test_schema.py` — 21 tests for schema validation
- `tests/config/test_inventory.py` — 17 tests for inventory loading + validation
- `tasks/todo.md` — Marked settings loader complete

### Created
- `tests/config/test_settings.py` — 12 tests for settings loading
- `config/inventory.yaml` — Example inventory matching spec
- `config/policies.yaml` — Example policies matching spec
- `config/settings.yaml` — Example settings matching spec
