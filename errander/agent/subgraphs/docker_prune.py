"""Docker prune sub-graph — reclaim disk from unused Docker resources.

Lifecycle:
1. Validate: Check Docker is available / not disabled by mode.
2. Assess: Count dangling images, stopped containers, reclaimable space.
   Idempotent — if nothing to prune, sets nothing_to_do=True.
3. Execute: Run docker system prune (or simulate in dry-run mode).
4. Verify: Re-check disk usage after pruning (skip in dry-run).

docker_command_mode controls how Docker is invoked:
  "wrapper"     — root-owned wrapper scripts at /usr/local/sbin/errander-docker-*.
                  This is the secure production default. Narrow sudoers, no raw
                  `sudo docker`.
  "direct_sudo" — `sudo -n /usr/bin/docker ...` directly. Lab/pre-prod only.
                  Logs a warning every batch. Not enterprise-hardened.
  "disabled"    — Errander will not plan or execute docker_prune on this env.

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

    # "wrapper" | "direct_sudo" | "disabled" (default: "wrapper")
    docker_command_mode: str

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
    """Validate that Docker is available and mode is not disabled.

    Uses docker_available from VM discovery. If not set, defaults to True
    (will fail at assess if Docker is actually missing).
    """
    mode = state.get("docker_command_mode", "wrapper")
    if mode == "disabled":
        logger.info(
            "Docker prune disabled for %s (docker_command_mode=disabled)",
            state.get("vm_id", "unknown"),
        )
        return {
            "status": ActionStatus.SKIPPED.value,
            "reason": "docker_command_mode=disabled",
        }

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
    Branches on docker_command_mode: wrapper calls the assess wrapper script;
    direct_sudo uses the original 4-call pattern.
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    mode = state.get("docker_command_mode", "wrapper")

    if mode == "wrapper":
        return await _assess_wrapper(vm_id, target, executor)
    # direct_sudo (disabled is already filtered by validate_node)
    return await _assess_direct(vm_id, target, executor)


async def _assess_wrapper(
    vm_id: str,
    target: dict[str, str],
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Assess via root-owned wrapper script."""
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/local/sbin/errander-docker-assess"),
        dry_run=False,
    )
    if not result.success:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "Docker assess wrapper failed",
            "nothing_to_do": True,
        }

    parsed = parse_assess_output(result.stdout)
    if not parsed["reachable"]:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": parsed["error"] or "Docker daemon not reachable",
            "nothing_to_do": True,
        }

    dangling = parsed["dangling_images"]
    stopped = parsed["stopped_containers"]
    system_df = parsed["system_df"]

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


async def _assess_direct(
    vm_id: str,
    target: dict[str, str],
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Assess via raw sudo -n /usr/bin/docker calls (direct_sudo mode)."""
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

    df_result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged("/usr/bin/docker system df 2>/dev/null"),
        dry_run=False,
    )
    system_df = df_result.stdout.strip() if df_result.success else ""

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

    Branches on docker_command_mode:
    - wrapper: calls errander-docker-prune-safe or errander-docker-prune-aggressive
    - direct_sudo: original behavior with sudo -n /usr/bin/docker

    Dry-run: docker system df (show reclaimable space).
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    dry_run = state.get("dry_run", True)
    aggressive = state.get("docker_prune_aggressive", False)
    mode = state.get("docker_command_mode", "wrapper")

    if mode == "wrapper":
        wrapper = (
            "errander-docker-prune-aggressive" if aggressive else "errander-docker-prune-safe"
        )
        live_cmd = privileged(f"/usr/local/sbin/{wrapper}")
        simulate_cmd = privileged("/usr/local/sbin/errander-docker-assess")
    else:
        # direct_sudo
        if aggressive:
            live_cmd = privileged("/usr/bin/docker system prune -af 2>&1")
        else:
            live_cmd = (
                privileged("/usr/bin/docker image prune -f 2>&1") + " && "
                + privileged("/usr/bin/docker container prune -f 2>&1")
            )
        simulate_cmd = privileged("/usr/bin/docker system df 2>/dev/null")

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=live_cmd,
        simulate_command=simulate_cmd,
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
    mode = state.get("docker_command_mode", "wrapper")

    if mode == "wrapper":
        cmd = privileged("/usr/local/sbin/errander-docker-assess")
    else:
        cmd = privileged("/usr/bin/docker system df 2>/dev/null")

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=cmd,
        dry_run=False,
    )

    if not result.success:
        return {"error": "Failed to verify Docker disk usage after prune"}

    if mode == "wrapper":
        parsed = parse_assess_output(result.stdout)
        disk_after = parsed["system_df"]
    else:
        disk_after = result.stdout.strip()

    return {"disk_after": disk_after}


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


def parse_assess_output(stdout: str) -> dict[str, Any]:
    """Parse the errander-docker-assess wrapper output.

    Expected format:
        reachable=yes|no
        dangling_images=N
        stopped_containers=N
        error=<optional error message>
        system_df_begin
        <raw docker system df output>
        system_df_end

    Returns a dict with parsed fields. Missing fields default to safe values.
    """
    result: dict[str, Any] = {
        "reachable": False,
        "dangling_images": 0,
        "stopped_containers": 0,
        "error": None,
        "system_df": "",
    }
    lines = stdout.splitlines()
    in_df_block = False
    df_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if line == "system_df_begin":
            in_df_block = True
            continue
        if line == "system_df_end":
            in_df_block = False
            continue
        if in_df_block:
            df_lines.append(line)
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "reachable":
            result["reachable"] = value.lower() in ("yes", "true", "1")
        elif key == "dangling_images":
            try:
                result["dangling_images"] = int(value)
            except ValueError:
                pass
        elif key == "stopped_containers":
            try:
                result["stopped_containers"] = int(value)
            except ValueError:
                pass
        elif key == "error":
            result["error"] = value or None
    result["system_df"] = "\n".join(df_lines)
    return result


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
