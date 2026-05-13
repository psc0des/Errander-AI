"""Per-VM maintenance graph — processes one VM through its full lifecycle.

Graph: lock → discover → plan → dispatch → check_more → audit → unlock

Each VM gets its own instance of this graph via Send() from the batch
orchestrator. The graph acquires a lock, discovers system state, plans
actions, dispatches each to the appropriate sub-graph, audits results,
and releases the lock.

Dependencies (injected at build time):
- SandboxExecutor: dry-run/live command execution
- FileLocker: VM-level locking
- AuditStore: audit trail
- SSHConnectionManager: SSH connections for OS detection
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.agent.decisions import prioritize_actions
from errander.safety.ai_audit import AIDecisionStore
from errander.safety.drift import compare_states, load_baseline, save_baseline
from errander.agent.subgraphs.backup_verify import (
    BackupVerifyGraphState,
    build_backup_verify_subgraph,
)
from errander.agent.subgraphs.disk_cleanup import (
    DiskCleanupGraphState,
    build_disk_cleanup_subgraph,
)
from errander.agent.subgraphs.docker_prune import (
    DockerPruneGraphState,
    build_docker_prune_subgraph,
)
from errander.agent.subgraphs.log_rotation import (
    LogRotationGraphState,
    build_log_rotation_subgraph,
)
from errander.agent.subgraphs.patching import (
    PatchingGraphState,
    build_patching_subgraph,
)
from errander.execution.os_detection import detect_os
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager
from errander.models.actions import (
    Action,
    ActionStatus,
    ActionType,
    RiskTier,
)
from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import ACTION_DURATION, ACTIONS_TOTAL, VM_LOCK_HELD
from errander.safety.audit import AuditStore
from errander.safety.locking import FileLocker
from errander.safety.validators import validate_action

logger = logging.getLogger(__name__)


# --- State ---

class VMGraphState(TypedDict, total=False):
    """State flowing through the per-VM maintenance graph."""

    # Identity
    vm_id: str
    batch_id: str
    dry_run: bool

    # Connection params (flattened from VMTarget for sub-graph injection)
    hostname: str
    ssh_user: str
    ssh_key_path: str
    os_family: str  # detected at runtime, updated after discover

    # Discovery
    vm_info: dict[str, object]  # serialized VMInfo fields

    # Planning
    planned_actions: list[dict[str, object]]  # serialized Action objects
    # True when batch-level approved plan was explicitly injected — distinguishes
    # "approved empty plan" (execute nothing) from "no plan yet" (re-plan allowed).
    pre_approved_plan_set: bool
    current_action_index: int

    # Results (accumulated across dispatch iterations)
    results: list[dict[str, object]]

    # Approval policy from environment config (relaxed / moderate / strict)
    env_policy: str

    # Per-decision AI audit (finding #3.4) — passed through from batch state
    # We store the db_path string rather than the store object (TypedDict must be serializable)
    ai_db_path: str

    # Lock state
    locked: bool
    lock_acquired_at: str | None
    error: str | None

    # Drift detection
    drift_detection_enabled: bool
    drift_abort_on_detection: bool
    drift_result: dict[str, object] | None

    # Disk growth trend (1.4)
    disk_growth_alerts: list[dict[str, object]]  # serialised DiskGrowth alerts

    # Configuration drift baselines (1.5)
    drift_changes: list[dict[str, object]]  # serialised DriftChange objects
    failed_login_summary: dict[str, object] | None  # serialised FailedLoginSummary

    # Per-VM opt-out: skip failed SSH login probe for this VM (e.g., honeypots).
    disable_failed_login_check: bool

    # Critical services monitored pre/post patching for health regressions.
    critical_services: list[str]


# --- Node functions ---

async def acquire_lock_node(
    state: VMGraphState,
    *,
    locker: FileLocker,
) -> dict[str, Any]:
    """Acquire a maintenance lock for this VM."""
    vm_id = state["vm_id"]
    batch_id = state.get("batch_id", "unknown")

    acquired = await locker.acquire(vm_id, batch_id)
    if not acquired:
        logger.warning("Could not acquire lock for %s — skipping", vm_id)
        return {
            "locked": False,
            "error": f"VM {vm_id} is already locked by another batch",
        }

    return {"locked": True, "lock_acquired_at": datetime.now(tz=timezone.utc).isoformat()}


async def discover_node(
    state: VMGraphState,
    *,
    ssh_manager: SSHConnectionManager,
) -> dict[str, Any]:
    """Discover system state via SSH (OS, disk, docker, packages, uptime)."""
    vm_id = state["vm_id"]
    hostname = state["hostname"]
    ssh_user = state["ssh_user"]
    key_path = state["ssh_key_path"]

    try:
        vm_info = await detect_os(
            vm_id=vm_id,
            hostname=hostname,
            username=ssh_user,
            key_path=key_path,
            ssh_manager=ssh_manager,
        )
    except (ValueError, ConnectionError, OSError) as exc:
        logger.error("Discovery failed for %s: %s", vm_id, exc)
        return {"error": f"Discovery failed: {exc}"}

    return {
        "vm_info": {
            "os_family": vm_info.os_family.value,
            "os_version": vm_info.os_version,
            "disk_usage": vm_info.disk_usage,
            "docker_available": vm_info.docker_available,
            "pending_packages": vm_info.pending_packages,
            "uptime_seconds": vm_info.uptime_seconds,
        },
        "os_family": vm_info.os_family.value,
    }


async def plan_actions_node(
    state: VMGraphState,
    *,
    llm_client: Any = None,
    ai_decision_store: AIDecisionStore | None = None,
) -> dict[str, Any]:
    """Plan and prioritize actions based on discovered state.

    Calls prioritize_actions() with optional LLMClient (finding #3.1).
    Falls back to hardcoded priority when LLM is unavailable.
    Per-decision audit logged to ai_decisions table (finding #3.4).
    """
    from errander.models.vm import OSFamily, VMInfo

    vm_info_dict = state.get("vm_info", {})
    vm_info = VMInfo(
        os_family=OSFamily(str(vm_info_dict.get("os_family", "ubuntu"))),
        os_version=str(vm_info_dict.get("os_version", "")),
        disk_usage=dict(vm_info_dict.get("disk_usage", {})),  # type: ignore[arg-type]
        docker_available=bool(vm_info_dict.get("docker_available", False)),
        pending_packages=int(vm_info_dict.get("pending_packages", 0)),
        uptime_seconds=float(vm_info_dict.get("uptime_seconds", 0.0)),
    )

    actions = await prioritize_actions(
        vm_info,
        llm_client=llm_client,
        policy=state.get("env_policy", "moderate"),
        batch_id=state.get("batch_id", "unknown"),
        vm_id=state.get("vm_id"),
        ai_store=ai_decision_store,
    )

    return {
        "planned_actions": [
            {
                "action_type": a.action_type.value,
                "risk_tier": a.risk_tier.value,
                "params": a.params,
            }
            for a in actions
        ],
        "current_action_index": 0,
    }


async def drift_check_node(
    state: VMGraphState,
    *,
    audit_store: AuditStore,
) -> dict[str, Any]:
    """Compare discovered state against stored baseline.

    Pass-through if drift detection is disabled or no baseline exists.
    If drift detected and abort_on_detection is True, sets error to skip VM.
    """
    vm_id = state["vm_id"]
    enabled = state.get("drift_detection_enabled", False)

    if not enabled:
        return {}

    vm_info = state.get("vm_info")
    if not vm_info:
        return {}

    baseline = await load_baseline(audit_store, vm_id)
    if baseline is None:
        logger.info("No drift baseline for %s — first run", vm_id)
        return {"drift_result": {"has_drift": False, "drifts": [], "baseline_found": False}}

    result = compare_states(baseline, dict(vm_info))
    drift_dict: dict[str, object] = {
        "has_drift": result.has_drift,
        "drifts": result.drifts,
        "baseline_found": result.baseline_found,
    }

    if result.has_drift:
        logger.warning("Drift detected on %s: %s", vm_id, "; ".join(result.drifts))
        await audit_store.log_event(
            AuditEvent(
                event_type=EventType.DRIFT_DETECTED,
                batch_id=state.get("batch_id", "unknown"),
                vm_id=vm_id,
                detail=f"Drift detected: {'; '.join(result.drifts)}",
                timestamp=datetime.now(tz=timezone.utc),
                metadata={"drifts": result.drifts},
            )
        )
        if state.get("drift_abort_on_detection", False):
            return {
                "drift_result": drift_dict,
                "error": f"Drift detected, aborting: {'; '.join(result.drifts)}",
            }

    return {"drift_result": drift_dict}


async def dispatch_action_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    disk_cleanup_compiled: Any = None,
    log_rotation_compiled: Any = None,
    docker_prune_compiled: Any = None,
    patching_compiled: Any = None,
    backup_verify_compiled: Any = None,
) -> dict[str, Any]:
    """Dispatch the current action to its sub-graph."""
    index = state.get("current_action_index", 0)
    planned = state.get("planned_actions", [])

    if index >= len(planned):
        return {}

    action = planned[index]
    action_type = action["action_type"]
    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    now = datetime.now(tz=timezone.utc)

    # Pre-dispatch validation
    try:
        action_obj = Action(
            action_type=ActionType(str(action_type)),
            risk_tier=RiskTier(str(action.get("risk_tier", "medium"))),
            params=dict(action.get("params", {})),  # type: ignore[arg-type]
        )
    except ValueError:
        logger.warning("Unknown action type %s on %s — skipping", action_type, vm_id)
        result_dict = {
            "action_type": action_type,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "detail": f"Unknown action type: {action_type}",
        }
        existing = list(state.get("results", []))
        existing.append(result_dict)
        return {
            "results": existing,
            "current_action_index": index + 1,
        }
    env_policy = state.get("env_policy", "moderate")
    is_valid, reason = await validate_action(action_obj, vm_id, os_family, policy=env_policy)
    if not is_valid:
        logger.warning("Validation blocked %s on %s: %s", action_type, vm_id, reason)
        result_dict = {
            "action_type": action_type,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "detail": f"Validation failed: {reason}",
        }
        existing = list(state.get("results", []))
        existing.append(result_dict)
        return {
            "results": existing,
            "current_action_index": index + 1,
        }

    action_start = datetime.now(tz=timezone.utc)

    if action_type == ActionType.DISK_CLEANUP.value:
        result_dict = await _run_disk_cleanup(state, disk_cleanup_compiled)
    elif action_type == ActionType.LOG_ROTATION.value:
        result_dict = await _run_log_rotation(state, log_rotation_compiled)
    elif action_type == ActionType.DOCKER_PRUNE.value:
        result_dict = await _run_docker_prune(state, docker_prune_compiled)
    elif action_type == ActionType.PATCHING.value:
        result_dict = await _run_patching(state, patching_compiled)
    elif action_type == ActionType.BACKUP_VERIFY.value:
        result_dict = await _run_backup_verify(state, backup_verify_compiled)
    else:
        logger.warning("Unknown action type %s for %s — skipping", action_type, vm_id)
        result_dict = {
            "action_type": action_type,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "detail": f"Unknown action type: {action_type}",
        }

    # Record metrics
    duration = (datetime.now(tz=timezone.utc) - action_start).total_seconds()
    status_str = str(result_dict.get("status", ""))
    ACTIONS_TOTAL.labels(action_type=action_type, status=status_str, vm_id=vm_id).inc()
    ACTION_DURATION.labels(action_type=action_type).observe(duration)

    existing = list(state.get("results", []))
    existing.append(result_dict)

    return {
        "results": existing,
        "current_action_index": index + 1,
    }


async def _run_disk_cleanup(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the disk cleanup sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=timezone.utc)

    sub_state: DiskCleanupGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("ssh_user", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("ssh_key_path", ""),  # type: ignore[typeddict-item]
    }

    try:
        final_state = await compiled.ainvoke(sub_state)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph disk_cleanup failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.DISK_CLEANUP.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in disk_cleanup for %s", vm_id)
        return {
            "action_type": ActionType.DISK_CLEANUP.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    error = final_state.get("error")

    detail_parts: list[str] = []
    if final_state.get("space_by_path"):
        detail_parts.append(f"assessed: {final_state['space_by_path']}")
    if final_state.get("cleanup_output"):
        detail_parts.append(f"cleaned: {list(final_state['cleanup_output'].keys())}")

    return {
        "action_type": ActionType.DISK_CLEANUP.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": error,
    }


async def _run_log_rotation(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the log rotation sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=timezone.utc)

    sub_state: LogRotationGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("ssh_user", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("ssh_key_path", ""),  # type: ignore[typeddict-item]
    }

    try:
        final_state = await compiled.ainvoke(sub_state)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph log_rotation failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.LOG_ROTATION.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in log_rotation for %s", vm_id)
        return {
            "action_type": ActionType.LOG_ROTATION.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("large_files"):
        detail_parts.append(f"large files: {len(final_state['large_files'])}")
    if final_state.get("nothing_to_do"):
        detail_parts.append("nothing to do — already clean")
    if final_state.get("rotation_output"):
        detail_parts.append(f"rotated: {list(final_state['rotation_output'].keys())}")

    return {
        "action_type": ActionType.LOG_ROTATION.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _run_docker_prune(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the Docker prune sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=timezone.utc)
    vm_info = state.get("vm_info", {})

    sub_state: DockerPruneGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "docker_available": bool(vm_info.get("docker_available", True)),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("ssh_user", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("ssh_key_path", ""),  # type: ignore[typeddict-item]
    }

    try:
        final_state = await compiled.ainvoke(sub_state)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph docker_prune failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.DOCKER_PRUNE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in docker_prune for %s", vm_id)
        return {
            "action_type": ActionType.DOCKER_PRUNE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("dangling_images") is not None:
        detail_parts.append(f"dangling images: {final_state['dangling_images']}")
    if final_state.get("stopped_containers") is not None:
        detail_parts.append(f"stopped containers: {final_state['stopped_containers']}")
    if final_state.get("nothing_to_do"):
        detail_parts.append("nothing to do — already clean")
    if final_state.get("prune_output"):
        detail_parts.append("pruned")

    return {
        "action_type": ActionType.DOCKER_PRUNE.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _run_patching(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the patching sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=timezone.utc)

    sub_state: PatchingGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("ssh_user", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("ssh_key_path", ""),  # type: ignore[typeddict-item]
        "batch_id": state.get("batch_id", ""),  # type: ignore[typeddict-item]
        "critical_services": list(state.get("critical_services") or []),  # type: ignore[typeddict-item]
    }

    try:
        final_state = await compiled.ainvoke(sub_state)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph patching failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.PATCHING.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in patching for %s", vm_id)
        return {
            "action_type": ActionType.PATCHING.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("pending_updates"):
        detail_parts.append(f"updates: {len(final_state['pending_updates'])} packages")
    if final_state.get("nothing_to_do"):
        detail_parts.append("nothing to do — already up-to-date")
    if final_state.get("version_snapshot"):
        detail_parts.append(f"snapshot: {len(final_state['version_snapshot'])} packages")

    return {
        "action_type": ActionType.PATCHING.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _run_backup_verify(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the backup verify sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=timezone.utc)

    # Get backup paths from action params if available
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    backup_paths: list[str] = []
    if index < len(planned):
        params = planned[index].get("params", {})
        backup_paths = list(params.get("backup_paths", []))  # type: ignore[union-attr]

    sub_state: BackupVerifyGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "backup_paths": backup_paths,
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-item]
        "username": state.get("ssh_user", ""),  # type: ignore[typeddict-item]
        "key_path": state.get("ssh_key_path", ""),  # type: ignore[typeddict-item]
    }

    try:
        final_state = await compiled.ainvoke(sub_state)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph backup_verify failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.BACKUP_VERIFY.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in backup_verify for %s", vm_id)
        return {
            "action_type": ActionType.BACKUP_VERIFY.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("issues"):
        detail_parts.append(f"issues: {len(final_state['issues'])}")
    if final_state.get("verify_output"):
        detail_parts.append(final_state["verify_output"].split("\n")[0])

    return {
        "action_type": ActionType.BACKUP_VERIFY.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def audit_results_node(
    state: VMGraphState,
    *,
    audit_store: AuditStore,
) -> dict[str, Any]:
    """Write all action results to the audit trail."""
    vm_id = state["vm_id"]
    batch_id = state.get("batch_id", "unknown")
    results = state.get("results", [])
    error = state.get("error")

    for r in results:
        event_type = (
            EventType.ACTION_COMPLETED
            if r.get("status") in (
                ActionStatus.SUCCESS.value,
                ActionStatus.DRY_RUN_OK.value,
                ActionStatus.SKIPPED.value,
            )
            else EventType.ACTION_FAILED
        )
        await audit_store.log_event(
            AuditEvent(
                event_type=event_type,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type=str(r.get("action_type", "")),
                detail=str(r.get("detail", "")),
                timestamp=datetime.now(tz=timezone.utc),
                metadata={
                    "status": str(r.get("status", "")),
                    "error": r.get("error"),
                },
            )
        )

    if error:
        await audit_store.log_event(
            AuditEvent(
                event_type=EventType.ACTION_FAILED,
                batch_id=batch_id,
                vm_id=vm_id,
                detail=error,
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

    # Save drift baseline if enabled and run was successful
    if state.get("drift_detection_enabled", False) and not error:
        vm_info = state.get("vm_info")
        if vm_info:
            has_success = any(
                r.get("status") in (
                    ActionStatus.SUCCESS.value,
                    ActionStatus.DRY_RUN_OK.value,
                )
                for r in results
            )
            if has_success:
                try:
                    await save_baseline(audit_store, vm_id, dict(vm_info))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Baseline save failed for %s: %s", vm_id, exc)

    return {}


async def release_lock_node(
    state: VMGraphState,
    *,
    locker: FileLocker,
) -> dict[str, Any]:
    """Release the VM lock. Always runs, even on error paths."""
    vm_id = state["vm_id"]
    batch_id = state.get("batch_id", "unknown")

    if state.get("locked"):
        released = await locker.release(vm_id, batch_id)
        if not released:
            logger.warning("Failed to release lock for %s", vm_id)
        # Record how long the lock was held
        acquired_at_str = state.get("lock_acquired_at")
        if acquired_at_str:
            acquired_at = datetime.fromisoformat(str(acquired_at_str))
            if acquired_at.tzinfo is None:
                acquired_at = acquired_at.replace(tzinfo=timezone.utc)
            held_seconds = (datetime.now(tz=timezone.utc) - acquired_at).total_seconds()
            VM_LOCK_HELD.labels(vm_id=vm_id).observe(held_seconds)
        return {"locked": False}

    return {}


async def disk_snapshot_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    disk_history_store: object,
    audit_store: AuditStore,
    settings: object,
) -> dict[str, Any]:
    """Capture disk usage, record to history, detect growth alerts.

    Runs after discover (VM reachable and creds known).  SSH failure is
    best-effort — never blocks the maintenance run.

    Args:
        state: Current VM graph state.
        executor: SandboxExecutor for SSH.
        disk_history_store: VMDiskHistoryStore (typed as object for TC001).
        audit_store: AuditStore for DISK_USAGE_CAPTURED events.
        settings: DiskGrowthSettings (typed as object for TC001).
    """
    from errander.config.settings import DiskGrowthSettings
    from errander.execution.disk_trend import record_and_detect_disk_growth
    from errander.safety.disk_history import VMDiskHistoryStore

    vm_id = state["vm_id"]
    vm_info = state.get("vm_info") or {}
    hostname = str(vm_info.get("hostname") or state.get("hostname", ""))
    username = str(vm_info.get("ssh_user") or state.get("ssh_user", ""))
    key_path = str(vm_info.get("ssh_key_path") or state.get("ssh_key_path", ""))

    if not isinstance(disk_history_store, VMDiskHistoryStore):
        return {"disk_growth_alerts": []}
    if not isinstance(settings, DiskGrowthSettings):
        return {"disk_growth_alerts": []}

    alerts = await record_and_detect_disk_growth(
        executor, vm_id, hostname, username, key_path,
        disk_history_store, settings,
    )

    # Emit one DISK_USAGE_CAPTURED event per growth alert detected
    for alert in alerts:
        await audit_store.log_event(AuditEvent(
            event_type=EventType.DISK_USAGE_CAPTURED,
            batch_id=state.get("batch_id", ""),
            vm_id=vm_id,
            action_type="disk_trend",
            detail=(
                f"{alert.mountpoint}: {alert.used_pct_start:.1f}% → "
                f"{alert.used_pct_end:.1f}% (+{alert.delta_pct:.1f}%) "
                f"over {settings.window_days}d"  # type: ignore[union-attr]
            ),
            metadata={
                "mountpoint": alert.mountpoint,
                "used_pct_start": alert.used_pct_start,
                "used_pct_end": alert.used_pct_end,
                "delta_pct": alert.delta_pct,
                "window_days": settings.window_days,  # type: ignore[union-attr]
            },
        ), dry_run=state.get("dry_run", True))

    serialised = [
        {
            "vm_id": a.vm_id,
            "mountpoint": a.mountpoint,
            "used_pct_start": a.used_pct_start,
            "used_pct_end": a.used_pct_end,
            "window_start": a.window_start.isoformat(),
            "window_end": a.window_end.isoformat(),
        }
        for a in alerts
    ]
    return {"disk_growth_alerts": serialised}


async def drift_baseline_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    baseline_store: object,
    audit_store: AuditStore | None,
    settings: object,
) -> dict[str, Any]:
    """Capture per-kind configuration baselines and emit drift events.

    Runs after disk_snapshot (or discover when disk snapshot is disabled).
    SSH failures per-check are best-effort — never blocks the maintenance run.

    Args:
        state: Current VM graph state.
        executor: SandboxExecutor for SSH.
        baseline_store: BaselineStore (typed as object for TC001).
        audit_store: AuditStore for DRIFT_KIND_CHANGED / _BASELINE_SAVED events.
        settings: DriftSettings (typed as object for TC001).
    """
    from datetime import UTC

    from errander.config.settings import DriftSettings
    from errander.safety.baselines import BaselineStore
    from errander.safety.drift_checks import (
        capture_authorized_keys,
        capture_listening_ports,
        capture_scheduled_jobs,
        capture_sudoers,
    )

    if not isinstance(baseline_store, BaselineStore):
        return {"drift_changes": []}
    if not isinstance(settings, DriftSettings):
        return {"drift_changes": []}

    vm_id = state["vm_id"]
    vm_info = state.get("vm_info") or {}
    hostname = str(vm_info.get("hostname") or state.get("hostname", ""))
    username = str(vm_info.get("ssh_user") or state.get("ssh_user", ""))
    key_path = str(vm_info.get("ssh_key_path") or state.get("ssh_key_path", ""))
    batch_id = state.get("batch_id", "")
    dry_run = state.get("dry_run", True)

    capture_fns = []
    if settings.sudoers:
        capture_fns.append(capture_sudoers)
    if settings.authorized_keys:
        capture_fns.append(capture_authorized_keys)
    if settings.listening_ports:
        capture_fns.append(capture_listening_ports)
    if settings.scheduled_jobs:
        capture_fns.append(capture_scheduled_jobs)

    changes: list[dict[str, object]] = []

    for capture_fn in capture_fns:
        try:
            captures = await capture_fn(executor, vm_id, hostname, username, key_path)
        except Exception:
            logger.exception("drift_baseline: %s capture failed on %s", capture_fn.__name__, vm_id)
            continue

        for capture in captures:
            comparison = await baseline_store.compare_and_save(vm_id, capture)

            if comparison.is_first_run:
                event_type = EventType.DRIFT_KIND_BASELINE_SAVED
                detail = f"{capture.kind}:{capture.scope_key} — first baseline saved"
            elif comparison.changed:
                event_type = EventType.DRIFT_KIND_CHANGED
                detail = f"{capture.kind}:{capture.scope_key} changed"
            else:
                continue  # unchanged — no event, no change recorded

            if audit_store is not None:
                await audit_store.log_event(
                    AuditEvent(
                        event_type=event_type,
                        batch_id=batch_id,
                        vm_id=vm_id,
                        detail=detail,
                        timestamp=datetime.now(tz=UTC),
                        metadata={
                            "kind": capture.kind,
                            "scope_key": capture.scope_key,
                        },
                    ),
                    dry_run=dry_run,
                )

            if comparison.changed:
                diff = comparison.unified_diff
                diff_lines = diff.splitlines()
                max_lines = settings.diff_max_lines
                if len(diff_lines) > max_lines:
                    diff = "\n".join(diff_lines[:max_lines])
                    diff += f"\n... ({len(diff_lines) - max_lines} more lines truncated)"
                changes.append({
                    "vm_id": vm_id,
                    "kind": capture.kind,
                    "scope_key": capture.scope_key,
                    "unified_diff": diff,
                })

    return {"drift_changes": changes}


async def failed_logins_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    audit_store: AuditStore | None,
    settings: object,
) -> dict[str, Any]:
    """Probe for failed SSH logins and emit a summary audit event.

    SSH failure → empty result (best-effort, never blocks maintenance).

    Args:
        state: Current VM graph state.
        executor: SandboxExecutor for SSH.
        audit_store: AuditStore for FAILED_SSH_LOGINS_OBSERVED events.
        settings: FailedSSHLoginsSettings (typed as object for TC001).
    """
    from datetime import UTC

    from errander.config.settings import FailedSSHLoginsSettings
    from errander.execution.failed_logins import detect_failed_logins

    if not isinstance(settings, FailedSSHLoginsSettings):
        return {"failed_login_summary": None}

    if state.get("disable_failed_login_check", False):
        logger.info("failed_logins: probe skipped for %s (disable_failed_login_check=true)", state.get("vm_id"))
        return {"failed_login_summary": None}

    vm_id = state["vm_id"]
    vm_info = state.get("vm_info") or {}
    hostname = str(vm_info.get("hostname") or state.get("hostname", ""))
    username = str(vm_info.get("ssh_user") or state.get("ssh_user", ""))
    key_path = str(vm_info.get("ssh_key_path") or state.get("ssh_key_path", ""))
    batch_id = state.get("batch_id", "")
    dry_run = state.get("dry_run", True)

    summary = await detect_failed_logins(
        executor, vm_id, hostname, username, key_path, settings,
    )
    if summary is None:
        return {"failed_login_summary": None}

    if summary.total_count > 0 and audit_store is not None:
        await audit_store.log_event(
            AuditEvent(
                event_type=EventType.FAILED_SSH_LOGINS_OBSERVED,
                batch_id=batch_id,
                vm_id=vm_id,
                detail=(
                    f"{summary.total_count} failed SSH logins"
                    f" in {summary.window_hours}h window"
                ),
                timestamp=datetime.now(tz=UTC),
                metadata={"total_count": str(summary.total_count)},
            ),
            dry_run=dry_run,
        )

    serialised: dict[str, object] = {
        "vm_id": vm_id,
        "window_hours": summary.window_hours,
        "total_count": summary.total_count,
        "top_users": [[u, c] for u, c in summary.top_users],
        "top_source_ips": [[ip, c] for ip, c in summary.top_source_ips],
    }
    return {"failed_login_summary": serialised}


# --- Routing ---

def route_after_lock(state: VMGraphState) -> str:
    if state.get("locked"):
        return "discover"
    return "audit_results"


def route_after_discover(state: VMGraphState) -> str:
    if state.get("error"):
        return "audit_results"
    return "drift_check"


def route_after_drift_check(state: VMGraphState) -> str:
    if state.get("error"):
        return "audit_results"
    if state.get("pre_approved_plan_set"):
        # Approved plan was explicitly injected — never re-plan after approval.
        # Empty approved plan means operator approved "do nothing" for this VM.
        actions = state.get("planned_actions") or []
        if actions:
            logger.info(
                "VM %s using pre-approved plan (%d actions) — skipping re-plan",
                state.get("vm_id"), len(actions),
            )
            return "dispatch_action"
        logger.info("VM %s: approved plan has zero actions — skipping to audit", state.get("vm_id"))
        return "audit_results"
    return "plan_actions"


def route_check_more(state: VMGraphState) -> str:
    index = state.get("current_action_index", 0)
    planned = state.get("planned_actions", [])
    if index < len(planned):
        return "dispatch_action"
    return "audit_results"


# --- Graph builder ---

def build_vm_graph(
    executor: SandboxExecutor,
    locker: FileLocker,
    audit_store: AuditStore,
    ssh_manager: SSHConnectionManager,
    llm_client: Any = None,
    ai_decision_store: AIDecisionStore | None = None,
    disk_history_store: object = None,
    sre_disk_settings: object = None,
    baseline_store: object = None,
    sre_drift_settings: object = None,
    sre_failed_logins_settings: object = None,
    vm_state_store: object = None,
) -> StateGraph:
    """Construct the per-VM maintenance graph.

    Args:
        executor: SandboxExecutor for SSH command execution.
        locker: FileLocker for VM-level locking.
        audit_store: AuditStore for audit trail.
        ssh_manager: SSHConnectionManager for OS detection.

    Returns:
        StateGraph for per-VM maintenance (call .compile() to use).
    """
    builder: StateGraph = StateGraph(VMGraphState)

    # Compile sub-graphs once — reused across all dispatches
    disk_cleanup_compiled = build_disk_cleanup_subgraph(executor).compile()
    log_rotation_compiled = build_log_rotation_subgraph(executor).compile()
    docker_prune_compiled = build_docker_prune_subgraph(executor).compile()
    patching_compiled = build_patching_subgraph(
        executor,
        audit_store=audit_store,
        vm_state_store=vm_state_store,  # type: ignore[arg-type]
    ).compile()
    backup_verify_compiled = build_backup_verify_subgraph(executor).compile()

    async def _acquire(state: VMGraphState) -> dict[str, Any]:
        return await acquire_lock_node(state, locker=locker)

    async def _discover(state: VMGraphState) -> dict[str, Any]:
        return await discover_node(state, ssh_manager=ssh_manager)

    async def _dispatch(state: VMGraphState) -> dict[str, Any]:
        return await dispatch_action_node(
            state,
            executor=executor,
            disk_cleanup_compiled=disk_cleanup_compiled,
            log_rotation_compiled=log_rotation_compiled,
            docker_prune_compiled=docker_prune_compiled,
            patching_compiled=patching_compiled,
            backup_verify_compiled=backup_verify_compiled,
        )

    async def _drift_check(state: VMGraphState) -> dict[str, Any]:
        return await drift_check_node(state, audit_store=audit_store)

    async def _audit(state: VMGraphState) -> dict[str, Any]:
        return await audit_results_node(state, audit_store=audit_store)

    async def _release(state: VMGraphState) -> dict[str, Any]:
        return await release_lock_node(state, locker=locker)

    builder.add_node("acquire_lock", _acquire)
    builder.add_node("discover", _discover)
    builder.add_node("drift_check", _drift_check)
    async def _plan_actions(state: VMGraphState) -> dict[str, Any]:
        return await plan_actions_node(
            state,
            llm_client=llm_client,
            ai_decision_store=ai_decision_store,
        )

    builder.add_node("plan_actions", _plan_actions)
    builder.add_node("dispatch_action", _dispatch)
    builder.add_node("check_more_actions", lambda state: {})
    builder.add_node("audit_results", _audit)
    builder.add_node("release_lock", _release)

    builder.set_entry_point("acquire_lock")

    builder.add_conditional_edges(
        "acquire_lock", route_after_lock, ["discover", "audit_results"],
    )
    # Build the ordered list of optional SRE snapshot nodes to insert between
    # discover and drift_check.  Each is only added when its store/settings
    # are provided.
    sre_snapshot_nodes: list[str] = []

    if disk_history_store is not None:
        async def _disk_snapshot(state: VMGraphState) -> dict[str, Any]:
            return await disk_snapshot_node(
                state, executor=executor,
                disk_history_store=disk_history_store,
                audit_store=audit_store,
                settings=sre_disk_settings,
            )

        builder.add_node("disk_snapshot", _disk_snapshot)
        sre_snapshot_nodes.append("disk_snapshot")

    if baseline_store is not None:
        async def _drift_baseline(state: VMGraphState) -> dict[str, Any]:
            return await drift_baseline_node(
                state, executor=executor,
                baseline_store=baseline_store,
                audit_store=audit_store,
                settings=sre_drift_settings,
            )

        builder.add_node("drift_baseline", _drift_baseline)
        sre_snapshot_nodes.append("drift_baseline")

    if sre_failed_logins_settings is not None:
        async def _failed_logins(state: VMGraphState) -> dict[str, Any]:
            return await failed_logins_node(
                state, executor=executor,
                audit_store=audit_store,
                settings=sre_failed_logins_settings,
            )

        builder.add_node("failed_logins", _failed_logins)
        sre_snapshot_nodes.append("failed_logins")

    if sre_snapshot_nodes:
        _first_sre = sre_snapshot_nodes[0]

        def _route_after_discover(state: VMGraphState, *, _first: str = _first_sre) -> str:
            return "audit_results" if state.get("error") else _first

        builder.add_conditional_edges(
            "discover", _route_after_discover, sre_snapshot_nodes + ["audit_results"],
        )
        for _prev, _nxt in zip(sre_snapshot_nodes, sre_snapshot_nodes[1:]):
            builder.add_edge(_prev, _nxt)
        builder.add_edge(sre_snapshot_nodes[-1], "drift_check")
    else:
        builder.add_conditional_edges(
            "discover", route_after_discover, ["drift_check", "audit_results"],
        )
    builder.add_conditional_edges(
        "drift_check",
        route_after_drift_check,
        ["plan_actions", "audit_results", "dispatch_action"],
    )
    builder.add_edge("plan_actions", "dispatch_action")
    builder.add_edge("dispatch_action", "check_more_actions")
    builder.add_conditional_edges(
        "check_more_actions", route_check_more, ["dispatch_action", "audit_results"],
    )
    builder.add_edge("audit_results", "release_lock")
    builder.add_edge("release_lock", END)

    return builder
