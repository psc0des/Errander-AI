"""Log rotation sub-graph — compress and rotate oversized log files.

Lifecycle:
1. Validate: Check target log directories are under /var/log.
2. Assess: Find log files exceeding size threshold. Idempotent — if none
   found, sets nothing_to_do=True and skips execution.
3. Execute: Force logrotate or manual gzip+truncate (or simulate in dry-run).
4. Verify: Re-check sizes after rotation (skip in dry-run).

Risk tier: Low (automatic).
Rollback strategy: None needed — logs are compressed, not deleted.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.execution.command_builder import CommandBuildError, safe_path
from errander.execution.sandbox import SandboxExecutor
from errander.models.actions import ActionStatus

logger = logging.getLogger(__name__)

# --- Allowed log directories ---

ALLOWED_LOG_PREFIXES: tuple[str, ...] = (
    "/var/log",
)


def is_valid_log_path(path: str) -> bool:
    """Check that a log path is under an allowed directory."""
    normalised = path.rstrip("/")
    return any(normalised == prefix or normalised.startswith(prefix + "/")
               for prefix in ALLOWED_LOG_PREFIXES)


# --- State ---

class LogRotationGraphState(TypedDict, total=False):
    """State flowing through the log rotation sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None

    log_paths: list[str]          # directories to scan (default: ["/var/log"])
    size_threshold_mb: int        # rotate logs larger than this (default: 100)
    compress: bool                # gzip rotated files (default: True)

    # Assessment results
    large_files: list[str]        # files exceeding threshold
    log_sizes: dict[str, str]     # file → human-readable size

    # Execution results
    rotation_output: dict[str, str]  # file → command output

    # Idempotency
    nothing_to_do: bool


# --- Node functions ---

def validate_node(state: LogRotationGraphState) -> dict[str, Any]:
    """Validate that all log paths are under allowed directories.

    HARDCODED CHECK — this is NEVER an LLM decision.
    """
    paths = state.get("log_paths", ["/var/log"])

    rejected = [p for p in paths if not is_valid_log_path(p)]
    if rejected:
        logger.error(
            "BLOCKED: log paths outside allowed directories for %s: %s",
            state.get("vm_id", "unknown"), rejected,
        )
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"Log paths outside allowed directories: {rejected}",
        }

    return {
        "log_paths": paths,
        "status": ActionStatus.PENDING.value,
    }


async def assess_node(
    state: LogRotationGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Find log files exceeding the size threshold.

    Idempotency: if no large files found, sets nothing_to_do=True.
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    threshold_mb = state.get("size_threshold_mb", 100)
    paths = state.get("log_paths", ["/var/log"])

    all_large_files: list[str] = []
    sizes: dict[str, str] = {}

    for log_dir in paths:
        # Find files larger than threshold
        cmd = (
            f"find {log_dir} -type f -size +{threshold_mb}M "
            f"-exec ls -lh {{}} \\; 2>/dev/null"
        )
        result = await executor.execute(
            vm_id, target["hostname"], target["username"], target["key_path"],
            command=cmd,
        )

        if not result.success:
            return {
                "status": ActionStatus.FAILED.value,
                "error": f"command failed (exit={result.exit_code}): {result.stderr[:200]}",
            }
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 9:
                    size = parts[4]
                    filepath = parts[-1]
                    all_large_files.append(filepath)
                    sizes[filepath] = size

    if not all_large_files:
        logger.info("No log files exceeding %dMB on %s — nothing to do", threshold_mb, vm_id)
        return {
            "large_files": [],
            "log_sizes": {},
            "nothing_to_do": True,
            "status": ActionStatus.SKIPPED.value,
        }

    logger.info(
        "Found %d log files exceeding %dMB on %s",
        len(all_large_files), threshold_mb, vm_id,
    )
    return {
        "large_files": all_large_files,
        "log_sizes": sizes,
        "nothing_to_do": False,
    }


async def execute_node(
    state: LogRotationGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Rotate oversized log files.

    Strategy: try logrotate --force first. If unavailable, fall back to
    manual gzip + truncate per file.

    In dry-run mode, uses ls -lh (show what would be rotated).
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    large_files = state.get("large_files", [])
    compress = state.get("compress", True)
    dry_run = state.get("dry_run", True)

    output: dict[str, str] = {}

    # Try system logrotate first
    logrotate_cmd = "logrotate --force /etc/logrotate.conf 2>&1"
    logrotate_sim = "logrotate --debug /etc/logrotate.conf 2>&1 | head -20"

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=logrotate_cmd,
        simulate_command=logrotate_sim,
        dry_run=dry_run,
    )

    if result.success:
        output["logrotate"] = result.stdout.strip()
    else:
        # Logrotate not available or failed — manual rotation per file
        logger.info("logrotate unavailable on %s, falling back to manual rotation", vm_id)
        for filepath in large_files:
            try:
                qp = safe_path(filepath)
                qp1 = safe_path(filepath + ".1")
            except CommandBuildError as exc:
                logger.error("Skipping log file with unsafe path on %s: %s", vm_id, exc)
                output[filepath] = "[SKIPPED — unsafe path]"
                continue
            if compress:
                live_cmd = (
                    f"cp {qp} {qp1} && "
                    f"gzip {qp1} && truncate -s 0 {qp}"
                )
            else:
                live_cmd = f"cp {qp} {qp1} && truncate -s 0 {qp}"
            sim_cmd = f"ls -lh {qp}"

            file_result = await executor.execute(
                vm_id, target["hostname"], target["username"], target["key_path"],
                command=live_cmd,
                simulate_command=sim_cmd,
                dry_run=dry_run,
            )
            output[filepath] = file_result.stdout.strip()

    status = ActionStatus.DRY_RUN_OK if dry_run else ActionStatus.SUCCESS
    return {
        "rotation_output": output,
        "status": status.value,
    }


async def verify_node(
    state: LogRotationGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Verify that oversized log files were reduced after rotation.

    Only runs in live mode.
    """
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return {}

    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    threshold_mb = state.get("size_threshold_mb", 100)
    paths = state.get("log_paths", ["/var/log"])

    remaining_large: list[str] = []
    for log_dir in paths:
        cmd = f"find {log_dir} -type f -size +{threshold_mb}M -ls 2>/dev/null | wc -l"
        result = await executor.execute(
            vm_id, target["hostname"], target["username"], target["key_path"],
            command=cmd,
        )
        if result.success:
            try:
                count = int(result.stdout.strip())
                if count > 0:
                    remaining_large.append(f"{log_dir}: {count} files still large")
            except ValueError:
                pass

    if remaining_large:
        logger.warning("Some log files still large on %s: %s", vm_id, remaining_large)
        return {"error": f"Files still exceeding threshold: {remaining_large}"}

    return {}


# --- Helpers ---

def _get_connection_params(state: LogRotationGraphState) -> dict[str, str]:
    """Extract SSH connection params from state."""
    return {
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("username", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("key_path", ""),  # type: ignore[typeddict-item]
    }


# --- Routing ---

def route_after_validate(state: LogRotationGraphState) -> str:
    """Route after validation: continue or abort."""
    if state.get("status") == ActionStatus.FAILED.value:
        return END
    return "assess"


def route_after_assess(state: LogRotationGraphState) -> str:
    """Route after assessment: skip if nothing to do (idempotency)."""
    if state.get("nothing_to_do"):
        return END
    return "execute"


def route_after_execute(state: LogRotationGraphState) -> str:
    """Route after execution: verify (live) or finish (dry-run)."""
    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return END
    return "verify"


# --- Graph builder ---

def build_log_rotation_subgraph(
    executor: SandboxExecutor,
) -> StateGraph:
    """Construct the log rotation sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.

    Returns:
        StateGraph for log rotation (call .compile() to use).
    """
    builder: StateGraph = StateGraph(LogRotationGraphState)

    async def _assess(state: LogRotationGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _execute(state: LogRotationGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: LogRotationGraphState) -> dict[str, Any]:
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
