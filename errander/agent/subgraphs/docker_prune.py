"""Docker prune sub-graph — reclaim disk from unused Docker resources.

Lifecycle:
1. Validate: Check Docker is installed and running.
2. Assess: Count dangling images, stopped containers, reclaimable space.
   Idempotent — if nothing to prune, sets nothing_to_do=True.
3. Execute: Run docker system prune (or simulate in dry-run mode).
4. Verify: Re-check disk usage after pruning (skip in dry-run).

Risk tier: Low (automatic).
Rollback strategy: Re-pull only — pruned resources are gone.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.execution.privilege import privileged
from errander.execution.sandbox import SandboxExecutor
from errander.models.actions import ActionStatus

logger = logging.getLogger(__name__)


# --- State ---

class DockerPruneGraphState(TypedDict, total=False):
    """State flowing through the Docker prune sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None

    docker_available: bool

    # Prune scope (finding #12):
    # aggressive=False (default) → prune dangling images + stopped containers only
    # aggressive=True            → prune ALL unused images (-a); reclassified HIGH
    docker_prune_aggressive: bool

    # Assessment results
    dangling_images: int
    stopped_containers: int
    reclaimable_space: str       # human-readable from docker system df
    system_df_output: str        # raw docker system df output

    # Execution results
    prune_output: str

    # Verification
    disk_before: str             # docker system df before prune
    disk_after: str              # docker system df after prune

    # Idempotency
    nothing_to_do: bool


# --- Node functions ---

def validate_node(state: DockerPruneGraphState) -> dict[str, Any]:
    """Validate that Docker is available on this VM.

    Uses docker_available from VM discovery. If not set, defaults to True
    (will fail at assess if Docker is actually missing).
    """
    docker_available = state.get("docker_available", True)

    if not docker_available:
        logger.info(
            "Docker not available on %s — skipping prune",
            state.get("vm_id", "unknown"),
        )
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "Docker not installed or not running",
        }

    return {"status": ActionStatus.PENDING.value}


async def assess_node(
    state: DockerPruneGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Assess Docker resource usage: dangling images, stopped containers.

    Idempotency: if nothing to prune, sets nothing_to_do=True.
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)

    # Assessment calls always use dry_run=False — must inspect real Docker state.

    # Check docker is actually reachable.
    # Production: replace sudo -n /usr/bin/docker with root-owned wrapper scripts.
    # See SETUP.md "Docker hardening" for the enterprise wrapper approach.
    docker_check = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker info >/dev/null 2>&1 && echo ok"),
        dry_run=False,
    )
    if not docker_check.success or "ok" not in docker_check.stdout:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "Docker daemon not responding",
            "nothing_to_do": True,
        }

    # Get system df
    df_result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker system df 2>/dev/null"),
        dry_run=False,
    )
    system_df = df_result.stdout.strip() if df_result.success else ""

    # Count dangling images
    dangling_result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker images -f dangling=true -q 2>/dev/null | wc -l"),
        dry_run=False,
    )
    if dangling_result.success and not dangling_result.stdout.strip():
        return {
            "status": ActionStatus.FAILED.value,
            "error": "command returned empty output",
            "nothing_to_do": False,
        }
    dangling = _parse_int(dangling_result.stdout) if dangling_result.success else 0

    # Count stopped containers
    stopped_result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker ps -a -f status=exited -q 2>/dev/null | wc -l"),
        dry_run=False,
    )
    if stopped_result.success and not stopped_result.stdout.strip():
        return {
            "status": ActionStatus.FAILED.value,
            "error": "command returned empty output",
            "nothing_to_do": False,
        }
    stopped = _parse_int(stopped_result.stdout) if stopped_result.success else 0

    if dangling == 0 and stopped == 0:
        logger.info("No dangling images or stopped containers on %s — nothing to do", vm_id)
        return {
            "dangling_images": 0,
            "stopped_containers": 0,
            "system_df_output": system_df,
            "nothing_to_do": True,
            "status": ActionStatus.SKIPPED.value,
        }

    logger.info(
        "Docker prune assessment on %s: %d dangling images, %d stopped containers",
        vm_id, dangling, stopped,
    )
    return {
        "dangling_images": dangling,
        "stopped_containers": stopped,
        "system_df_output": system_df,
        "disk_before": system_df,
        "nothing_to_do": False,
    }


async def execute_node(
    state: DockerPruneGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Execute docker prune.

    Default (aggressive=False, finding #12):
      docker image prune -f        — dangling images only
      docker container prune -f    — exited containers only
      Does NOT use 'docker system prune -a' which removes ALL unused images.

    Aggressive (aggressive=True, risk tier HIGH, requires approval):
      docker system prune -af      — all unused images + containers + networks.

    Dry-run: docker system df (show reclaimable space).
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    dry_run = state.get("dry_run", True)
    aggressive = state.get("docker_prune_aggressive", False)

    if aggressive:
        live_cmd = privileged("/usr/bin/docker system prune -af 2>&1")
    else:
        # Safe default: dangling images + stopped containers only
        live_cmd = (
            privileged("/usr/bin/docker image prune -f 2>&1") + " && "
            + privileged("/usr/bin/docker container prune -f 2>&1")
        )

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=live_cmd,
        simulate_command=privileged("/usr/bin/docker system df 2>/dev/null"),
        dry_run=dry_run,
    )

    if dry_run:
        status = ActionStatus.DRY_RUN_OK
    elif result.success:
        status = ActionStatus.SUCCESS
    else:
        status = ActionStatus.FAILED
    return {
        "prune_output": result.stdout.strip(),
        "status": status.value,
        "error": result.stderr.strip() if not result.success else None,
    }


async def verify_node(
    state: DockerPruneGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Verify Docker resources were reclaimed after pruning.

    Only runs in live mode.
    """
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return {}

    vm_id = state["vm_id"]
    target = _get_connection_params(state)

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker system df 2>/dev/null"),
        dry_run=False,
    )

    if not result.success:
        return {"error": "Failed to verify Docker disk usage after prune"}

    return {"disk_after": result.stdout.strip()}


# --- Helpers ---

def _get_connection_params(state: DockerPruneGraphState) -> dict[str, str]:
    """Extract SSH connection params from state."""
    return {
        "hostname": str(state.get("hostname", "")),
        "username": str(state.get("username", "")),
        "key_path": str(state.get("key_path", "")),
    }


def _parse_int(s: str) -> int:
    """Parse an integer from command output, defaulting to 0."""
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


# --- Routing ---

def route_after_validate(state: DockerPruneGraphState) -> str:
    """Route after validation: continue or abort."""
    if state.get("status") in (ActionStatus.FAILED.value, ActionStatus.SKIPPED.value):
        return END
    return "assess"


def route_after_assess(state: DockerPruneGraphState) -> str:
    """Route after assessment: skip if nothing to do (idempotency)."""
    if state.get("nothing_to_do"):
        return END
    return "execute"


def route_after_execute(state: DockerPruneGraphState) -> str:
    """Route after execution: verify (live) or finish (dry-run)."""
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return END
    return "verify"


# --- Graph builder ---

def build_docker_prune_subgraph(
    executor: SandboxExecutor,
) -> StateGraph:
    """Construct the Docker prune sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.

    Returns:
        StateGraph for Docker prune (call .compile() to use).
    """
    builder: StateGraph = StateGraph(DockerPruneGraphState)

    async def _assess(state: DockerPruneGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _execute(state: DockerPruneGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: DockerPruneGraphState) -> dict[str, Any]:
        return await verify_node(state, executor=executor)

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("execute", _execute)
    builder.add_node("verify", _verify)

    builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_conditional_edges("assess", route_after_assess, ["execute", END])
    builder.add_conditional_edges("execute", route_after_execute, ["verify", END])
    builder.add_edge("verify", END)

    return builder
