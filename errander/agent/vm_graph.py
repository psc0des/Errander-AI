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

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.agent.decisions import prioritize_actions
from errander.agent.subgraphs.backup_verify import (
    BackupVerifyGraphState,
    build_backup_verify_subgraph,
)
from errander.agent.subgraphs.disk_cleanup import (
    DiskCleanupGraphState,
    build_disk_cleanup_subgraph,
)
from errander.agent.subgraphs.docker_hygiene import (
    DockerHygieneGraphState,
    build_docker_hygiene_subgraph,
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
from errander.execution.privilege import (
    REQUIRED_BINARIES_BY_ACTION,
    parse_capability_check,
    sudo_capability_check,
)
from errander.models.actions import (
    Action,
    ActionStatus,
    ActionType,
    RiskTier,
)
from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import ACTION_DURATION, ACTIONS_TOTAL, VM_LOCK_HELD
from errander.safety.drift import compare_states, load_baseline, save_baseline
from errander.safety.validators import validate_action

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor
    from errander.execution.ssh import SSHConnectionManager
    from errander.integrations.slack import SlackClient
    from errander.safety.ai_audit import AIDecisionStore
    from errander.safety.audit import AuditStore
    from errander.safety.hygiene_approval import HygieneApprovalManager
    from errander.safety.locking import FileLocker

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

    # Docker command mode: "wrapper" | "direct_sudo" | "disabled" (default: "wrapper")
    docker_command_mode: str


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

    return {"locked": True, "lock_acquired_at": datetime.now(tz=UTC).isoformat()}


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
        disk_usage=dict(vm_info_dict.get("disk_usage") or {}),  # type: ignore[call-overload]
        docker_available=bool(vm_info_dict.get("docker_available", False)),
        pending_packages=int(str(vm_info_dict.get("pending_packages", 0))),
        uptime_seconds=float(str(vm_info_dict.get("uptime_seconds", 0.0))),
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
                timestamp=datetime.now(tz=UTC),
                metadata={"drifts": result.drifts},
            )
        )
        if state.get("drift_abort_on_detection", False):
            return {
                "drift_result": drift_dict,
                "error": f"Drift detected, aborting: {'; '.join(result.drifts)}",
            }

    return {"drift_result": drift_dict}


async def sudo_preflight_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    audit_store: AuditStore | None = None,
) -> dict[str, Any]:
    """Check sudo -n capability for binaries required by the planned actions.

    Skipped in dry-run mode. In live mode, runs sudo_capability_check() via SSH
    for the binaries needed by planned actions. Fails closed with an error that
    routes to audit_results if any required binary cannot be called with sudo -n.

    This catches misconfigured sudoers before the agent touches anything on the VM.
    """
    dry_run = state.get("dry_run", True)
    if dry_run:
        return {}

    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    planned = state.get("planned_actions", [])
    action_types = {a.get("action_type") for a in planned}
    batch_id = state.get("batch_id", "unknown")

    from errander.agent.subgraphs import BUILTIN_ACTIONS

    docker_mode = state.get("docker_command_mode", "wrapper")

    # Collect binaries required by the planned action types + OS family.
    required: list[str] = []
    for action_type in action_types:
        if action_type == "patching":
            key = "patching_dnf" if os_family == "rhel" else "patching_apt"
            required.extend(REQUIRED_BINARIES_BY_ACTION.get(key, []))
        elif action_type == "docker_prune":
            if docker_mode == "disabled":
                continue  # no preflight needed
            if docker_mode == "wrapper":
                manifest = BUILTIN_ACTIONS.get("docker_prune")
                wrappers = list(manifest.required_wrappers) if manifest else []
                required.extend(wrappers)
            else:
                required.extend(REQUIRED_BINARIES_BY_ACTION.get("docker_prune_direct", []))
        else:
            required.extend(REQUIRED_BINARIES_BY_ACTION.get(str(action_type), []))

    if not required:
        return {}

    # Emit a warning audit event when direct_sudo Docker mode is planned.
    if docker_mode == "direct_sudo" and any(a.get("action_type") == "docker_prune" for a in planned):
        logger.warning(
            "VM %s using direct_sudo Docker mode — not production hardened",
            vm_id,
        )
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.SUDO_PREFLIGHT_FAILED,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="docker_prune",
                detail="WARNING: direct_sudo Docker mode is not production hardened",
                metadata={"docker_command_mode": "direct_sudo"},
            ), dry_run=False)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_required = [b for b in required if not (b in seen or seen.add(b))]  # type: ignore[func-returns-value]

    target_hostname = state.get("hostname", "")
    target_username = state.get("ssh_user", "")
    target_key_path = state.get("ssh_key_path", "")

    result = await executor.execute(
        vm_id, target_hostname, target_username, target_key_path,
        command=sudo_capability_check(unique_required),
        dry_run=False,
    )

    if not result.success:
        detail = f"Sudo preflight SSH probe failed on {vm_id}: {result.stderr[:200]}"
        logger.error("SUDO PREFLIGHT FAILED (SSH error) on %s — blocking live execution", vm_id)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.SUDO_PREFLIGHT_FAILED,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="sudo_preflight",
                detail=detail,
            ), dry_run=False)
        return {"error": detail}

    _ok, failed = parse_capability_check(result.stdout)

    if failed:
        # Wrappers under /usr/local/sbin/ are enabled-action prerequisites;
        # their absence is a TARGET_PREFLIGHT_FAILED (VM-skip, batch continues).
        # Ordinary binary/sudoers failures are SUDO_PREFLIGHT_FAILED.
        wrapper_failures = [b for b in failed if b.startswith("/usr/local/sbin/")]
        binary_failures = [b for b in failed if not b.startswith("/usr/local/sbin/")]

        if wrapper_failures:
            detail = (
                f"Required wrapper(s) missing on {vm_id}: {wrapper_failures}. "
                "Install wrappers — see SETUP.md#optional-docker-cleanup."
            )
            logger.error("TARGET PREFLIGHT FAILED on %s: %s", vm_id, detail)
            if audit_store is not None:
                await audit_store.log_event(AuditEvent(
                    event_type=EventType.TARGET_PREFLIGHT_FAILED,
                    batch_id=batch_id,
                    vm_id=vm_id,
                    action_type="sudo_preflight",
                    detail=detail,
                    metadata={"missing_wrappers": wrapper_failures},
                ), dry_run=False)
            return {"error": detail}

        if binary_failures:
            detail = (
                f"sudo -n unavailable on {vm_id} for: {binary_failures}. "
                "Check sudoers — see SETUP.md Step 3."
            )
            logger.error("SUDO PREFLIGHT FAILED on %s: %s", vm_id, detail)
            if audit_store is not None:
                await audit_store.log_event(AuditEvent(
                    event_type=EventType.SUDO_PREFLIGHT_FAILED,
                    batch_id=batch_id,
                    vm_id=vm_id,
                    action_type="sudo_preflight",
                    detail=detail,
                    metadata={"failed_binaries": binary_failures},
                ), dry_run=False)
            return {"error": detail}

    logger.info("Sudo preflight passed on %s for %d binaries", vm_id, len(unique_required))
    return {}


def route_after_sudo_preflight(state: VMGraphState) -> str:
    """Route to dispatch if sudo preflight passed, else to audit_results."""
    return "audit_results" if state.get("error") else "dispatch_action"


async def dispatch_action_node(
    state: VMGraphState,
    *,
    executor: SandboxExecutor,
    disk_cleanup_compiled: Any = None,
    log_rotation_compiled: Any = None,
    docker_prune_compiled: Any = None,
    docker_hygiene_compiled: Any = None,
    patching_compiled: Any = None,
    backup_verify_compiled: Any = None,
    hygiene_manager: HygieneApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    web_base_url: str = "",
    approval_timeout_seconds: int = 1800,
    approval_poll_interval_seconds: int = 30,
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
    now = datetime.now(tz=UTC)

    # Pre-dispatch validation
    try:
        action_obj = Action(
            action_type=ActionType(str(action_type)),
            risk_tier=RiskTier(str(action.get("risk_tier", "medium"))),
            params=dict(action.get("params") or {}),  # type: ignore[call-overload]
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

    action_start = datetime.now(tz=UTC)

    if action_type == ActionType.DISK_CLEANUP.value:
        result_dict = await _run_disk_cleanup(state, disk_cleanup_compiled)
    elif action_type == ActionType.LOG_ROTATION.value:
        result_dict = await _run_log_rotation(state, log_rotation_compiled)
    elif action_type == ActionType.DOCKER_PRUNE.value:
        result_dict = await _run_docker_prune(state, docker_prune_compiled)
    elif action_type == ActionType.DOCKER_HYGIENE.value:
        result_dict = await _run_docker_hygiene(
            state, docker_hygiene_compiled,
            hygiene_manager=hygiene_manager,
            slack_client=slack_client,
            web_base_url=web_base_url,
            approval_timeout_seconds=approval_timeout_seconds,
            approval_poll_interval_seconds=approval_poll_interval_seconds,
        )
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
    duration = (datetime.now(tz=UTC) - action_start).total_seconds()
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
    now = datetime.now(tz=UTC)

    # Read approved action params from the plan (P1-1)
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    action_params: dict[str, object] = {}
    if index < len(planned):
        _raw = planned[index].get("params")
        action_params = dict(_raw) if isinstance(_raw, dict) else {}

    sub_state: DiskCleanupGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),
        "username": state.get("ssh_user", ""),
        "key_path": state.get("ssh_key_path", ""),
        **({  # type: ignore[typeddict-item]
            k: action_params[k]
            for k in ("whitelist_paths", "tmp_age_days", "journal_vacuum_days")
            if k in action_params
        }),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    error = final_state.get("error")

    detail_parts: list[str] = []
    if final_state.get("cleanup_output"):
        detail_parts.append(f"cleaned: {', '.join(final_state['cleanup_output'].keys())}")
    disk_before: dict[str, float] = final_state.get("disk_before") or {}
    disk_after: dict[str, float] = final_state.get("disk_after") or {}
    if disk_before and disk_after:
        for mount in ("/", "/var", "/data"):
            if mount in disk_before and mount in disk_after:
                detail_parts.append(
                    f"{mount}: {disk_before[mount]:.0f}% → {disk_after[mount]:.0f}%"
                )
                break
    elif disk_before and final_state.get("space_by_path"):
        reclaim = ", ".join(
            f"{k}: {v}" for k, v in final_state["space_by_path"].items() if v not in ("0", "unknown", "")
        )
        if reclaim:
            detail_parts.append(f"reclaimable: {reclaim}")

    return {
        "action_type": ActionType.DISK_CLEANUP.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": error,
    }


async def _run_log_rotation(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the log rotation sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=UTC)

    # Read approved action params from the plan (P1-1)
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    action_params: dict[str, object] = {}
    if index < len(planned):
        _raw = planned[index].get("params")
        action_params = dict(_raw) if isinstance(_raw, dict) else {}

    sub_state: LogRotationGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),
        "username": state.get("ssh_user", ""),
        "key_path": state.get("ssh_key_path", ""),
        **({  # type: ignore[typeddict-item]
            k: action_params[k]
            for k in ("log_paths", "size_threshold_mb", "compress")
            if k in action_params
        }),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("nothing_to_do"):
        detail_parts.append("nothing to do — no oversized log files")
    elif final_state.get("large_files"):
        detail_parts.append(f"found: {len(final_state['large_files'])} oversized file(s)")
    if final_state.get("rotation_output"):
        rot: dict[str, str] = final_state["rotation_output"]
        if "logrotate" in rot:
            count = len(final_state.get("large_files", []))
            detail_parts.append(f"rotated: {count} file(s) via logrotate")
        else:
            detail_parts.append(f"rotated: {len(rot)} file(s) manually")

    return {
        "action_type": ActionType.LOG_ROTATION.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _run_docker_prune(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the Docker prune sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=UTC)
    vm_info = state.get("vm_info", {})

    # Read approved action params from the plan (P1-1)
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    action_params: dict[str, object] = {}
    if index < len(planned):
        _raw = planned[index].get("params")
        action_params = dict(_raw) if isinstance(_raw, dict) else {}

    sub_state: DockerPruneGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "docker_available": bool(vm_info.get("docker_available", True)),
        "docker_prune_aggressive": bool(action_params.get("aggressive", False)),
        "docker_command_mode": str(state.get("docker_command_mode", "wrapper")),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-unknown-key]
        "username": state.get("ssh_user", ""),
        "key_path": state.get("ssh_key_path", ""),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


def _format_hygiene_result(
    final_state: dict[str, Any],
    vm_id: str,
    started_at: datetime,
) -> dict[str, object]:
    """Build the action result dict from a completed docker_hygiene sub-graph state."""
    status = final_state.get("status", ActionStatus.SKIPPED.value)
    detail_parts: list[str] = []
    assessment = final_state.get("assessment")
    if assessment is not None:
        n_findings = len(assessment.findings)
        n_cleanup = len(assessment.cleanup_candidates())
        n_investigate = len(assessment.investigate())
        detail_parts.append(
            f"assessed: {n_findings} findings "
            f"({n_cleanup} cleanup, {n_investigate} investigate)"
        )
    removal_results = final_state.get("removal_results") or ()
    if removal_results:
        n_removed = sum(1 for r in removal_results if r.status.value == "removed")
        n_drift = sum(1 for r in removal_results if r.status.value == "drift_skipped")
        n_failed = sum(1 for r in removal_results if r.status.value == "failed")
        detail_parts.append(
            f"removed {n_removed}, drift_skipped {n_drift}, failed {n_failed}"
        )
    return {
        "action_type": ActionType.DOCKER_HYGIENE.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts) if detail_parts else "no findings",
        "error": final_state.get("error"),
        "removal_results": removal_results,
    }


async def _run_docker_hygiene(
    state: VMGraphState,
    compiled: Any,
    *,
    hygiene_manager: HygieneApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    web_base_url: str = "",
    approval_timeout_seconds: int = 1800,
    approval_poll_interval_seconds: int = 30,
) -> dict[str, object]:
    """Run the docker_hygiene sub-graph with full assess → approve → execute flow.

    Fast path: if ``approval`` is already present in the action params (test
    injection or replay), the sub-graph is invoked directly with it.

    Live path: assess-only first, then post a Slack message + register with the
    HygieneApprovalManager, background-poll for thread replies while waiting
    for a decision, and re-invoke with the resolved approval.

    Per-object audit:
    - One audit event per DockerHygieneRemovalResult is written here, in
      addition to the aggregate action result dict returned to the parent
      graph. The action-level audit is unchanged; the per-object events are
      new and live alongside it (Exact-Object Approval invariant).
    """
    vm_id = state["vm_id"]
    now = datetime.now(tz=UTC)
    vm_info = state.get("vm_info", {})
    batch_id = state.get("batch_id", "unknown")

    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    action_params: dict[str, object] = {}
    if index < len(planned):
        _raw = planned[index].get("params")
        action_params = dict(_raw) if isinstance(_raw, dict) else {}

    sub_state: DockerHygieneGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "docker_available": bool(vm_info.get("docker_available", True)),
        "docker_command_mode": str(state.get("docker_command_mode", "wrapper")),
    }
    sub_state_with_conn: dict[str, Any] = dict(sub_state)
    sub_state_with_conn["hostname"] = state.get("hostname", "")
    sub_state_with_conn["username"] = state.get("ssh_user", "")
    sub_state_with_conn["key_path"] = state.get("ssh_key_path", "")

    # Fast path: approval pre-injected (test / replay) — skip approval gate.
    pre_approval = action_params.get("approval")
    if pre_approval is not None:
        sub_state_with_conn["approval"] = pre_approval
        try:
            final_state: dict[str, Any] = await compiled.ainvoke(sub_state_with_conn)
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.error("Sub-graph docker_hygiene failed for %s: %s", vm_id, exc)
            return {
                "action_type": ActionType.DOCKER_HYGIENE.value,
                "status": ActionStatus.FAILED.value,
                "vm_id": vm_id,
                "started_at": now.isoformat(),
                "completed_at": datetime.now(tz=UTC).isoformat(),
                "detail": "sub-graph raised exception",
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error in docker_hygiene for %s", vm_id)
            return {
                "action_type": ActionType.DOCKER_HYGIENE.value,
                "status": ActionStatus.FAILED.value,
                "vm_id": vm_id,
                "started_at": now.isoformat(),
                "completed_at": datetime.now(tz=UTC).isoformat(),
                "detail": "sub-graph raised exception",
                "error": str(exc),
            }
        return _format_hygiene_result(final_state, vm_id, now)

    # Live path — Phase 1: assess-only (no approval in state).
    try:
        assess_state: dict[str, Any] = await compiled.ainvoke(sub_state_with_conn)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph docker_hygiene assess failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception during assess",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in docker_hygiene assess for %s", vm_id)
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception during assess",
            "error": str(exc),
        }

    assessment = assess_state.get("assessment")

    # No assessment (docker unavailable or failed): return assess result as-is.
    if assessment is None:
        return _format_hygiene_result(assess_state, vm_id, now)

    # Nothing actionable: return assess result (no approval needed).
    if not assessment.cleanup_candidates():
        return _format_hygiene_result(assess_state, vm_id, now)

    # Dry-run: report findings without requesting approval.
    dry_run = state.get("dry_run", True)
    if dry_run:
        return _format_hygiene_result(assess_state, vm_id, now)

    # No approval manager available: cannot obtain approval — skip execution.
    if hygiene_manager is None:
        logger.warning(
            "docker_hygiene: no approval manager available for %s — skipping execution", vm_id
        )
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "no approval manager; skipping execution",
            "error": None,
            "removal_results": (),
        }

    # Phase 2: post Slack approval message + register with manager.
    from errander.safety.hygiene_approval import (
        format_hygiene_approval_message,
        poll_hygiene_replies_once,
    )

    # Build signed web-approval URL if a base URL is configured.
    web_approval_url: str | None = None
    if web_base_url:
        try:
            from errander.integrations.signed_url import make_signed_token
            token = make_signed_token(
                {"batch_id": batch_id, "vm_id": vm_id},
                ttl_seconds=approval_timeout_seconds,
            )
            web_approval_url = f"{web_base_url.rstrip('/')}/ui/docker-hygiene/approve?token={token}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not build signed URL for %s: %s", vm_id, exc)

    msg_body = format_hygiene_approval_message(
        assessment,
        web_approval_url=web_approval_url,
        batch_id=batch_id,
    )
    slack_ts: str | None = None
    if slack_client is not None:
        try:
            slack_ts = await slack_client.post_message(msg_body)
            logger.info("docker_hygiene Slack approval message posted ts=%s for %s", slack_ts, vm_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to post hygiene Slack message for %s: %s", vm_id, exc)

    pending = hygiene_manager.register(batch_id, vm_id, assessment, slack_message_ts=slack_ts)

    # Background Slack reply poller (runs until decision or cancellation).
    async def _poll_loop() -> None:
        while not pending.is_decided():
            await asyncio.sleep(approval_poll_interval_seconds)
            try:
                await poll_hygiene_replies_once(slack_client, pending, hygiene_manager)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Hygiene Slack poll error for %s: %s", vm_id, exc)

    poll_task: asyncio.Task[None] | None = None
    if slack_ts is not None and slack_client is not None:
        poll_task = asyncio.create_task(_poll_loop())

    # Phase 3: wait for operator decision.
    try:
        decision = await hygiene_manager.wait_for_decision(
            batch_id, vm_id, timeout_seconds=approval_timeout_seconds
        )
    finally:
        if poll_task is not None:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poll_task

    if decision is None:
        logger.warning(
            "docker_hygiene approval timed out for %s after %ds", vm_id, approval_timeout_seconds
        )
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "approval timeout",
            "error": None,
            "removal_results": (),
        }

    if not decision.approved_findings:
        logger.info("docker_hygiene rejected by operator for %s", vm_id)
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "rejected by operator",
            "error": None,
            "removal_results": (),
        }

    # Phase 4: re-invoke sub-graph with approval to execute removals.
    sub_state_exec: dict[str, Any] = dict(sub_state_with_conn)
    sub_state_exec["approval"] = decision

    try:
        exec_state: dict[str, Any] = await compiled.ainvoke(sub_state_exec)
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.error("Sub-graph docker_hygiene execute failed for %s: %s", vm_id, exc)
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception during execute",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in docker_hygiene execute for %s", vm_id)
        return {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "status": ActionStatus.FAILED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception during execute",
            "error": str(exc),
        }

    return _format_hygiene_result(exec_state, vm_id, now)


async def _run_patching(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the patching sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=UTC)

    # Extract approved packages from the current action's enriched preview so
    # execute_node can install pinned versions instead of a broad upgrade.
    index = state.get("current_action_index", 0)
    planned = state.get("planned_actions", [])
    approved_packages: list[dict[str, str]] = []
    if index < len(planned):
        action = planned[index]
        if isinstance(action, dict):
            preview = action.get("preview") or {}
            if isinstance(preview, dict):
                raw_pkgs = preview.get("packages")
                if isinstance(raw_pkgs, list):
                    approved_packages = [
                        p for p in raw_pkgs
                        if isinstance(p, dict) and p.get("name")
                    ]

    sub_state: PatchingGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-unknown-key]
        "username": state.get("ssh_user", ""),
        "key_path": state.get("ssh_key_path", ""),
        "batch_id": state.get("batch_id", ""),
        "critical_services": list(state.get("critical_services") or []),
        "approved_packages": approved_packages,
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "detail": "sub-graph raised exception",
            "error": str(exc),
        }

    status = final_state.get("status", ActionStatus.FAILED.value)
    detail_parts: list[str] = []
    if final_state.get("nothing_to_do"):
        detail_parts.append("nothing to do — already up-to-date")
    elif final_state.get("changed_packages") is not None:
        n = len(final_state["changed_packages"])
        detail_parts.append(f"installed: {n} package(s)" if n else "no package versions changed")
    elif final_state.get("pending_updates"):
        detail_parts.append(f"queued: {len(final_state['pending_updates'])} package(s)")

    return {
        "action_type": ActionType.PATCHING.value,
        "status": status,
        "vm_id": vm_id,
        "started_at": now.isoformat(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _run_backup_verify(
    state: VMGraphState,
    compiled: Any,
) -> dict[str, object]:
    """Run the backup verify sub-graph and return a serialised result dict."""
    vm_id = state["vm_id"]
    now = datetime.now(tz=UTC)

    # Get backup paths from action params if available
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)
    backup_paths: list[str] = []
    if index < len(planned):
        params_raw = planned[index].get("params")
        params: dict[str, object] = params_raw if isinstance(params_raw, dict) else {}
        bp_raw = params.get("backup_paths")
        backup_paths = [str(p) for p in bp_raw] if isinstance(bp_raw, list) else []

    sub_state: BackupVerifyGraphState = {
        "vm_id": vm_id,
        "os_family": state.get("os_family", "ubuntu"),
        "dry_run": state.get("dry_run", True),
        "backup_paths": backup_paths,
        "hostname": state.get("hostname", ""),  # type: ignore[typeddict-unknown-key]
        "username": state.get("ssh_user", ""),
        "key_path": state.get("ssh_key_path", ""),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
            "completed_at": datetime.now(tz=UTC).isoformat(),
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
        "completed_at": datetime.now(tz=UTC).isoformat(),
        "detail": "; ".join(detail_parts),
        "error": final_state.get("error"),
    }


async def _write_docker_hygiene_per_object_audit(
    audit_store: AuditStore,
    batch_id: str,
    vm_id: str,
    result_dict: dict[str, Any],
) -> None:
    """Write one audit event per docker_hygiene removal result.

    Per Exact-Object Approval invariant: each removed/drifted/failed object
    gets its own audit row, not just a batch-level summary.
    """
    removal_results = result_dict.get("removal_results") or ()
    for rr in removal_results:
        status = rr.status.value
        if status == "removed":
            event_type = EventType.DOCKER_HYGIENE_OBJECT_REMOVED
        elif status == "drift_skipped":
            event_type = EventType.DOCKER_HYGIENE_OBJECT_DRIFT_SKIPPED
        elif status == "failed":
            event_type = EventType.DOCKER_HYGIENE_OBJECT_REMOVE_FAILED
        else:
            # skipped_not_found — log as drift_skipped (object already gone)
            event_type = EventType.DOCKER_HYGIENE_OBJECT_DRIFT_SKIPPED

        finding = rr.finding
        detail = (
            f"{finding.resource_class.value} {finding.identity} → {status}"
        )
        metadata: dict[str, Any] = {
            "resource_class": finding.resource_class.value,
            "object_id": finding.object_id,
            "object_name": finding.name,
            "classification_at_approval": finding.classification.value,
            "removal_status": status,
            "drift_reason": rr.drift_reason,
            "error": rr.error,
            "size_bytes": finding.size_bytes,
        }
        await audit_store.log_event(
            AuditEvent(
                event_type=event_type,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type=ActionType.DOCKER_HYGIENE.value,
                detail=detail,
                timestamp=datetime.now(tz=UTC),
                metadata=metadata,
            )
        )


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
                timestamp=datetime.now(tz=UTC),
                metadata={
                    "status": str(r.get("status", "")),
                    "error": r.get("error"),
                },
            )
        )

        # docker_hygiene per-object audit (Exact-Object Approval invariant —
        # one audit row per removed/drifted/failed object, not per batch).
        if r.get("action_type") == ActionType.DOCKER_HYGIENE.value:
            await _write_docker_hygiene_per_object_audit(
                audit_store, batch_id, vm_id, r,
            )

    if error:
        await audit_store.log_event(
            AuditEvent(
                event_type=EventType.ACTION_FAILED,
                batch_id=batch_id,
                vm_id=vm_id,
                detail=error,
                timestamp=datetime.now(tz=UTC),
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
                acquired_at = acquired_at.replace(tzinfo=UTC)
            held_seconds = (datetime.now(tz=UTC) - acquired_at).total_seconds()
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
                f"over {settings.window_days}d"
            ),
            metadata={
                "mountpoint": alert.mountpoint,
                "used_pct_start": alert.used_pct_start,
                "used_pct_end": alert.used_pct_end,
                "delta_pct": alert.delta_pct,
                "window_days": settings.window_days,
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
            if dry_run:
                # Dry-run must not mutate the operational baseline.
                continue
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


async def post_cleanup_disk_gate_node(
    state: VMGraphState,
    *,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore | None = None,
) -> dict[str, Any]:
    """Re-check / disk usage after disk_cleanup or log_rotation, before patching.

    Only fires when:
      - last completed action was disk_cleanup or log_rotation
      - next planned action is patching

    ≥95%: inject a skipped result for patching, advance index.
    90–94%: warn only, allow patching to proceed.
    SSH failure: pass silently (best-effort).
    """
    results = list(state.get("results", []))
    planned = state.get("planned_actions", [])
    index = state.get("current_action_index", 0)

    if not results:
        return {}
    last_action_type = str(results[-1].get("action_type", ""))
    if last_action_type not in ("disk_cleanup", "log_rotation"):
        return {}
    if index >= len(planned):
        return {}
    next_action_type = str(planned[index].get("action_type", ""))
    if next_action_type != "patching":
        return {}

    vm_id = state["vm_id"]
    hostname = str(state.get("hostname", ""))
    ssh_user = str(state.get("ssh_user", "errander-ai"))
    ssh_key_path = str(state.get("ssh_key_path", ""))
    batch_id = str(state.get("batch_id", ""))

    disk_pct: int | None = None
    try:
        ssh_result = await ssh_manager.execute(
            vm_id, hostname, ssh_user, ssh_key_path,
            "df -BM / 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%M'",
        )
        if ssh_result.success and ssh_result.stdout.strip():
            disk_pct = int(ssh_result.stdout.strip())
    except Exception as exc:
        logger.warning("post_cleanup_disk_gate: SSH failed for %s: %s", vm_id, exc)
        return {}

    if disk_pct is None:
        return {}

    if disk_pct >= 95:
        detail = (
            f"post_cleanup_disk_gate: / still at {disk_pct}% after "
            f"{last_action_type} — patching skipped"
        )
        logger.warning("%s: %s", vm_id, detail)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.DISK_GATE_BLOCKED,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="patching",
                detail=detail,
                metadata={"disk_pct": disk_pct, "gate": "post_cleanup_disk_gate"},
            ), dry_run=False)
        now = datetime.now(tz=UTC)
        results.append({
            "action_type": "patching",
            "status": ActionStatus.SKIPPED.value,
            "vm_id": vm_id,
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "detail": detail,
        })
        return {"results": results, "current_action_index": index + 1}

    if disk_pct >= 90:
        logger.warning(
            "post_cleanup_disk_gate: %s disk at %d%% after %s — proceeding with caution",
            vm_id, disk_pct, last_action_type,
        )

    return {}


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
    hygiene_manager: HygieneApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    web_base_url: str = "",
    approval_timeout_seconds: int = 1800,
    approval_poll_interval_seconds: int = 30,
) -> StateGraph[VMGraphState]:
    """Construct the per-VM maintenance graph.

    Args:
        executor: SandboxExecutor for SSH command execution.
        locker: FileLocker for VM-level locking.
        audit_store: AuditStore for audit trail.
        ssh_manager: SSHConnectionManager for OS detection.

    Returns:
        StateGraph for per-VM maintenance (call .compile() to use).
    """
    builder: StateGraph[VMGraphState] = StateGraph(VMGraphState)

    # Compile sub-graphs once — reused across all dispatches
    disk_cleanup_compiled = build_disk_cleanup_subgraph(executor).compile()
    log_rotation_compiled = build_log_rotation_subgraph(executor).compile()
    docker_prune_compiled = build_docker_prune_subgraph(executor).compile()
    docker_hygiene_compiled = build_docker_hygiene_subgraph(executor).compile()
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
            docker_hygiene_compiled=docker_hygiene_compiled,
            patching_compiled=patching_compiled,
            backup_verify_compiled=backup_verify_compiled,
            hygiene_manager=hygiene_manager,
            slack_client=slack_client,
            web_base_url=web_base_url,
            approval_timeout_seconds=approval_timeout_seconds,
            approval_poll_interval_seconds=approval_poll_interval_seconds,
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

    async def _sudo_preflight(state: VMGraphState) -> dict[str, Any]:
        return await sudo_preflight_node(state, executor=executor, audit_store=audit_store)

    async def _post_cleanup_gate(state: VMGraphState) -> dict[str, Any]:
        return await post_cleanup_disk_gate_node(
            state, ssh_manager=ssh_manager, audit_store=audit_store
        )

    builder.add_node("plan_actions", _plan_actions)
    builder.add_node("sudo_preflight", _sudo_preflight)
    builder.add_node("dispatch_action", _dispatch)
    builder.add_node("post_cleanup_disk_gate", _post_cleanup_gate)
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
        for _prev, _nxt in zip(sre_snapshot_nodes, sre_snapshot_nodes[1:], strict=False):
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
    builder.add_edge("plan_actions", "sudo_preflight")
    builder.add_conditional_edges(
        "sudo_preflight", route_after_sudo_preflight, ["dispatch_action", "audit_results"],
    )
    builder.add_edge("dispatch_action", "post_cleanup_disk_gate")
    builder.add_edge("post_cleanup_disk_gate", "check_more_actions")
    builder.add_conditional_edges(
        "check_more_actions", route_check_more, ["dispatch_action", "audit_results"],
    )
    builder.add_edge("audit_results", "release_lock")
    builder.add_edge("release_lock", END)

    return builder
