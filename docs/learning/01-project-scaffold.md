# 01 — Project Scaffold

**Date**: 2026-03-21
**Phase**: Phase 1.1 — Project Foundation

---

## What Was Built

The complete project skeleton for AutoMaint using **Option C: Parent Orchestrator + Fan-Out with Sub-Graphs**. This is a 3-level LangGraph architecture:

1. **Batch Orchestrator** (`agent/graph.py`) — loads config, validates targets, fans out to VMs via `Send()`
2. **Per-VM Graph** (`agent/vm_graph.py`) — locks VM, discovers state, plans/dispatches actions sequentially
3. **Action Sub-Graphs** (`agent/subgraphs/*.py`) — one per action type with validate → snapshot → execute → verify → rollback lifecycle

86 files total: 45 source files, 28 test files, plus pyproject.toml, .gitignore, and task tracking.

## Why Option C

Option A (single flat graph) is simpler but can't parallelize across VMs. Option B (sub-graphs, no fan-out) adds action isolation but still processes VMs sequentially. Option C combines both:

- **Fan-out via `Send()`** — process multiple VMs in parallel within a single batch run
- **Action sub-graphs** — each action type (patching, disk cleanup, etc.) is its own isolated graph with independent state, validation, and rollback
- **Three levels of state** — BatchState, VMMaintenanceState, per-action state (PatchingState, DiskCleanupState, etc.)

The complexity is justified because this replaces a human DevOps engineer. A human doesn't patch servers one at a time.

## Key Concepts

### Strategy Pattern for OS Abstraction

The `execution/commands.py` file uses the **strategy pattern** — an abstract `PackageManager` interface with concrete implementations (`AptManager` for Ubuntu/Debian, `DnfManager` for RHEL):

```python
class PackageManager(ABC):
    @abstractmethod
    def list_upgradable(self) -> str: ...
    @abstractmethod
    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str: ...

class AptManager(PackageManager): ...
class DnfManager(PackageManager): ...

def get_package_manager(os_family: OSFamily) -> PackageManager:
    managers = {OSFamily.UBUNTU: AptManager, OSFamily.DEBIAN: AptManager, OSFamily.RHEL: DnfManager}
    return managers[os_family]()
```

The managers generate shell command **strings** — they don't execute anything. Execution always flows through the SSH layer. This separation means you can unit-test command generation without SSH.

### State Dataclasses with Reducers

LangGraph uses **reducers** to merge state updates. The `_merge_results` function is a custom reducer that appends new action results:

```python
def _merge_results(existing: list[ActionResult], new: list[ActionResult]) -> list[ActionResult]:
    return [*existing, *new]

@dataclass
class BatchState:
    vm_results: Annotated[list[ActionResult], _merge_results] = field(default_factory=list)
```

When multiple VM sub-graphs complete in parallel, their results are merged into the parent's `vm_results` list via this reducer.

### Policy System

Three built-in policies control approval requirements:

| Policy | Auto-approve | Max retries |
|--------|-------------|-------------|
| `relaxed` | Low + Medium | 2 |
| `moderate` | Low only | 1 |
| `strict` | None (all need approval) | 0 |

Each VM in inventory references a policy by name. The safety validators check the action's risk tier against the VM's policy to decide if human approval is needed.

### Sandbox/Dry-Run Architecture

Dry-run is the **default mode**. The sandbox layer (`execution/sandbox.py`) wraps SSH execution:
- When `dry_run=True`: runs the simulate command (e.g., `apt-get --simulate`) or returns a synthetic result
- When `dry_run=False`: runs the real command

This means the full graph pipeline runs identically in both modes — only the execution layer differs.

## Gotchas Encountered

1. **`uv sync --group dev` vs `--extra dev`**: pyproject.toml uses `[project.optional-dependencies]` (PEP 621), not `[dependency-groups]` (PEP 735). The `--group` flag is for dependency groups, `--extra` is for optional dependencies. They look similar but are different specs.

2. **`uv` not pre-installed**: Had to `pip install uv` first. On a fresh machine, uv won't exist unless explicitly installed.

3. **Background task timeouts**: `uv sync` downloading 68 packages took longer than the default CLI timeout. Multiple parallel installs were accidentally triggered. No harm done (uv handles concurrent runs gracefully), but it was confusing.

## Project Structure

```
automaint/
├── agent/           # LangGraph graphs (3 levels)
│   ├── graph.py     # Level 1: Batch orchestrator (fan-out)
│   ├── vm_graph.py  # Level 2: Per-VM maintenance
│   ├── state.py     # All state dataclasses
│   ├── decisions.py # LLM decision logic + fallbacks
│   └── subgraphs/   # Level 3: Action sub-graphs
├── models/          # Pydantic/dataclass models (VM, Action, Plan, Event)
├── safety/          # Validators, rollback, approval, locking, audit
├── execution/       # SSH, OS commands, sandbox/dry-run
├── integrations/    # Slack, LLM client, secrets
├── observability/   # Prometheus metrics, tracking, reports
├── config/          # Inventory, policies, schema, settings
├── scheduling/      # APScheduler, maintenance windows
└── main.py          # Entry point
tests/               # Mirrors src (40 tests, all passing)
tasks/               # todo.md + lessons.md
```

## Questions to Test Understanding

1. **Why do `PackageManager` implementations return strings instead of executing commands directly?**
   - Answer: Separation of concerns. Command generation is testable without SSH. Execution is handled by the SSH layer, which adds timeout, retry, dry-run interception, etc.

2. **What happens when two VM sub-graphs finish at the same time and both write to `vm_results`?**
   - Answer: The `_merge_results` reducer appends both result lists. LangGraph guarantees reducer-based state merging is safe for parallel branches.

3. **Why is `strict` policy's `auto_approve_tiers` an empty frozenset?**
   - Answer: Strict mode requires human approval for ALL risk tiers, including Low. Nothing is auto-approved.

4. **What's the difference between `dry_run=True` in BatchState vs. the sandbox layer?**
   - Answer: `dry_run` in BatchState is a configuration flag that propagates to all VMs and actions. The sandbox layer is the enforcement point — it intercepts SSH commands and substitutes simulate commands when the flag is True.

5. **Why are there separate state dataclasses per action type (PatchingState, DiskCleanupState, etc.) instead of one generic ActionState?**
   - Answer: Each action type needs different data. Patching tracks available patches and excluded kernel packages. Disk cleanup tracks whitelist paths and tmp file age. Type-safe per-action state prevents "one state fits all" bloat and catches errors at definition time.
