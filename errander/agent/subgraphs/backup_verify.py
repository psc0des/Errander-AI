"""Backup verification sub-graph — confirm backups exist and are recent.

Lifecycle:
1. Validate: Check backup paths are configured.
2. Assess: For each path, check existence, size, and modification time.
3. Verify: Flag missing, stale, or zero-size backups.

This is a read-only verification action — no execute step, no state changes.
Inherently idempotent.

Risk tier: High (human approval required).
Rollback strategy: N/A — no state changes made.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.execution.command_builder import CommandBuildError, safe_path
from errander.models.actions import ActionStatus

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


# --- State ---

class BackupVerifyGraphState(TypedDict, total=False):
    """State flowing through the backup verification sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None

    backup_paths: list[str]       # paths to verify
    max_age_hours: int            # backups older than this are stale (default: 24)

    # Assessment results
    backup_metadata: list[dict[str, str]]  # path, size, last_modified per backup

    # Verification results
    issues: list[str]             # missing, stale, or zero-size backups
    verify_output: str            # summary of verification


# --- Node functions ---

def validate_node(state: BackupVerifyGraphState) -> dict[str, Any]:
    """Validate that backup paths are configured.

    If no paths are provided, there is nothing to verify.
    """
    paths = state.get("backup_paths", [])

    if not paths:
        logger.info(
            "No backup paths configured for %s — skipping verification",
            state.get("vm_id", "unknown"),
        )
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "No backup paths configured",
        }

    return {"status": ActionStatus.PENDING.value}


async def assess_node(
    state: BackupVerifyGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Check existence, size, and modification time of each backup path.

    Uses stat to get file metadata. Handles missing files gracefully.
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    paths = state.get("backup_paths", [])

    metadata: list[dict[str, str]] = []

    for path in paths:
        try:
            quoted = safe_path(path)
        except CommandBuildError as exc:
            logger.error("Skipping backup path with unsafe chars on %s: %s", vm_id, exc)
            metadata.append({
                "path": path,
                "size": "0",
                "last_modified": "0",
                "exists": "false",
            })
            continue
        # stat -c '%s %Y %n' gives: size_bytes epoch_mtime filename
        # dry_run=False: backup verification is read-only — always check real VM state.
        cmd = f"stat -c '%s %Y %n' {quoted} 2>/dev/null || echo 'MISSING {quoted}'"
        result = await executor.execute(
            vm_id, target["hostname"], target["username"], target["key_path"],
            command=cmd,
            dry_run=False,
        )

        output = result.stdout.strip()
        if output.startswith("MISSING"):
            metadata.append({
                "path": path,
                "size": "0",
                "last_modified": "0",
                "exists": "false",
            })
        else:
            parts = output.split(None, 2)
            if len(parts) >= 3:
                metadata.append({
                    "path": parts[2],
                    "size": parts[0],
                    "last_modified": parts[1],
                    "exists": "true",
                })
            else:
                metadata.append({
                    "path": path,
                    "size": "0",
                    "last_modified": "0",
                    "exists": "false",
                })

    return {"backup_metadata": metadata}


def verify_node(state: BackupVerifyGraphState) -> dict[str, Any]:
    """Check each backup against criteria: exists, recent, non-zero size.

    Flags:
    - MISSING: file does not exist
    - STALE: file older than max_age_hours
    - EMPTY: file has zero size
    """
    metadata = state.get("backup_metadata", [])
    max_age_hours = state.get("max_age_hours", 24)
    now = time.time()
    max_age_seconds = max_age_hours * 3600

    issues: list[str] = []
    healthy = 0

    for entry in metadata:
        path = entry.get("path", "unknown")
        exists = entry.get("exists", "false") == "true"

        if not exists:
            issues.append(f"MISSING: {path}")
            continue

        size = int(entry.get("size", "0"))
        if size == 0:
            issues.append(f"EMPTY: {path} (zero bytes)")
            continue

        mtime = float(entry.get("last_modified", "0"))
        age_seconds = now - mtime
        if age_seconds > max_age_seconds:
            age_hours = age_seconds / 3600
            issues.append(
                f"STALE: {path} (last modified {age_hours:.1f}h ago, "
                f"threshold {max_age_hours}h)"
            )
            continue

        healthy += 1

    # Build summary
    total = len(metadata)
    summary_parts = [f"Verified {total} backup paths: {healthy} healthy"]
    if issues:
        summary_parts.append(f", {len(issues)} issues found")

    summary = "".join(summary_parts)
    if issues:
        summary += "\n" + "\n".join(f"  - {i}" for i in issues)

    status = ActionStatus.SUCCESS if not issues else ActionStatus.NEEDS_MANUAL
    logger.info(
        "Backup verification on %s: %d healthy, %d issues",
        state.get("vm_id", "unknown"), healthy, len(issues),
    )

    return {
        "issues": issues,
        "verify_output": summary,
        "status": status.value,
    }


# --- Helpers ---

def _get_connection_params(state: BackupVerifyGraphState) -> dict[str, str]:
    """Extract SSH connection params from state."""
    return {
        "hostname": state.get("hostname", ""),
        "username": state.get("username", ""),
        "key_path": state.get("key_path", ""),
    }


# --- Routing ---

def route_after_validate(state: BackupVerifyGraphState) -> str:
    """Route after validation: continue or abort."""
    if state.get("status") == ActionStatus.SKIPPED.value:
        return END
    return "assess"


# --- Graph builder ---

def build_backup_verify_subgraph(
    executor: SandboxExecutor,
) -> StateGraph[BackupVerifyGraphState]:
    """Construct the backup verification sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.

    Returns:
        StateGraph for backup verification (call .compile() to use).
    """
    builder: StateGraph[BackupVerifyGraphState] = StateGraph(BackupVerifyGraphState)

    async def _assess(state: BackupVerifyGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("verify", verify_node)

    builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_edge("assess", "verify")
    builder.add_edge("verify", END)

    return builder
