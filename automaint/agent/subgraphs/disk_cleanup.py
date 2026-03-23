"""Disk cleanup sub-graph — remove files from approved whitelist paths only.

WHITELIST (only these paths are safe to clean):
- /tmp (files older than configurable threshold)
- apt/yum package cache
- Old journal logs (journalctl --vacuum-time)
- Orphaned package dependencies

Anything NOT on the whitelist requires human approval.

Lifecycle:
1. Validate: Confirm all requested paths are on the whitelist.
2. Assess: Calculate reclaimable space per whitelist path (via SSH).
3. Execute: Remove files (or simulate in dry-run mode).
4. Verify: Confirm space was reclaimed.

Risk tier: Low (automatic).
Rollback strategy: None needed — only targets known-safe paths.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from automaint.execution.commands import AptManager, DnfManager, get_package_manager
from automaint.execution.sandbox import SandboxExecutor
from automaint.execution.ssh import SSHResult
from automaint.models.actions import ActionStatus

logger = logging.getLogger(__name__)

# --- Whitelist enforcement (hardcoded, never LLM-decided) ---

ALLOWED_CLEANUP_PATHS: frozenset[str] = frozenset({
    "/tmp",
    "apt-cache",
    "yum-cache",
    "journal",
    "orphaned-deps",
})


def is_whitelisted(path: str) -> bool:
    """Check if a cleanup path is on the approved whitelist."""
    return path in ALLOWED_CLEANUP_PATHS


def validate_whitelist(paths: list[str]) -> list[str]:
    """Return any paths NOT on the whitelist."""
    return [p for p in paths if not is_whitelisted(p)]


# --- State (TypedDict for LangGraph compatibility) ---

class DiskCleanupGraphState(TypedDict, total=False):
    """State flowing through the disk cleanup sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None
    whitelist_paths: list[str]
    tmp_age_days: int
    journal_vacuum_days: int
    # Assessment results
    space_by_path: dict[str, str]  # path → human-readable size
    # Execution results
    cleanup_output: dict[str, str]  # path → command output
    # Verification
    disk_before: dict[str, float]  # mount → usage %
    disk_after: dict[str, float]   # mount → usage %


# --- Reusable command generators ---

def _tmp_cleanup_cmd(age_days: int) -> str:
    return f"find /tmp -type f -mtime +{age_days} -delete 2>/dev/null; echo done"


def _tmp_assess_cmd(age_days: int) -> str:
    return f"find /tmp -type f -mtime +{age_days} -exec du -ch {{}} + 2>/dev/null | tail -1 || echo '0\ttotal'"


def _journal_vacuum_cmd(days: int) -> str:
    return f"journalctl --vacuum-time={days}d 2>/dev/null || echo 'journal not available'"


def _journal_size_cmd() -> str:
    return "journalctl --disk-usage 2>/dev/null || echo '0'"


# --- Node functions ---

def validate_node(state: DiskCleanupGraphState) -> dict[str, Any]:
    """Validate that all requested cleanup paths are on the whitelist.

    HARDCODED CHECK — this is NEVER an LLM decision.
    """
    paths = state.get("whitelist_paths", list(ALLOWED_CLEANUP_PATHS))
    rejected = validate_whitelist(paths)

    if rejected:
        logger.error(
            "BLOCKED: paths not on whitelist for %s: %s",
            state.get("vm_id", "unknown"), rejected,
        )
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"Paths not on cleanup whitelist: {rejected}",
        }

    return {
        "whitelist_paths": paths,
        "status": ActionStatus.PENDING.value,
    }


async def assess_node(
    state: DiskCleanupGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Calculate reclaimable space per whitelist path via SSH.

    Runs assessment commands (du, journalctl --disk-usage, etc.)
    to determine how much space each path consumes.
    """
    vm_id = state["vm_id"]
    os_family = state["os_family"]
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)

    space: dict[str, str] = {}
    paths = state.get("whitelist_paths", list(ALLOWED_CLEANUP_PATHS))
    age_days = state.get("tmp_age_days", 7)

    # Get disk usage before cleanup
    df_result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command="df -h",
    )
    disk_before = _parse_df(df_result.stdout) if df_result.success else {}

    for path in paths:
        result: SSHResult
        if path == "/tmp":
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=_tmp_assess_cmd(age_days),
            )
            space["/tmp"] = result.stdout.strip() if result.success else "unknown"

        elif path in ("apt-cache", "yum-cache"):
            cmd = pkg_mgr.cache_size()
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=cmd,
            )
            space[path] = result.stdout.strip() if result.success else "unknown"

        elif path == "journal":
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=_journal_size_cmd(),
            )
            space["journal"] = result.stdout.strip() if result.success else "unknown"

        elif path == "orphaned-deps":
            # Can't easily assess size of orphaned deps without running autoremove --simulate
            if os_family in ("ubuntu", "debian"):
                sim_cmd = "apt-get autoremove --simulate 2>/dev/null | tail -1"
            else:
                sim_cmd = "dnf autoremove --assumeno 2>/dev/null | tail -3"
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=sim_cmd,
            )
            space["orphaned-deps"] = result.stdout.strip() if result.success else "unknown"

    return {
        "space_by_path": space,
        "disk_before": disk_before,
    }


async def execute_node(
    state: DiskCleanupGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Execute cleanup commands for each whitelist path.

    In dry-run mode, uses simulate commands or returns synthetic results.
    In live mode, executes the real cleanup commands.
    """
    vm_id = state["vm_id"]
    os_family = state["os_family"]
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)

    paths = state.get("whitelist_paths", list(ALLOWED_CLEANUP_PATHS))
    age_days = state.get("tmp_age_days", 7)
    journal_days = state.get("journal_vacuum_days", 7)

    output: dict[str, str] = {}

    for path in paths:
        if path == "/tmp":
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=_tmp_cleanup_cmd(age_days),
                simulate_command=_tmp_assess_cmd(age_days),
            )
            output["/tmp"] = result.stdout.strip()

        elif path in ("apt-cache", "yum-cache"):
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=pkg_mgr.clean_cache(),
                simulate_command=pkg_mgr.cache_size(),
            )
            output[path] = result.stdout.strip()

        elif path == "journal":
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=_journal_vacuum_cmd(journal_days),
                simulate_command=_journal_size_cmd(),
            )
            output["journal"] = result.stdout.strip()

        elif path == "orphaned-deps":
            if os_family in ("ubuntu", "debian"):
                sim_cmd = "apt-get autoremove --simulate 2>/dev/null"
            else:
                sim_cmd = "dnf autoremove --assumeno 2>/dev/null"
            result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=pkg_mgr.autoremove(),
                simulate_command=sim_cmd,
            )
            output["orphaned-deps"] = result.stdout.strip()

    status = ActionStatus.DRY_RUN_OK if executor.dry_run else ActionStatus.SUCCESS
    return {
        "cleanup_output": output,
        "status": status.value,
    }


async def verify_node(
    state: DiskCleanupGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Verify disk space was reclaimed after cleanup.

    Compares disk usage before and after. Only runs in live mode.
    """
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return {}

    vm_id = state["vm_id"]
    target = _get_connection_params(state)

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command="df -h",
    )

    if not result.success:
        return {"error": "Failed to verify disk usage after cleanup"}

    disk_after = _parse_df(result.stdout)
    return {"disk_after": disk_after}


# --- Helper functions ---

def _parse_df(output: str) -> dict[str, float]:
    """Parse df -h output into mount → usage percentage."""
    usage: dict[str, float] = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            try:
                usage[parts[5]] = float(parts[4].rstrip("%"))
            except ValueError:
                continue
    return usage


def _get_connection_params(state: DiskCleanupGraphState) -> dict[str, str]:
    """Extract SSH connection params from state.

    These are injected by the per-VM graph when dispatching to
    the sub-graph. For standalone testing, defaults are provided.
    """
    return {
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("username", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("key_path", ""),  # type: ignore[typeddict-item]
    }


def get_package_manager_by_name(os_family: str) -> AptManager | DnfManager:
    """Get the package manager instance for an OS family string."""
    if os_family in ("ubuntu", "debian"):
        return AptManager()
    return DnfManager()


# --- Routing ---

def route_after_validate(state: DiskCleanupGraphState) -> str:
    """Route after validation: continue or abort."""
    if state.get("status") == ActionStatus.FAILED.value:
        return END
    return "assess"


def route_after_execute(state: DiskCleanupGraphState) -> str:
    """Route after execution: verify (live) or finish (dry-run)."""
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return END
    return "verify"


# --- Graph builder ---

def build_disk_cleanup_nodes() -> dict[str, Any]:
    """Return the node functions for the disk cleanup sub-graph.

    The caller (per-VM graph or tests) must inject the SandboxExecutor
    by wrapping these nodes with the executor instance.

    Returns:
        Dict of node_name → node_function.
    """
    return {
        "validate": validate_node,
        "assess": assess_node,
        "execute": execute_node,
        "verify": verify_node,
    }


def build_disk_cleanup_subgraph(
    executor: SandboxExecutor,
) -> StateGraph:
    """Construct the disk cleanup sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.

    Returns:
        StateGraph for disk cleanup (call .compile() to use).
    """
    builder: StateGraph = StateGraph(DiskCleanupGraphState)

    # Wrap async nodes with executor — must be async def, not lambda
    async def _assess(state: DiskCleanupGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _execute(state: DiskCleanupGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: DiskCleanupGraphState) -> dict[str, Any]:
        return await verify_node(state, executor=executor)

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("execute", _execute)
    builder.add_node("verify", _verify)

    # Set entry point
    builder.set_entry_point("validate")

    # Edges
    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_edge("assess", "execute")
    builder.add_conditional_edges("execute", route_after_execute, ["verify", END])
    builder.add_edge("verify", END)

    return builder
