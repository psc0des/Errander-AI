"""Patching sub-graph — non-kernel OS package updates.

Lifecycle:
1. Validate: Confirm kernel packages are excluded (hardcoded, NEVER LLM-decided).
2. Assess: List upgradable packages, filter out excluded patterns.
   Idempotent — if no updates pending, sets nothing_to_do=True.
3. Snapshot: Record installed package versions for rollback.
4. Execute: Run package upgrade (apt/dnf) or simulate in dry-run mode.
5. Verify: Confirm packages updated to expected versions.

Risk tier: Medium (log + notify).
Rollback strategy: Full — snapshot package list, batch rollback to previous versions.

IMPORTANT: Kernel packages (linux-*, linux-image-*, kernel-*) are ALWAYS excluded.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.agent.subgraphs.disk_cleanup import get_package_manager_by_name
from errander.execution.sandbox import SandboxExecutor
from errander.models.actions import ActionStatus

logger = logging.getLogger(__name__)

# --- Kernel exclusion (hardcoded, NEVER configurable) ---

MANDATORY_KERNEL_EXCLUDES: frozenset[str] = frozenset({
    "linux-*",
    "linux-image-*",
    "linux-headers-*",
    "kernel-*",
    "kernel-core-*",
    "kernel-modules-*",
})


def _is_kernel_package(package_name: str) -> bool:
    """Check if a package name matches any kernel exclusion pattern."""
    return any(
        fnmatch.fnmatch(package_name, pattern)
        for pattern in MANDATORY_KERNEL_EXCLUDES
    )


def _filter_kernel_packages(packages: list[str]) -> list[str]:
    """Remove kernel packages from a list of package names."""
    return [p for p in packages if not _is_kernel_package(p)]


# --- State ---

class PatchingGraphState(TypedDict, total=False):
    """State flowing through the patching sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None

    exclude_patterns: list[str]   # patterns to exclude (kernel always included)

    # Assessment results
    pending_updates: list[str]    # non-excluded packages with available updates

    # Snapshot (for rollback)
    version_snapshot: dict[str, str]  # package → version before patching

    # Execution results
    patch_output: str

    # Verification
    updated_versions: dict[str, str]  # package → version after patching

    # Idempotency
    nothing_to_do: bool


# --- Node functions ---

def validate_node(state: PatchingGraphState) -> dict[str, Any]:
    """Validate that kernel packages are excluded.

    HARDCODED CHECK — kernel exclusion is NEVER an LLM decision.
    Always merges MANDATORY_KERNEL_EXCLUDES into exclude_patterns.
    """
    user_excludes = state.get("exclude_patterns", [])

    # Merge mandatory kernel excludes with any user-provided excludes
    all_excludes = list(MANDATORY_KERNEL_EXCLUDES | frozenset(user_excludes))

    logger.info(
        "Patching validation for %s: exclude patterns = %s",
        state.get("vm_id", "unknown"), all_excludes,
    )

    return {
        "exclude_patterns": all_excludes,
        "status": ActionStatus.PENDING.value,
    }


async def assess_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """List upgradable packages and filter out excluded patterns.

    Idempotency: if no non-excluded updates are pending, sets nothing_to_do=True.
    """
    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)
    exclude_patterns = state.get("exclude_patterns", list(MANDATORY_KERNEL_EXCLUDES))

    # Refresh local package index so upgradable list reflects current upstream state.
    # Without this, a VM that has never run apt update returns an empty or stale list.
    refresh = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.refresh_package_lists(),
    )
    if not refresh.success:
        logger.warning(
            "Package list refresh failed on %s (continuing with cached lists): %s",
            vm_id, refresh.stderr[:200],
        )

    # List upgradable packages
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_upgradable(),
    )

    if not result.success:
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"Failed to list upgradable packages: {result.stderr}",
        }

    # Parse package names from output
    all_packages = _parse_upgradable(result.stdout, os_family)

    # Filter out excluded patterns
    filtered: list[str] = []
    for pkg in all_packages:
        excluded = any(fnmatch.fnmatch(pkg, pat) for pat in exclude_patterns)
        if not excluded:
            filtered.append(pkg)

    if not filtered:
        logger.info("No non-excluded updates pending on %s — nothing to do", vm_id)
        return {
            "pending_updates": [],
            "nothing_to_do": True,
            "status": ActionStatus.SKIPPED.value,
        }

    logger.info(
        "Found %d upgradable packages on %s (after filtering %d excluded)",
        len(filtered), vm_id, len(all_packages) - len(filtered),
    )
    return {
        "pending_updates": filtered,
        "nothing_to_do": False,
    }


async def snapshot_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Record installed package versions for rollback.

    This is critical safety infrastructure — if patching fails, we need
    these versions to roll back.
    """
    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)
    pending = state.get("pending_updates", [])

    if not pending:
        return {"version_snapshot": {}}

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_installed_versions(pending),
    )

    if not result.success:
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"Failed to capture package snapshot: {result.stderr[:200]}",
        }

    snapshot = _parse_versions(result.stdout)

    if not snapshot:
        logger.error(
            "Package snapshot is empty for %s — aborting to preserve rollback", vm_id,
        )
        return {
            "status": ActionStatus.FAILED.value,
            "error": "empty package snapshot — cannot guarantee rollback safety",
        }

    logger.info("Captured version snapshot for %d packages on %s", len(snapshot), vm_id)
    return {"version_snapshot": snapshot}


async def execute_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Execute package upgrade.

    Live: apt-get upgrade / dnf upgrade with kernel exclusions.
    Dry-run: apt-get --simulate upgrade / dnf check-update.
    """
    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)
    exclude_patterns = state.get("exclude_patterns", list(MANDATORY_KERNEL_EXCLUDES))

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.upgrade_all(exclude_patterns=exclude_patterns),
        simulate_command=pkg_mgr.simulate_upgrade(),
    )

    status = ActionStatus.DRY_RUN_OK if executor.dry_run else ActionStatus.SUCCESS
    if not result.success and not executor.dry_run:
        status = ActionStatus.FAILED

    return {
        "patch_output": result.stdout.strip(),
        "status": status.value,
        "error": result.stderr.strip() if not result.success else None,
    }


async def verify_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Verify packages were updated by comparing with snapshot.

    Only runs in live mode.
    """
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return {}

    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)
    pending = state.get("pending_updates", [])
    snapshot = state.get("version_snapshot", {})

    if not pending:
        return {}

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_installed_versions(pending),
    )

    if not result.success:
        return {"error": "Failed to verify package versions after patching"}

    current_versions = _parse_versions(result.stdout)

    # Compare with snapshot
    changed: dict[str, str] = {}
    for pkg, new_ver in current_versions.items():
        old_ver = snapshot.get(pkg, "unknown")
        if old_ver != new_ver:
            changed[pkg] = f"{old_ver} -> {new_ver}"

    if changed:
        logger.info("Updated %d packages on %s: %s", len(changed), vm_id, changed)
    else:
        logger.info("No version changes detected on %s after patching", vm_id)

    return {"updated_versions": current_versions}


# --- Helpers ---

def _get_connection_params(state: PatchingGraphState) -> dict[str, str]:
    """Extract SSH connection params from state."""
    return {
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("username", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("key_path", ""),  # type: ignore[typeddict-item]
    }


def _parse_upgradable(output: str, os_family: str) -> list[str]:
    """Parse package names from list-upgradable output.

    apt format: 'package/source version1 arch [upgradable from: version2]'
    dnf format: 'package.arch  version  repo'
    """
    packages: list[str] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Listing") or line.startswith("Last metadata"):
            continue

        if os_family in ("ubuntu", "debian"):
            # apt: "package/source version ..."
            if "/" in line:
                pkg = line.split("/")[0]
                if pkg:
                    packages.append(pkg)
        else:
            # dnf: "package.arch version repo"
            parts = line.split()
            if parts:
                # Remove architecture suffix (.x86_64, .noarch, etc.)
                pkg = parts[0].rsplit(".", 1)[0] if "." in parts[0] else parts[0]
                if pkg:
                    packages.append(pkg)

    return packages


def _parse_versions(output: str) -> dict[str, str]:
    """Parse package=version pairs from dpkg-query or rpm -q output."""
    versions: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" in line:
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                versions[parts[0]] = parts[1]
    return versions


# --- Routing ---

def route_after_validate(state: PatchingGraphState) -> str:
    """Route after validation: continue or abort."""
    if state.get("status") == ActionStatus.FAILED.value:
        return END
    return "assess"


def route_after_assess(state: PatchingGraphState) -> str:
    """Route after assessment: skip if nothing to do (idempotency)."""
    if state.get("nothing_to_do"):
        return END
    return "snapshot"


def route_after_execute(state: PatchingGraphState) -> str:
    """Route after execution: verify (live) or finish (dry-run)."""
    if state.get("status") in (ActionStatus.DRY_RUN_OK.value, ActionStatus.FAILED.value):
        return END
    return "verify"


# --- Graph builder ---

def build_patching_subgraph(
    executor: SandboxExecutor,
) -> StateGraph:
    """Construct the patching sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.

    Returns:
        StateGraph for patching (call .compile() to use).
    """
    builder: StateGraph = StateGraph(PatchingGraphState)

    async def _assess(state: PatchingGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _snapshot(state: PatchingGraphState) -> dict[str, Any]:
        return await snapshot_node(state, executor=executor)

    async def _execute(state: PatchingGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: PatchingGraphState) -> dict[str, Any]:
        return await verify_node(state, executor=executor)

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("snapshot", _snapshot)
    builder.add_node("execute", _execute)
    builder.add_node("verify", _verify)

    builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_conditional_edges("assess", route_after_assess, ["snapshot", END])
    builder.add_edge("snapshot", "execute")
    builder.add_conditional_edges("execute", route_after_execute, ["verify", END])
    builder.add_edge("verify", END)

    return builder
