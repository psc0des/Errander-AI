# AutoMaint — Project Status

## Last Updated
2026-03-23

## Current Phase
**Phase 1.3 — First Action: Disk Cleanup** (sub-graph complete, pending real VM test)

## Completed

### Phase 1.1: Project Foundation
- Full project scaffold — Option C architecture (Parent Orchestrator + Fan-Out + Sub-Graphs)
- Data models, state dataclasses, strategy pattern stubs, policy system
- All module stubs created, test structure mirroring src

### Phase 1.2: Core Infrastructure
- Settings loader (env vars + YAML), schema validation, inventory loader with inheritance
- Audit logging (async SQLite), SSH execution (connection pooling + retry)
- OS detection, sandbox/dry-run wrapper, file-based VM locking

### Phase 1.3: Disk Cleanup (in progress)
- **Sub-graph**: LangGraph StateGraph with 4 nodes: validate → assess → execute → verify
- **Whitelist enforcement**: Hardcoded `ALLOWED_CLEANUP_PATHS` — `/tmp`, `apt-cache`, `yum-cache`, `journal`, `orphaned-deps`. Non-whitelisted paths are BLOCKED immediately.
- **Dry-run mode**: Uses simulate commands (e.g., `apt-get autoremove --simulate`) or synthetic `[DRY-RUN]` results
- **Live mode**: Real cleanup commands — `find /tmp -delete`, `apt-get clean`, `journalctl --vacuum-time`, `autoremove`
- **OS-aware**: AptManager for Ubuntu/Debian, DnfManager for RHEL — command generation fully implemented
- **Verification**: Post-cleanup `df -h` comparison against pre-cleanup baseline
- **Tests**: 31 tests covering whitelist, validation, routing, assess, execute, verify, sub-graph integration
- **Total tests**: 209 passing

## In Progress
Nothing — disk cleanup sub-graph complete, pending real VM dry-run test.

## Next Up
- **Phase 1.4: Per-VM Graph** — vm_graph (lock → discover → plan → dispatch → audit → unlock)
- **Phase 1.5: Batch Orchestrator** — fan-out to per-VM graphs
- **Phase 1.6: Integrations** — LLM client, Slack client, approval gate

## Decisions Made
- **LangGraph node wrapping**: Async nodes with injected dependencies must use `async def` wrappers, not lambdas (LangGraph requires awaitable functions)
- **TypedDict for graph state**: LangGraph works better with TypedDict than dataclasses for state
- **Assess before execute**: Added assessment step between validate and execute to collect space data before cleanup
- **Command generation pattern**: PackageManager generates command strings, SandboxExecutor handles dry-run/live routing
- **Whitelist is code, not config**: Cleanup whitelist is a frozen set in Python, never loaded from YAML or LLM decisions

## Blockers
None.

## Files Changed (This Session)
### Modified
- `automaint/agent/subgraphs/disk_cleanup.py` — Full sub-graph implementation
- `automaint/execution/commands.py` — AptManager + DnfManager fully implemented
- `tests/agent/subgraphs/test_disk_cleanup.py` — 31 tests
- `tasks/todo.md` — Phase 1.3 items checked off

### Created
- `docs/learning/04-disk-cleanup-subgraph.md` — Learning doc for LangGraph patterns
