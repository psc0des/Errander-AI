"""Batch orchestrator graph — fan-out across a fleet of VMs.

Level 1 of the Option C architecture. This graph:
1. Initialises the batch (generates batch_id)
2. Checks maintenance window — blocks if outside window unless force=True
3. Validates targets via SSH + OS check, partitions into healthy/failed
4. Fans out to per-VM graphs via LangGraph Send()
5. Collects aggregated results
6. Generates a report

Graph: init_batch → validate_window → validate_targets → fan_out
       → collect_results → generate_report → END

Dependencies (injected at build time):
- SandboxExecutor: passed through to per-VM graphs
- FileLocker: passed through to per-VM graphs
- AuditStore: audit trail + passed to per-VM graphs
- SSHConnectionManager: SSH connections
- MaintenanceWindow: optional window config (None = no window check)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from errander.agent.decisions import prioritize_actions
from errander.agent.vm_graph import VMGraphState, build_vm_graph
from errander.execution.os_detection import detect_os
from errander.models.actions import ACTION_RISK_TIERS, ActionStatus, ActionType, RiskTier
from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import BATCH_DURATION
from errander.safety.approval import ApprovalManager, await_dual_approval
from errander.scheduling.windows import (
    MaintenanceWindow,
    check_window_from_config,
    next_window_open,
)

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor
    from errander.execution.ssh import SSHConnectionManager
    from errander.integrations.slack import SlackClient
    from errander.safety.audit import AuditStore
    from errander.safety.deferred import DeferredExecutionStore
    from errander.safety.locking import FileLocker

logger = logging.getLogger(__name__)

# Nodes that are safe to resume after an agent crash (Project A, A5).
# These nodes are either idempotent or start a new side-effect boundary.
# Any other node in a RUNNING checkpoint → NEEDS_OPERATOR_REVIEW.
SAFE_RESUME_NODES: frozenset[str] = frozenset({
    "approval_gate",
    "dispatch_current_wave",
    "check_wave_health",
    "generate_plan_artifact",
    "validate_window",
    "validate_targets",
    "generate_report",
})


# --- Result accumulator reducers ---

def _merge_vm_results(
    existing: list[dict[str, object]],
    incoming: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append-only reducer for aggregating per-VM execution results."""
    return [*existing, *incoming]


def _merge_vm_plans(
    existing: list[dict[str, object]],
    incoming: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append-only reducer for aggregating per-VM planning results."""
    return [*existing, *incoming]


def _merge_sre_list(
    existing: list[dict[str, object]],
    incoming: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append-only reducer for SRE signal lists (disk growth, drift, failed logins)."""
    return [*existing, *incoming]


def _effective_vm_plans(state: BatchGraphState) -> list[dict[str, object]]:
    """Return enriched_vm_plans if set (post-enrich), otherwise raw vm_plans.

    enrich_plan_node writes to enriched_vm_plans (not vm_plans) to avoid
    double-appending via the append-only reducer on vm_plans.
    """
    enriched = state.get("enriched_vm_plans")  # type: ignore[attr-defined]
    return list(enriched) if enriched else list(state.get("vm_plans", []))  # type: ignore[attr-defined]


# --- State ---

class BatchGraphState(TypedDict, total=False):
    """State for the batch orchestrator graph."""

    batch_id: str
    batch_started_at: str
    dry_run: bool
    force: bool
    force_reason: str

    # Loaded targets (serialised VMTarget fields)
    targets: list[dict[str, object]]

    # Partitioned after validate_targets
    healthy_targets: list[dict[str, object]]
    failed_targets: list[dict[str, object]]

    # Per-VM plans from the planning phase (append-only via reducer)
    vm_plans: Annotated[list[dict[str, object]], _merge_vm_plans]

    # Enriched VM plans after enrich_plan_node runs (replaces vm_plans for all post-enrich
    # consumers so the append-only reducer on vm_plans doesn't double the entries)
    enriched_vm_plans: list[dict[str, object]]

    # Plan artifact (set by generate_plan_artifact_node)
    plan_id: str
    plan_hash: str

    # Aggregated results from all VM graphs (append-only via reducer)
    vm_results: Annotated[list[dict[str, object]], _merge_vm_results]

    # SRE signals aggregated from all VM graphs (append-only via reducer)
    sre_disk_growth: Annotated[list[dict[str, object]], _merge_sre_list]
    sre_drift_changes: Annotated[list[dict[str, object]], _merge_sre_list]
    sre_failed_logins: Annotated[list[dict[str, object]], _merge_sre_list]

    # Final report
    report: str
    error: str | None

    # Approval (set by approval_gate_node, gates wave dispatch)
    approved: bool | None

    # Approval policy from environment config (relaxed / moderate / strict)
    env_policy: str

    # Set True by deferred executor so the approval message and audit trail
    # make clear this is a re-approval at window time, not the original approval.
    is_deferred_reapproval: bool

    # Rolling update state
    rolling_update_percentage: int
    wave_failure_threshold: float
    health_check_command: str
    current_wave: int
    total_waves: int
    waves: list[list[dict[str, object]]]
    wave_aborted: bool

    # Canary state
    canary_enabled: bool
    canary_health_check_command: str
    canary_passed: bool | None

    # Drift detection passthrough (for VMGraphState injection)
    drift_detection_enabled: bool
    drift_abort_on_detection: bool

    # Deferred execution
    env_name: str
    deferred: bool

    # P0-2: preloaded artifact for exact deferred replay
    preloaded_plan_json: str | None
    preloaded_plan_hash: str | None
    preloaded_plan_id: str | None
    preloaded_approved_at: str | None   # ISO timestamp when operator approved; used for age check
    is_deferred_replay: bool

    # Per-decision AI audit DB path (finding #3.4) — serializable for Send()
    ai_db_path: str

    # Docker command mode: "wrapper" | "direct_sudo" | "disabled" (default: "wrapper")
    docker_command_mode: str

    # Action names that are enabled in this environment's inventory.
    # Built from env_schema.actions at batch init time and used by plan_vm_node
    # to restrict prioritize_actions() to only actions the operator has opted in to.
    enabled_actions: list[str]


# --- Node functions ---

async def init_batch_node(
    state: BatchGraphState,
    *,
    settings: Any = None,
    batch_store: Any = None,
) -> dict[str, Any]:
    """Generate a unique batch ID and inject rolling/canary/drift settings."""
    from errander.config.settings import Settings as _Settings
    _s: _Settings = settings if settings is not None else _Settings()

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    dry_run: bool = bool(state.get("dry_run", True))
    logger.info("Starting batch %s (dry_run=%s)", batch_id, dry_run)

    if batch_store is not None:
        await batch_store.insert(
            batch_id,
            env_name=str(state.get("env_name", "")),
            dry_run=dry_run,
            vm_count=len(list(state.get("targets") or [])),
        )

    return {
        "batch_id": batch_id,
        "batch_started_at": datetime.now(tz=UTC).isoformat(),
        # Rolling updates
        "rolling_update_percentage": _s.rolling_update_percentage,
        "wave_failure_threshold": _s.wave_failure_threshold,
        "health_check_command": _s.health_check_command,
        # Canary
        "canary_enabled": _s.canary_enabled,
        "canary_health_check_command": _s.canary_health_check_command,
        # Drift
        "drift_detection_enabled": _s.drift_detection_enabled,
        "drift_abort_on_detection": _s.drift_abort_on_detection,
    }


async def validate_window_node(
    state: BatchGraphState,
    *,
    window: MaintenanceWindow | None = None,
) -> dict[str, Any]:
    """Check that current time is within the maintenance window.

    If no window is configured, the check passes unconditionally.
    If force=True, the window is bypassed with a warning logged.
    Otherwise, if the current time is outside the window, sets error
    and the graph short-circuits to generate_report.

    Args:
        state: Current batch graph state.
        window: Optional configured maintenance window. None = no restriction.
    """
    force = state.get("force", False)

    if force:
        reason = state.get("force_reason", "(no reason given)")
        logger.warning("Maintenance window bypassed (force=True): %s", reason)
        return {}

    if window is None:
        logger.debug("No maintenance window configured — proceeding")
        return {}

    now = datetime.now(tz=UTC)
    if check_window_from_config(now, window):
        logger.info("Maintenance window check passed")
        return {}

    msg = (
        f"Outside maintenance window "
        f"(days={window.days}, hours={window.start_hour:02d}:00-{window.end_hour:02d}:00 "
        f"{window.timezone}). Use force=True to override."
    )
    logger.warning("Batch blocked: %s", msg)
    return {"error": msg}


async def validate_targets_node(
    state: BatchGraphState,
    *,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore,
) -> dict[str, Any]:
    """SSH-verify each target: detect OS and verify it matches inventory (finding #8).

    Replaces the trivial 'echo ok' connectivity check with a real OS detection:
    reads /etc/os-release, parses the OS family, and compares against the
    declared os_family in inventory. Mismatches go to failed_targets with
    reason OS_MISMATCH.

    A successfully validated target has its detected os_family stored in the
    target dict, so downstream nodes don't need to re-detect.
    """
    from errander.execution.os_detection import parse_os_release, verify_os_match
    from errander.models.vm import OSFamily

    batch_id = state.get("batch_id", "unknown")
    targets = state.get("targets", [])
    healthy: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    for t in targets:
        vm_id = str(t["vm_id"])
        hostname = str(t["hostname"])
        ssh_user = str(t["ssh_user"])
        key_path = str(t["ssh_key_path"])
        declared_os = str(t.get("os_family", "ubuntu"))

        try:
            result = await ssh_manager.execute(
                vm_id, hostname, ssh_user, key_path, "cat /etc/os-release",
            )
            if not result.success:
                logger.warning("Target %s: /etc/os-release unreadable", vm_id)
                failed.append(t)
                continue

            detected_family, detected_ver = parse_os_release(result.stdout)

            # Verify declared matches detected
            try:
                declared_family = OSFamily(declared_os)
            except ValueError:
                declared_family = OSFamily.UBUNTU  # fallback for unknown declared

            if not verify_os_match(declared_family, detected_family):
                msg = (
                    f"OS mismatch: inventory declares '{declared_os}' "
                    f"but detected '{detected_family.value}' ({detected_ver})"
                )
                logger.warning("Target %s OS_MISMATCH: %s", vm_id, msg)
                await audit_store.log_event(
                    AuditEvent(
                        event_type=EventType.OS_MISMATCH,
                        batch_id=batch_id,
                        vm_id=vm_id,
                        detail=msg,
                        timestamp=datetime.now(tz=UTC),
                        metadata={
                            "declared": declared_os,
                            "detected": detected_family.value,
                            "detected_version": detected_ver,
                        },
                    )
                )
                failed.append(t)
                continue

            # Store detected OS in the target dict for downstream nodes
            validated = dict(t)
            validated["os_family"] = detected_family.value
            validated["os_version"] = detected_ver

            # Sudo/wrapper readiness check — fail early rather than mid-batch
            try:
                from errander.execution.target_validation import check_target
                _enabled: list[str] | None = state.get("enabled_actions")
                readiness = await check_target(
                    vm_id=vm_id,
                    hostname=hostname,
                    username=ssh_user,
                    key_path=key_path,
                    os_family=detected_family.value,
                    docker_command_mode=str(state.get("docker_command_mode", "wrapper")),
                    ssh_manager=ssh_manager,
                    enabled_actions=_enabled,
                )
                if readiness.verdict == "blocked":
                    logger.warning(
                        "Target %s readiness BLOCKED: %s — removing from batch",
                        vm_id, "; ".join(readiness.issues),
                    )
                    await audit_store.log_event(
                        AuditEvent(
                            event_type=EventType.TARGET_READINESS_BLOCKED,
                            batch_id=batch_id,
                            vm_id=vm_id,
                            detail=f"Target readiness blocked: {'; '.join(readiness.issues)}",
                            timestamp=datetime.now(tz=UTC),
                        )
                    )
                    failed.append({**validated, "error": f"readiness blocked: {'; '.join(readiness.issues)}"})
                    continue
                if readiness.verdict == "warnings":
                    logger.warning(
                        "Target %s readiness WARNINGS: %s — proceeding with caution",
                        vm_id, "; ".join(readiness.issues),
                    )
                    validated["readiness_warnings"] = readiness.issues
            except Exception as exc:
                logger.debug("Readiness check failed for %s: %s — proceeding", vm_id, exc)

            healthy.append(validated)
            logger.info(
                "Target %s validated OK (OS: %s %s)",
                vm_id, detected_family.value, detected_ver,
            )

        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.error("Target %s unreachable: %s", vm_id, exc)
            failed.append(t)
            await audit_store.log_event(
                AuditEvent(
                    event_type=EventType.ACTION_FAILED,
                    batch_id=batch_id,
                    vm_id=vm_id,
                    detail=f"Target validation failed: {exc}",
                    timestamp=datetime.now(tz=UTC),
                )
            )
        except ValueError as exc:
            # Unsupported OS detected
            logger.warning("Target %s unsupported OS: %s", vm_id, exc)
            failed.append(t)
            await audit_store.log_event(
                AuditEvent(
                    event_type=EventType.ACTION_FAILED,
                    batch_id=batch_id,
                    vm_id=vm_id,
                    detail=f"Unsupported OS: {exc}",
                    timestamp=datetime.now(tz=UTC),
                )
            )

    return {
        "healthy_targets": healthy,
        "failed_targets": failed,
    }


async def check_fleet_health_node(
    state: BatchGraphState,
    *,
    audit_store: AuditStore,
    fleet_failure_threshold: float = 0.5,
) -> dict[str, Any]:
    """Abort pre-flight if too many targets failed validation (finding #7).

    Compares failed/total against fleet_failure_threshold. If exceeded,
    sets error and emits a FLEET_ABORT audit event. No actions run.

    Args:
        state: Current batch state (healthy_targets, failed_targets set by validate_targets).
        audit_store: For the FLEET_ABORT audit event.
        fleet_failure_threshold: Fraction of total targets that may fail before abort.
    """
    healthy = state.get("healthy_targets", [])
    failed = state.get("failed_targets", [])
    total = len(healthy) + len(failed)
    batch_id = state.get("batch_id", "unknown")

    if total == 0:
        return {"error": "No targets in batch"}

    failure_rate = len(failed) / total
    if failure_rate > fleet_failure_threshold:
        msg = (
            f"Fleet pre-flight abort: {len(failed)}/{total} targets failed validation "
            f"({failure_rate:.0%} > threshold {fleet_failure_threshold:.0%})"
        )
        logger.error("FLEET_ABORT batch %s: %s", batch_id, msg)
        await audit_store.log_event(
            AuditEvent(
                event_type=EventType.FLEET_ABORT,
                batch_id=batch_id,
                detail=msg,
                timestamp=datetime.now(tz=UTC),
                metadata={
                    "healthy": len(healthy),
                    "failed": len(failed),
                    "total": total,
                    "failure_rate": failure_rate,
                    "threshold": fleet_failure_threshold,
                },
            )
        )
        return {"error": msg}

    if failed:
        logger.warning(
            "Batch %s: %d/%d targets failed validation (below abort threshold %.0f%%), continuing",
            batch_id, len(failed), total, fleet_failure_threshold * 100,
        )
    return {}


# --- Wave helpers ---

def _partition_into_waves(
    targets: list[dict[str, object]],
    percentage: int,
) -> list[list[dict[str, object]]]:
    """Split targets into waves based on percentage.

    100% returns a single wave (all targets). 25% with 8 targets
    returns 4 waves of 2. Always at least 1 per wave.
    """
    if percentage >= 100 or len(targets) == 0:
        return [targets] if targets else []

    wave_size = max(1, math.ceil(len(targets) * percentage / 100))
    return [targets[i:i + wave_size] for i in range(0, len(targets), wave_size)]


async def prepare_waves_node(state: BatchGraphState) -> dict[str, Any]:
    """Partition healthy targets into waves."""
    healthy = state.get("healthy_targets", [])
    percentage = state.get("rolling_update_percentage", 100)
    canary_enabled = state.get("canary_enabled", False)

    waves = _partition_into_waves(healthy, percentage)

    # Canary: force wave 0 = exactly 1 VM
    if canary_enabled and len(healthy) > 1:
        canary_target = healthy[0]
        remaining = healthy[1:]
        remaining_waves = _partition_into_waves(remaining, percentage)
        waves = [[canary_target]] + remaining_waves

    logger.info(
        "Batch %s: %d targets -> %d wave(s)%s",
        state.get("batch_id", "unknown"), len(healthy), len(waves),
        " (canary enabled)" if canary_enabled else "",
    )

    return {
        "waves": waves,
        "current_wave": 0,
        "total_waves": len(waves),
        "wave_aborted": False,
        "canary_passed": None,
    }


async def check_wave_health_node(
    state: BatchGraphState,
    *,
    ssh_manager: SSHConnectionManager,
    health_check_command: str = "echo ok",
) -> dict[str, Any]:
    """Check health of VMs in the just-completed wave.

    Runs health_check_command on each VM. If failure rate exceeds
    wave_failure_threshold, sets wave_aborted=True.
    Also advances current_wave counter.
    """
    from errander.observability.metrics import WAVE_HEALTH_CHECKS

    current_wave = state.get("current_wave", 0)
    waves = state.get("waves", [])
    threshold = state.get("wave_failure_threshold", 0.5)
    canary_enabled = state.get("canary_enabled", False)
    is_canary_wave = canary_enabled and current_wave == 0

    if current_wave >= len(waves):
        return {"wave_aborted": False}

    wave_targets = waves[current_wave]

    # Use stricter canary health check command for wave 0
    cmd = health_check_command
    if is_canary_wave:
        cmd = state.get("canary_health_check_command", health_check_command)

    # Run health check on each VM in the wave
    health_failures = 0
    for t in wave_targets:
        try:
            result = await ssh_manager.execute(
                str(t["vm_id"]),
                str(t["hostname"]),
                str(t["ssh_user"]),
                str(t["ssh_key_path"]),
                cmd,
            )
            if not result.success:
                health_failures += 1
                logger.warning("Health check failed for %s", t["vm_id"])
        except (ConnectionError, OSError, TimeoutError) as exc:
            health_failures += 1
            logger.error("Health check error for %s: %s", t["vm_id"], exc)

    total = len(wave_targets)
    failure_rate = (health_failures / total) if total > 0 else 0.0

    # Canary wave: ANY failure aborts
    if is_canary_wave:
        if health_failures > 0:
            logger.error("Canary VM FAILED health check. Aborting rollout.")
            WAVE_HEALTH_CHECKS.labels(wave=str(current_wave), outcome="failed").inc()
            return {
                "wave_aborted": True,
                "canary_passed": False,
                "current_wave": current_wave + 1,
            }
        logger.info("Canary VM PASSED health check. Proceeding.")
        WAVE_HEALTH_CHECKS.labels(wave=str(current_wave), outcome="passed").inc()
        return {
            "canary_passed": True,
            "current_wave": current_wave + 1,
        }

    # Normal threshold check for non-canary waves
    if failure_rate > threshold:
        logger.error(
            "Wave %d/%d FAILED health check (%d/%d unhealthy). Aborting.",
            current_wave + 1, state.get("total_waves", 1),
            health_failures, total,
        )
        WAVE_HEALTH_CHECKS.labels(wave=str(current_wave), outcome="failed").inc()
        return {
            "wave_aborted": True,
            "current_wave": current_wave + 1,
        }

    logger.info(
        "Wave %d/%d passed health check (%d/%d healthy)",
        current_wave + 1, state.get("total_waves", 1),
        total - health_failures, total,
    )
    WAVE_HEALTH_CHECKS.labels(wave=str(current_wave), outcome="passed").inc()
    return {
        "current_wave": current_wave + 1,
    }


async def run_vm_node(
    state: VMGraphState,
    *,
    vm_compiled: Any,
) -> dict[str, Any]:
    """Execute a single VM graph and return its results for aggregation.

    This node is the target of each Send() from the fan-out routing function.
    It receives a VMGraphState (the Send arg) and a pre-compiled vm graph
    captured in a closure.
    """
    try:
        final: VMGraphState = await vm_compiled.ainvoke(state)
        sre_disk: list[dict[str, object]] = list(final.get("disk_growth_alerts") or [])
        sre_drift: list[dict[str, object]] = list(final.get("drift_changes") or [])
        raw_logins = final.get("failed_login_summary")
        sre_logins: list[dict[str, object]] = (
            [raw_logins] if isinstance(raw_logins, dict) else []
        )
        return {
            "vm_results": final.get("results", []),
            "sre_disk_growth": sre_disk,
            "sre_drift_changes": sre_drift,
            "sre_failed_logins": sre_logins,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("VM graph crashed for %s", state.get("vm_id"))
        return {
            "vm_results": [{
                "action_type": "unknown",
                "status": ActionStatus.FAILED.value,
                "vm_id": state.get("vm_id", "unknown"),
                "started_at": datetime.now(tz=UTC).isoformat(),
                "completed_at": datetime.now(tz=UTC).isoformat(),
                "detail": "vm graph raised exception",
                "error": str(exc),
            }],
            "sre_disk_growth": [],
            "sre_drift_changes": [],
            "sre_failed_logins": [],
        }


async def collect_results_node(state: BatchGraphState) -> dict[str, Any]:
    """Collect and log aggregated results. (Passthrough — reducer handles merge.)"""
    vm_results = state.get("vm_results", [])
    batch_id = state.get("batch_id", "unknown")
    logger.info(
        "Batch %s collected %d action results across all VMs",
        batch_id, len(vm_results),
    )
    return {}


async def generate_report_node(
    state: BatchGraphState,
    *,
    batch_store: Any = None,
) -> dict[str, Any]:
    """Generate a human-readable report from aggregated SRE signals and action results."""
    from errander.models.batches import BatchStatus
    from errander.models.reports import BatchReport, DiskGrowth, DriftChange, FailedLoginSummary
    from errander.observability.reporting import render_batch_report

    batch_id = state.get("batch_id", "")
    raw_results: list[dict[str, object]] = list(state.get("vm_results") or [])

    disk_growth_alerts: list[DiskGrowth] = []
    for d in state.get("sre_disk_growth") or []:
        try:
            disk_growth_alerts.append(DiskGrowth(
                vm_id=str(d["vm_id"]),
                mountpoint=str(d["mountpoint"]),
                used_pct_start=float(str(d["used_pct_start"])),
                used_pct_end=float(str(d["used_pct_end"])),
                window_start=datetime.fromisoformat(str(d["window_start"])),
                window_end=datetime.fromisoformat(str(d["window_end"])),
            ))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Could not deserialise disk growth alert %s: %s", d, exc)

    drift_changes: list[DriftChange] = []
    for d in state.get("sre_drift_changes") or []:
        try:
            drift_changes.append(DriftChange(
                vm_id=str(d["vm_id"]),
                kind=str(d["kind"]),
                scope_key=str(d["scope_key"]),
                unified_diff=str(d["unified_diff"]),
            ))
        except (KeyError, ValueError) as exc:
            logger.warning("Could not deserialise drift change %s: %s", d, exc)

    failed_logins: list[FailedLoginSummary] = []
    for d in state.get("sre_failed_logins") or []:
        try:
            raw_users = d.get("top_users")
            raw_ips = d.get("top_source_ips")
            failed_logins.append(FailedLoginSummary(
                vm_id=str(d["vm_id"]),
                window_hours=int(str(d["window_hours"])),
                total_count=int(str(d["total_count"])),
                top_users=tuple(
                    (str(u), int(c))
                    for u, c in (raw_users if isinstance(raw_users, list) else [])
                ),
                top_source_ips=tuple(
                    (str(ip), int(c))
                    for ip, c in (raw_ips if isinstance(raw_ips, list) else [])
                ),
            ))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Could not deserialise failed logins %s: %s", d, exc)

    batch_report = BatchReport(
        batch_id=batch_id,
        generated_at=datetime.now(tz=UTC),
        vm_action_results=raw_results,
        disk_growth_alerts=disk_growth_alerts,
        drift_changes=drift_changes,
        failed_logins=failed_logins,
    )
    rendered = render_batch_report(batch_report)

    # Record batch duration
    started_at_str = state.get("batch_started_at")
    if started_at_str and isinstance(started_at_str, str):
        try:
            started_at = datetime.fromisoformat(started_at_str)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            batch_duration = (datetime.now(tz=UTC) - started_at).total_seconds()
            BATCH_DURATION.observe(batch_duration)
        except (ValueError, TypeError):
            pass  # Skip metric if timestamp is unparseable

    # Persist terminal batch status — determines COMPLETED vs ABORTED vs WITH_FAILURES.
    if batch_store is not None and batch_id:
        _error = state.get("error")
        _approved = state.get("approved")
        if _error or _approved is False:
            _final_status = BatchStatus.ABORTED
            _abort_reason = str(_error) if _error else "operator rejected"
        else:
            _failed_statuses = {"failed", "rollback_failed", "rolled_back", "needs_manual"}
            _has_failures = any(
                str(r.get("status", "")) in _failed_statuses
                for r in raw_results
            )
            _final_status = (
                BatchStatus.COMPLETED_WITH_FAILURES if _has_failures else BatchStatus.COMPLETED
            )
            _abort_reason = None
        await batch_store.update_status(batch_id, _final_status, error=_abort_reason)

    return {"report": rendered}


# --- Planning phase nodes ---

async def _load_stored_signals(
    vm_id: str,
    audit_store: Any,
    disk_history_store: Any,
    baseline_store: Any,
) -> Any:
    """Read pre-existing store data for a VM to inform planning.

    Best-effort: all failures return empty StoredSignalContext rather than blocking planning.
    """
    from errander.agent.decisions import StoredSignalContext
    from errander.models.events import EventType
    from errander.safety.disk_history import VMDiskHistoryStore as _DiskStore

    ctx = StoredSignalContext()

    if isinstance(disk_history_store, _DiskStore):
        try:
            mountpoints = await disk_history_store.get_distinct_mountpoints(vm_id)
            trend_parts: list[str] = []
            for mp in mountpoints[:3]:
                points = await disk_history_store.get_window(vm_id, mp, window_days=7)
                if len(points) >= 2:
                    delta = points[-1].used_pct - points[0].used_pct
                    trend_parts.append(
                        f"{mp}: {points[-1].used_pct:.0f}%"
                        + (f" (+{delta:.0f}% over 7d)" if delta >= 2 else "")
                    )
            ctx.disk_trend_summary = "; ".join(trend_parts)
        except Exception:
            pass

    if audit_store is not None:
        try:
            drift_events = await audit_store.get_events(
                vm_id=vm_id,
                event_type=EventType.DRIFT_KIND_CHANGED,
                limit=20,
            )
            ctx.drift_kinds_detected = sorted({
                str(e.metadata.get("kind", ""))
                for e in drift_events
                if e.metadata.get("kind")
            })
        except Exception:
            pass

    if audit_store is not None:
        try:
            failures = await audit_store.get_events(
                vm_id=vm_id,
                event_type=EventType.ACTION_FAILED,
                limit=50,
            )
            ctx.recent_failure_count = len(failures)

            patch_completed = await audit_store.get_events(
                vm_id=vm_id,
                event_type=EventType.ACTION_COMPLETED,
                limit=20,
            )
            import datetime as _dt
            for ev in patch_completed:
                if str(ev.action_type) == "patching":
                    if ev.timestamp:
                        delta = _dt.datetime.now(_dt.UTC) - ev.timestamp.replace(tzinfo=_dt.UTC)
                        ctx.last_patch_days_ago = delta.days
                    break
        except Exception:
            pass

    if audit_store is not None:
        try:
            login_events = await audit_store.get_events(
                vm_id=vm_id,
                event_type=EventType.FAILED_SSH_LOGINS_OBSERVED,
                limit=5,
            )
            for ev in login_events:
                ctx.failed_login_count_24h += int(str(ev.metadata.get("total_count", 0)))
        except Exception:
            pass

    return ctx


async def plan_vm_node(
    state: dict[str, Any],
    *,
    ssh_manager: SSHConnectionManager,
    llm_client: Any = None,
    ai_decision_store: Any = None,
    audit_store: Any = None,
    disk_history_store: Any = None,
    baseline_store: Any = None,
) -> dict[str, Any]:
    """Plan actions for a single VM without any execution.

    Runs OS detection + LLM-powered action prioritization via SSH (read-only),
    then returns the planned actions for inclusion in the ImmutableBatchPlan.
    The same llm_client and ai_decision_store used at batch-level planning are
    reused here so the approved plan is generated by the LLM path (blocker #2).

    No package upgrades, no file changes — purely informational SSH calls.
    """
    vm_id = str(state.get("vm_id", ""))
    hostname = str(state.get("hostname", ""))
    ssh_user = str(state.get("ssh_user", ""))
    key_path = str(state.get("ssh_key_path", ""))
    env_policy = str(state.get("env_policy", "moderate"))
    batch_id = str(state.get("batch_id", "unknown"))

    try:
        vm_info = await detect_os(
            vm_id=vm_id,
            hostname=hostname,
            username=ssh_user,
            key_path=key_path,
            ssh_manager=ssh_manager,
        )
    except (ValueError, ConnectionError, OSError) as exc:
        logger.warning("Planning SSH failed for %s: %s — VM excluded from plan", vm_id, exc)
        return {"vm_plans": []}

    stored_signals = await _load_stored_signals(
        vm_id=vm_id,
        audit_store=audit_store,
        disk_history_store=disk_history_store,
        baseline_store=baseline_store,
    )

    # Build available_actions from the inventory-enabled list. None falls back to
    # DEFAULT_PRIORITY (used only when state predates this field — tests, replays).
    _enabled_names: list[str] | None = state.get("enabled_actions")
    _valid_action_values = {m.value for m in ActionType}
    available_for_planning: list[ActionType] | None = (
        [ActionType(n) for n in _enabled_names if n in _valid_action_values]
        if _enabled_names is not None
        else None
    )

    actions = await prioritize_actions(
        vm_info,
        llm_client=llm_client,
        policy=env_policy,
        batch_id=batch_id,
        vm_id=vm_id,
        ai_store=ai_decision_store,
        stored_signals=stored_signals,
        available_actions=available_for_planning,
    )

    return {
        "vm_plans": [{
            "vm_id": vm_id,
            "planned_actions": [
                {
                    "action_type": a.action_type.value,
                    "risk_tier": a.risk_tier.value,
                    "params": a.params,  # included in plan hash and wave dispatch
                }
                for a in actions
            ],
            "os_family": vm_info.os_family.value,
        }],
    }


async def collect_plans_node(state: BatchGraphState) -> dict[str, Any]:
    """Log collected planning results. (Passthrough — reducer handles merge.)"""
    vm_plans = state.get("vm_plans", [])
    batch_id = state.get("batch_id", "unknown")
    logger.info("Batch %s collected plans for %d VMs", batch_id, len(vm_plans))
    return {}


async def enrich_plan_node(
    state: BatchGraphState,
    *,
    ssh_manager: SSHConnectionManager,
) -> dict[str, Any]:
    """Assess exact packages/space per planned action before the plan is hashed.

    Runs SSH read commands per VM per action type so the plan hash covers
    the exact packages/versions the operator will approve -- not just action
    categories. Failures are best-effort; an unreachable VM gets
    preview={"error": "..."} and the batch continues.

    P0-1: what makes the HITL guarantee honest.
    """
    vm_plans: list[dict[str, object]] = list(state.get("vm_plans", []))
    targets: list[dict[str, object]] = list(state.get("targets", []))
    target_by_id: dict[str, dict[str, object]] = {
        str(t.get("vm_id", "")): t for t in targets
    }

    enriched_plans: list[dict[str, object]] = list(
        await asyncio.gather(*[
            _enrich_vm_plan(plan, target_by_id, ssh_manager)
            for plan in vm_plans
        ])
    )
    return {"enriched_vm_plans": enriched_plans}


async def _enrich_vm_plan(
    plan: dict[str, object],
    target_by_id: dict[str, dict[str, object]],
    ssh_manager: SSHConnectionManager,
) -> dict[str, object]:
    """Enrich one VM's planned actions with preview data. Best-effort."""
    vm_id = str(plan.get("vm_id", ""))
    os_family = str(plan.get("os_family", "ubuntu"))
    target = target_by_id.get(vm_id, {})
    hostname = str(target.get("hostname", ""))
    username = str(target.get("ssh_user", "errander-ai"))
    key_path = str(target.get("ssh_key_path", ""))

    actions_raw = plan.get("planned_actions")
    if not isinstance(actions_raw, list):
        return plan

    enriched_actions: list[dict[str, object]] = []
    for action in actions_raw:
        if not isinstance(action, dict):
            enriched_actions.append(action)
            continue

        action_type = str(action.get("action_type", ""))
        preview: dict[str, object] = {}

        try:
            if action_type == "patching" and hostname:
                preview = await _preview_patching(
                    vm_id, hostname, username, key_path, os_family, ssh_manager
                )
            elif action_type == "disk_cleanup" and hostname:
                preview = await _preview_disk_cleanup(
                    vm_id, hostname, username, key_path, ssh_manager
                )
            # docker_prune / log_rotation / backup_verify: no preview for MVP
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enrich_plan: preview failed for %s/%s: %s", vm_id, action_type, exc
            )
            preview = {"error": str(exc)[:200]}

        enriched_actions.append({**action, "preview": preview})

    return {**plan, "planned_actions": enriched_actions}


async def _preview_patching(
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    os_family: str,
    ssh_manager: SSHConnectionManager,
) -> dict[str, object]:
    """Query exact upgradable packages with versions for patching preview."""
    import fnmatch

    from errander.agent.subgraphs.disk_cleanup import get_package_manager_by_name
    from errander.agent.subgraphs.patching import (
        MANDATORY_KERNEL_EXCLUDES,
        _parse_upgradable_with_versions,
    )

    pkg_mgr = get_package_manager_by_name(os_family)
    result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, pkg_mgr.list_upgradable(),
    )
    if not result.success:
        return {"error": f"could not list packages: {result.stderr[:200]}"}

    packages = _parse_upgradable_with_versions(result.stdout, os_family)
    filtered = [
        p for p in packages
        if not any(fnmatch.fnmatch(p["name"], pat) for pat in MANDATORY_KERNEL_EXCLUDES)
    ]
    return {
        "packages": filtered,
        "package_count": len(filtered),
        "total_upgradable": len(packages),
    }


async def _preview_disk_cleanup(
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    ssh_manager: SSHConnectionManager,
) -> dict[str, object]:
    """Query disk usage and apt cache size for disk_cleanup preview."""
    preview: dict[str, object] = {}

    df_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path,
        "df -BM / 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%'",
    )
    if df_result.success:
        with contextlib.suppress(ValueError):
            preview["disk_pct"] = int(df_result.stdout.strip())

    cache_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path,
        "du -sm /var/cache/apt/archives 2>/dev/null | awk '{print $1}'",
    )
    if cache_result.success:
        with contextlib.suppress(ValueError):
            preview["apt_cache_mb"] = int(cache_result.stdout.strip())

    return preview


async def generate_plan_artifact_node(state: BatchGraphState) -> dict[str, Any]:
    """Build ImmutableBatchPlan from collected VM plans and compute hash.

    The plan_hash is stored in state and included in the Slack approval
    request. Live execution validates hash stability (finding #3).
    """
    import hashlib
    import json
    import uuid

    vm_plans = _effective_vm_plans(state)
    batch_id = state.get("batch_id", "unknown")
    env_name = state.get("env_name", "")
    plan_id = f"plan-{uuid.uuid4().hex[:12]}"

    canonical = json.dumps(
        {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
        sort_keys=True,
        default=str,
    )
    plan_hash = hashlib.sha256(canonical.encode()).hexdigest()

    logger.info(
        "Plan %s generated: hash=%s, %d VMs planned",
        plan_id, plan_hash[:12], len(vm_plans),
    )
    return {"plan_id": plan_id, "plan_hash": plan_hash}


# --- Approval gate ---

def _max_risk_tier_from_results(vm_results: list[dict[str, object]]) -> RiskTier:
    """Determine the highest risk tier across all action results."""
    max_tier = RiskTier.LOW
    tier_order = {RiskTier.LOW: 0, RiskTier.MEDIUM: 1, RiskTier.HIGH: 2, RiskTier.CRITICAL: 3}
    for r in vm_results:
        action_type_str = str(r.get("action_type", ""))
        try:
            action_type = ActionType(action_type_str)
            tier = ACTION_RISK_TIERS.get(action_type, RiskTier.MEDIUM)
            if tier_order.get(tier, 0) > tier_order.get(max_tier, 0):
                max_tier = tier
        except ValueError:
            continue
    return max_tier


def _max_risk_tier_from_plans(vm_plans: list[dict[str, object]]) -> RiskTier:
    """Determine the highest risk tier across all planned actions (finding #3)."""
    max_tier = RiskTier.LOW
    tier_order = {RiskTier.LOW: 0, RiskTier.MEDIUM: 1, RiskTier.HIGH: 2, RiskTier.CRITICAL: 3}
    for plan in vm_plans:
        _pa = plan.get("planned_actions")
        for action in (_pa if isinstance(_pa, list) else []):
            try:
                tier = RiskTier(str(action.get("risk_tier", "low")))
                if tier_order.get(tier, 0) > tier_order.get(max_tier, 0):
                    max_tier = tier
            except ValueError:
                continue
    return max_tier


def _format_plan_for_approval(
    vm_plans: list[dict[str, object]],
    batch_id: str,
    plan_id: str,
    plan_hash: str,
    is_deferred_reapproval: bool = False,
) -> str:
    """Format a batch plan for the Slack approval message.

    P0-1: Shows exact packages/versions from enrich_plan_node preview data.
    Hash commits to this exact artifact.
    """
    header = (
        f":repeat: *Deferred Re-Approval Required* -- Batch `{batch_id}`"
        if is_deferred_reapproval
        else f"*Live Execution Approval* -- Batch `{batch_id}`"
    )
    lines = [
        header,
        f"Plan: `{plan_id}` | Hash: `{plan_hash[:16]}`",
        f"{len(vm_plans)} VM(s):",
    ]
    for plan in vm_plans:
        vm_id = plan.get("vm_id", "?")
        lines.append(f"\n  *`{vm_id}`*:")
        raw_actions = plan.get("planned_actions")
        for a in (raw_actions if isinstance(raw_actions, list) else []):
            action_type = str(a.get("action_type", "?"))
            preview = a.get("preview") if isinstance(a.get("preview"), dict) else {}
            assert isinstance(preview, dict)

            if action_type == "patching":
                packages = preview.get("packages")
                if packages and isinstance(packages, list):
                    lines.append(f"    - patching: {len(packages)} package(s)")
                    for pkg in packages[:10]:
                        if not isinstance(pkg, dict):
                            continue
                        name = str(pkg.get("name", "?"))
                        cur = str(pkg.get("current", ""))
                        tgt = str(pkg.get("target", ""))
                        if cur and tgt:
                            lines.append(f"      `{name}`  {cur} -> {tgt}")
                        else:
                            lines.append(f"      `{name}`")
                    if len(packages) > 10:
                        lines.append(f"      ... and {len(packages) - 10} more")
                elif "error" in preview:
                    lines.append(
                        f"    - patching (preview unavailable: {str(preview['error'])[:80]})"
                    )
                else:
                    # No preview data — show params as before (dry-run or enrich skipped)
                    params = a.get("params") or {}
                    label = "patching"
                    if isinstance(params, dict) and params:
                        param_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
                        label = f"patching({param_str})"
                    lines.append(f"    - {label}")

            elif action_type == "disk_cleanup":
                disk_pct = preview.get("disk_pct")
                cache_mb = preview.get("apt_cache_mb")
                detail_parts = []
                if isinstance(disk_pct, int):
                    detail_parts.append(f"{disk_pct}% disk used")
                if isinstance(cache_mb, int):
                    detail_parts.append(f"~{cache_mb}MB apt cache")
                detail = f": {', '.join(detail_parts)}" if detail_parts else ""
                lines.append(f"    - disk_cleanup{detail}")

            else:
                params = a.get("params") or {}
                label = action_type
                if isinstance(params, dict) and params:
                    param_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
                    label = f"{action_type}({param_str})"
                lines.append(f"    - {label}")

    lines.extend([
        "",
        f"Hash `{plan_hash[:16]}` commits to the exact packages and actions listed above.",
        "",
        "Reply :white_check_mark: to approve or :x: to reject (timeout -> auto-REJECT)",
    ])
    return "\n".join(lines)


async def approval_gate_node(
    state: BatchGraphState,
    *,
    approval_manager: ApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    audit_store: AuditStore | None = None,
    window: MaintenanceWindow | None = None,
    deferred_store: DeferredExecutionStore | None = None,
    approval_timeout_seconds: int = 1800,
    approval_poll_interval_seconds: int = 30,
    require_live_approval: bool = True,
    autonomous_live_apply_enabled: bool = False,
) -> dict[str, Any]:
    """Gate live execution behind Slack approval of the ImmutableBatchPlan.

    Approval now happens BEFORE execution (finding #3):
    - Dry-run: auto-approve — sandbox execution is always safe.
    - Live: approval threshold depends on env_policy (finding #6):
        strict   → MEDIUM, HIGH, CRITICAL require approval
        moderate → HIGH, CRITICAL require approval (default)
        relaxed  → CRITICAL only (and CRITICAL is blocked by design)

    For live batches approved while outside the maintenance window,
    execution is deferred: a record is saved to DeferredExecutionStore
    and the window-opener scheduler job picks it up at window start.
    Dry-run batches always execute immediately regardless of window.
    """
    vm_plans = _effective_vm_plans(state)
    batch_id = state.get("batch_id", "unknown")
    env_name = state.get("env_name", "unknown")
    env_policy = state.get("env_policy", "strict")
    plan_id = state.get("plan_id", "unknown")
    plan_hash = state.get("plan_hash", "")
    dry_run = state.get("dry_run", True)

    max_tier = _max_risk_tier_from_plans(vm_plans)

    # Enforce the autonomous mode gate: if autonomous_live_apply_enabled=False (default),
    # require_live_approval cannot be disabled — HITL is mandatory.
    if not autonomous_live_apply_enabled and not require_live_approval and not dry_run:
        logger.warning(
            "Batch %s: require_live_approval=False rejected — autonomous_live_apply_enabled=False "
            "enforces HITL. Forcing require_live_approval=True.",
            batch_id,
        )
        require_live_approval = True

    # When require_live_approval=True (HITL guardrail, default while P0-1/P0-2
    # are open), ALL live tiers require human approval — policy is ignored.
    if require_live_approval and not dry_run:
        _approval_tiers: frozenset[RiskTier] = frozenset({
            RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL,
        })
    elif env_policy == "strict":
        _approval_tiers = frozenset({
            RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL,
        })
    elif env_policy == "relaxed":
        _approval_tiers = frozenset({RiskTier.CRITICAL})
    else:  # moderate
        _approval_tiers = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})

    # P0-2: deferred replay — carry forward the original operator approval.
    # The hash was verified by load_deferred_artifact_node; no re-approval needed.
    if bool(state.get("is_deferred_replay", False)):
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.APPROVAL_GRANTED,
                batch_id=batch_id,
                detail=(
                    f"P0-2 replay: carrying forward original approval — "
                    f"artifact hash {plan_hash[:12]} verified. "
                    f"No re-approval required."
                ),
                metadata={"replay_mode": True, "plan_hash": plan_hash[:12]},
            ))
        logger.info(
            "Batch %s P0-2 replay: auto-approved (hash=%s verified)",
            batch_id, plan_hash[:12],
        )
        return {"approved": True}

    if dry_run:
        # Sandbox execution is safe — no operator approval needed.
        # Graduating dry-run → live is a separate operator-initiated action (finding #4).
        approved = True
        approver = None
        logger.info(
            "Batch %s dry-run — proceeding to sandbox execution (max risk tier: %s)",
            batch_id, max_tier.value,
        )
    elif max_tier in _approval_tiers and approval_manager is None:
        # Approval required but no mechanism available — fail closed.
        # This guards against misconfigured deployments where require_live_approval=True
        # but the approval manager was never wired up.
        logger.error(
            "Batch %s BLOCKED: live approval required (require_live_approval=%s, "
            "policy=%s, max tier=%s) but no approval_manager is configured — "
            "refusing live execution",
            batch_id, require_live_approval, env_policy, max_tier.value,
        )
        return {"approved": False, "error": "live approval required but no approval manager configured"}
    elif max_tier in _approval_tiers and approval_manager is not None:
        is_deferred = bool(state.get("is_deferred_reapproval", False))
        plan_summary = _format_plan_for_approval(
            vm_plans, batch_id, plan_id, plan_hash,
            is_deferred_reapproval=is_deferred,
        )
        logger.info(
            "Batch %s requires live approval before execution (policy=%s, max risk tier: %s%s)",
            batch_id, env_policy, max_tier.value,
            " [deferred re-approval]" if is_deferred else "",
        )
        approved, approver = await await_dual_approval(
            approval_manager, slack_client, batch_id, plan_summary,
            timeout_seconds=approval_timeout_seconds,
            poll_interval_seconds=approval_poll_interval_seconds,
        )
        logger.info(
            "Batch %s %s by %s",
            batch_id,
            "approved" if approved else "rejected",
            approver or "timeout",
        )
    else:
        approved = True
        approver = None
        logger.info(
            "Batch %s live auto-approved (policy=%s, max risk tier: %s)",
            batch_id, env_policy, max_tier.value,
        )

    # Live approved outside window → defer execution to next window start.
    # Dry-run always runs immediately (sandbox is window-agnostic).
    if approved and not dry_run and window is not None:
        now = datetime.now(tz=UTC)
        if not check_window_from_config(now, window):
            next_open = next_window_open(now, window)
            if deferred_store is not None:
                # P0-2: serialize exact plan artifact for faithful replay at window time
                import json as _json
                _plan_json = _json.dumps(
                    {"plan_id": plan_id, "vm_plans": vm_plans},
                    default=str,
                )
                await deferred_store.save(
                    batch_id=batch_id,
                    env_name=env_name,
                    approved_by=approver,
                    window_start=next_open,
                    plan_json=_plan_json,
                    plan_hash=plan_hash,
                )
            if audit_store is not None:
                await audit_store.log_event(AuditEvent(
                    event_type=EventType.EXECUTION_DEFERRED,
                    batch_id=batch_id,
                    detail=f"Deferred to {next_open.isoformat()}",
                    metadata={
                        "window_start": next_open.isoformat(),
                        "approved_by": approver,
                        "plan_hash": plan_hash[:12],
                        "artifact_saved": True,
                    },
                ))
            if slack_client is not None:
                approver_label = approver or "auto"
                await slack_client.post_alert(
                    f"Batch `{batch_id}` approved by {approver_label}.\n"
                    f"Outside maintenance window — execution scheduled for "
                    f"{next_open.strftime('%Y-%m-%d %H:%M UTC')}"
                )
            logger.info(
                "Batch %s deferred to %s (approved by %s)",
                batch_id, next_open.isoformat(), approver,
            )
            return {"approved": True, "deferred": True}

    return {"approved": approved, "deferred": False}


async def verify_plan_hash_node(state: BatchGraphState) -> dict[str, Any]:
    """Re-verify plan hash hasn't drifted between approval and execution (finding #3).

    Dry-run is exempt — sandbox execution is always safe regardless of hash.
    For live runs, re-computes SHA-256 from current state and compares to
    the hash that was approved. Any mismatch aborts execution cleanly.
    """
    import hashlib
    import json

    dry_run = state.get("dry_run", True)
    if dry_run:
        return {}

    stored_hash = state.get("plan_hash", "")
    if not stored_hash:
        logger.error("Batch %s: plan_hash missing — cannot verify integrity before execution", state.get("batch_id"))
        return {
            "error": "plan_hash missing — cannot verify plan integrity before execution",
            "approved": False,
        }

    vm_plans = _effective_vm_plans(state)
    batch_id = state.get("batch_id", "unknown")
    env_name = state.get("env_name", "")

    canonical = json.dumps(
        {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
        sort_keys=True,
        default=str,
    )
    current_hash = hashlib.sha256(canonical.encode()).hexdigest()

    if current_hash != stored_hash:
        logger.error(
            "PLAN HASH DRIFT on batch %s: approved=%s current=%s — aborting execution",
            batch_id, stored_hash[:12], current_hash[:12],
        )
        return {
            "error": (
                f"plan integrity check failed: hash drifted between approval and execution "
                f"(approved={stored_hash[:12]}, current={current_hash[:12]})"
            ),
            "approved": False,
        }

    logger.info("Plan hash verified for batch %s (%s)", batch_id, stored_hash[:12])
    return {}


# --- Routing ---

def route_after_prepare_waves(state: BatchGraphState) -> str:
    waves = state.get("waves", [])
    if not waves:
        return "generate_report"
    return "dispatch_wave"


def route_after_wave_health(state: BatchGraphState) -> str:
    if state.get("wave_aborted"):
        return "collect_results"

    current_wave = state.get("current_wave", 0)
    total_waves = state.get("total_waves", 0)

    if current_wave < total_waves:
        return "dispatch_wave"    # next wave
    return "collect_results"      # all waves done


def route_after_window(state: BatchGraphState) -> str:
    """Block if window check sets an error."""
    if state.get("error"):
        return "generate_report"
    return "validate_targets"


_DEFERRED_MAX_ARTIFACT_AGE_HOURS = 168  # 7 days, matches DeferredExecutionStore._EXPIRY_DAYS


async def load_deferred_artifact_node(state: BatchGraphState) -> dict[str, Any]:
    """P0-2 replay mode: deserialize stored artifact, verify hash, skip planning.

    Populates vm_plans + plan_id + plan_hash from the stored JSON blob.
    Hash mismatch or exceeded age limit → returns error so route_after_approval aborts cleanly.
    With pinned execution, packages install exact approved versions; unavailable versions
    fail closed at execution time.
    """
    import hashlib
    import json
    from datetime import UTC, datetime

    raw_json = state.get("preloaded_plan_json") or ""
    stored_hash = state.get("preloaded_plan_hash") or ""
    batch_id = state.get("batch_id", "unknown")
    env_name = state.get("env_name", "")

    if not raw_json or not stored_hash:
        return {"error": "P0-2 replay: missing plan artifact — aborting"}

    # Age check: artifact replay without a valid approval timestamp is not safe — fail closed.
    # Legacy records without a stored artifact fall back to re-plan/re-approve (plan_json is None
    # so this node is never reached for them).
    approved_at_str = (state.get("preloaded_approved_at") or "").strip()
    if not approved_at_str:
        return {
            "error": (
                "P0-2 replay: missing approval timestamp — "
                "cannot verify artifact age; re-approval required"
            ),
        }
    try:
        approved_at = datetime.fromisoformat(approved_at_str)
        age_hours = (datetime.now(tz=UTC) - approved_at).total_seconds() / 3600
        if age_hours > _DEFERRED_MAX_ARTIFACT_AGE_HOURS:
            return {
                "error": (
                    f"P0-2 replay: artifact approved {age_hours:.0f}h ago exceeds "
                    f"{_DEFERRED_MAX_ARTIFACT_AGE_HOURS}h limit — re-approval required"
                ),
            }
        if age_hours > 24:
            logger.warning(
                "P0-2 replay: artifact is %.0fh old for batch %s — "
                "pinned install will fail closed if approved versions are unavailable",
                age_hours, batch_id,
            )
    except ValueError:
        return {
            "error": (
                f"P0-2 replay: unparseable approval timestamp {approved_at_str!r} — "
                "re-approval required"
            ),
        }

    try:
        artifact = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return {"error": f"P0-2 replay: corrupt plan artifact JSON — {exc}"}

    vm_plans = artifact.get("vm_plans", [])
    plan_id = artifact.get("plan_id", "replayed")

    canonical = json.dumps(
        {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
        sort_keys=True, default=str,
    )
    computed = hashlib.sha256(canonical.encode()).hexdigest()
    if computed != stored_hash:
        logger.error(
            "P0-2 replay: hash mismatch for batch %s — stored=%s computed=%s",
            batch_id, stored_hash[:12], computed[:12],
        )
        return {
            "error": (
                f"P0-2 replay: artifact hash mismatch — possible tampering (batch {batch_id})"
            ),
        }

    logger.info(
        "P0-2 replay: artifact verified for batch %s (hash=%s, %d VMs, approved_at=%s)",
        batch_id, stored_hash[:12], len(vm_plans), approved_at_str or "unknown",
    )
    return {
        "enriched_vm_plans": vm_plans,
        "plan_id": plan_id,
        "plan_hash": stored_hash,
        "is_deferred_replay": True,
    }


def route_after_fleet_check(state: BatchGraphState) -> str:
    """Route after fleet health check: abort (too many failures), replay, or continue."""
    if state.get("error"):
        return "generate_report"
    if not state.get("healthy_targets"):
        return "generate_report"
    # P0-2: if a preloaded artifact is present, skip planning entirely
    if state.get("preloaded_plan_json"):
        return "load_deferred_artifact"
    return "plan_vms"


def route_after_approval(state: BatchGraphState) -> str:
    """Route after plan approval: verify hash (approved), report (rejected), end (deferred)."""
    if state.get("deferred"):
        return END
    if not state.get("approved"):
        return "generate_report"
    return "verify_plan_hash"


def route_after_hash_verify(state: BatchGraphState) -> str:
    """Route after hash verification: execute (ok), report (drift detected)."""
    if state.get("error") or state.get("approved") is False:
        return "generate_report"
    return "prepare_waves"


def make_fan_out_router(
    batch_id_getter: Any,
    executor: SandboxExecutor,
    locker: FileLocker,
    audit_store: AuditStore,
    ssh_manager: SSHConnectionManager,
) -> Any:
    """Build the fan-out routing function (closure over dependencies).

    Kept for backward compatibility with existing tests.
    Returns a function that either sends to generate_report (no healthy
    targets) or emits a list of Send() objects — one per healthy target.
    LangGraph processes Send() returns as parallel node invocations.
    """
    vm_compiled = build_vm_graph(executor, locker, audit_store, ssh_manager).compile()

    def route_after_validate(
        state: BatchGraphState,
    ) -> str | list[Send]:
        healthy = state.get("healthy_targets", [])
        if not healthy:
            return "generate_report"

        batch_id = state.get("batch_id", "unknown")
        dry_run = state.get("dry_run", True)
        env_policy = state.get("env_policy", "strict")
        docker_command_mode = state.get("docker_command_mode", "wrapper")

        return [
            Send(
                "run_vm",
                VMGraphState(
                    vm_id=str(t["vm_id"]),
                    batch_id=batch_id,
                    dry_run=dry_run,
                    hostname=str(t["hostname"]),
                    ssh_user=str(t["ssh_user"]),
                    ssh_key_path=str(t["ssh_key_path"]),
                    os_family=str(t.get("os_family", "ubuntu")),
                    env_policy=env_policy,
                    docker_command_mode=docker_command_mode,
                    locked=False,
                    results=[],
                    current_action_index=0,
                    planned_actions=[],
                    error=None,
                    drift_detection_enabled=state.get("drift_detection_enabled", False),
                    drift_abort_on_detection=state.get("drift_abort_on_detection", False),
                    disable_failed_login_check=bool(t.get("disable_failed_login_check", False)),
                    critical_services=list(t.get("critical_services") or []),  # type: ignore[call-overload]
                ),
            )
            for t in healthy
        ]

    return route_after_validate, vm_compiled


def make_wave_dispatcher(
    executor: SandboxExecutor,
    locker: FileLocker,
    audit_store: AuditStore,
    ssh_manager: SSHConnectionManager,
    llm_client: Any = None,
    ai_decision_store: Any = None,
    disk_history_store: object = None,
    sre_disk_settings: object = None,
    baseline_store: object = None,
    sre_drift_settings: object = None,
    sre_failed_logins_settings: object = None,
    vm_state_store: object = None,
) -> tuple[Any, Any]:
    """Build the wave dispatch routing function.

    Returns (dispatch_fn, vm_compiled). The dispatch_fn emits
    Send() for only the current wave's targets.
    """
    vm_compiled = build_vm_graph(
        executor, locker, audit_store, ssh_manager,
        llm_client=llm_client,
        ai_decision_store=ai_decision_store,
        disk_history_store=disk_history_store,
        sre_disk_settings=sre_disk_settings,
        baseline_store=baseline_store,
        sre_drift_settings=sre_drift_settings,
        sre_failed_logins_settings=sre_failed_logins_settings,
        vm_state_store=vm_state_store,
    ).compile()

    def dispatch_current_wave(state: BatchGraphState) -> str | list[Send]:
        current_wave = state.get("current_wave", 0)
        waves = state.get("waves", [])

        if current_wave >= len(waves) or not waves[current_wave]:
            return "check_wave_health"

        wave_targets = waves[current_wave]
        batch_id = state.get("batch_id", "unknown")
        dry_run = state.get("dry_run", True)

        logger.info(
            "Dispatching wave %d/%d: %d VMs",
            current_wave + 1, state.get("total_waves", 1), len(wave_targets),
        )

        env_policy = state.get("env_policy", "strict")
        ai_db_path = state.get("ai_db_path", "")
        docker_command_mode = state.get("docker_command_mode", "wrapper")

        # Build approved plan lookup: vm_id → planned_actions.
        # Execution MUST follow the approved plan — the VM graph skips re-planning
        # when pre_approved_plan_set=True.
        vm_id_to_approved_actions: dict[str, list[dict[str, object]]] = {
            str(p["vm_id"]): list(p.get("planned_actions") or [])  # type: ignore[call-overload]
            for p in _effective_vm_plans(state)
        }

        sends: list[Send] = []
        for t in wave_targets:
            vm_id_str = str(t["vm_id"])
            if vm_id_str in vm_id_to_approved_actions:
                approved_actions = vm_id_to_approved_actions[vm_id_str]
                vm_error: str | None = None
            elif not dry_run:
                # Live mode: VM missing from approved plan — fail closed.
                # Never allow re-planning after the operator approved a specific plan.
                logger.error(
                    "VM %s not in approved plan — failing closed (live run requires approved plan)",
                    vm_id_str,
                )
                approved_actions = []
                vm_error = "VM not in approved plan — cannot execute without approval"
            else:
                # Dry-run: allow normal re-planning (no approved plan yet).
                approved_actions = []
                vm_error = None

            sends.append(
                Send(
                    "run_vm",
                    VMGraphState(
                        vm_id=vm_id_str,
                        batch_id=batch_id,
                        dry_run=dry_run,
                        hostname=str(t["hostname"]),
                        ssh_user=str(t["ssh_user"]),
                        ssh_key_path=str(t["ssh_key_path"]),
                        os_family=str(t.get("os_family", "ubuntu")),
                        env_policy=env_policy,
                        ai_db_path=ai_db_path,
                        docker_command_mode=docker_command_mode,
                        locked=False,
                        results=[],
                        current_action_index=0,
                        planned_actions=approved_actions,
                        # Mark plan as explicitly set so VM graph never re-plans after approval.
                        # False only when dry-run without an approved plan (pre-approval phase).
                        pre_approved_plan_set=(vm_id_str in vm_id_to_approved_actions or not dry_run),
                        error=vm_error,
                        drift_detection_enabled=state.get("drift_detection_enabled", False),
                        drift_abort_on_detection=state.get("drift_abort_on_detection", False),
                        disable_failed_login_check=bool(t.get("disable_failed_login_check", False)),
                        critical_services=list(t.get("critical_services") or []),  # type: ignore[call-overload]
                    ),
                )
            )
        return sends

    return dispatch_current_wave, vm_compiled


def route_after_validate(state: BatchGraphState) -> str:
    """Standalone routing function used in tests (no Send, no dependencies)."""
    if not state.get("healthy_targets"):
        return "generate_report"
    return "fan_out"  # only used in unit test context


def route_plan_vms(state: BatchGraphState) -> str | list[Send]:
    """Fan-out router: emit one Send("plan_vm", ...) per healthy target.

    Extracted to module level so it can be unit-tested without building the
    full graph. Each Send payload carries the per-VM identity fields PLUS
    ``enabled_actions`` from batch state so ``plan_vm_node`` can pass it to
    ``prioritize_actions(available_actions=...)``.

    When ``enabled_actions`` is absent from state (old states, replays), the key
    is omitted from the Send payload so ``plan_vm_node`` falls back to
    DEFAULT_PRIORITY rather than planning zero actions.
    """
    healthy = state.get("healthy_targets", [])
    if not healthy:
        return "generate_report"
    batch_id = state.get("batch_id", "unknown")
    env_policy = state.get("env_policy", "strict")
    _enabled_raw: list[str] | None = state.get("enabled_actions")
    sends = []
    for t in healthy:
        payload: dict[str, object] = {
            "vm_id": str(t["vm_id"]),
            "hostname": str(t["hostname"]),
            "ssh_user": str(t["ssh_user"]),
            "ssh_key_path": str(t["ssh_key_path"]),
            "batch_id": batch_id,
            "env_policy": env_policy,
        }
        if _enabled_raw is not None:
            payload["enabled_actions"] = list(_enabled_raw)
        sends.append(Send("plan_vm", payload))
    return sends


# --- Graph builder ---

def build_batch_graph(
    executor: SandboxExecutor,
    locker: FileLocker,
    audit_store: AuditStore,
    ssh_manager: SSHConnectionManager,
    window: MaintenanceWindow | None = None,
    approval_manager: ApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    settings: Any = None,
    deferred_store: DeferredExecutionStore | None = None,
    llm_client: Any = None,
    ai_decision_store: Any = None,
    disk_history_store: object = None,
    sre_disk_settings: object = None,
    baseline_store: object = None,
    sre_drift_settings: object = None,
    sre_failed_logins_settings: object = None,
    vm_state_store: object = None,
) -> StateGraph[BatchGraphState]:
    """Construct the batch orchestrator graph.

    New plan/apply flow (finding #3):
      init_batch → validate_window → validate_targets
      → plan_vm (fan-out) → collect_plans → generate_plan_artifact
      → approval_gate (BEFORE execution)
      → prepare_waves → dispatch_wave → run_vm (fan-out)
      → check_wave_health → collect_results → generate_report → END

    Args:
        executor: SandboxExecutor for SSH command execution.
        locker: FileLocker for VM-level locking.
        audit_store: AuditStore for audit trail.
        ssh_manager: SSHConnectionManager for SSH connections.
        window: Optional maintenance window.
        approval_manager: Optional ApprovalManager for Slack approval.
        slack_client: Optional SlackClient for approval notifications.
        settings: Optional Settings instance for rolling/canary/drift config.
        deferred_store: Optional store for deferred batch execution.

    Returns:
        StateGraph for the batch orchestrator (call .compile() to use).
    """
    from errander.config.settings import Settings as _Settings
    _settings: _Settings = settings if settings is not None else _Settings()

    # BatchStore shares the audit DB connection — no second file handle needed.
    # isinstance guard: test mocks don't return real BatchStore objects; skip gracefully.
    from errander.safety.batches import BatchStore as _BatchStore
    _raw_batch_store = audit_store.make_batch_store()
    _batch_store: _BatchStore | None = (
        _raw_batch_store if isinstance(_raw_batch_store, _BatchStore) else None
    )

    builder: StateGraph[BatchGraphState] = StateGraph(BatchGraphState)

    # --- Closures capturing injected dependencies ---

    async def _init_batch(state: BatchGraphState) -> dict[str, Any]:
        return await init_batch_node(state, settings=_settings, batch_store=_batch_store)

    async def _validate_window(state: BatchGraphState) -> dict[str, Any]:
        return await validate_window_node(state, window=window)

    async def _validate_targets(state: BatchGraphState) -> dict[str, Any]:
        return await validate_targets_node(
            state, ssh_manager=ssh_manager, audit_store=audit_store,
        )

    async def _plan_vm(state: dict[str, Any]) -> dict[str, Any]:
        return await plan_vm_node(
            state,
            ssh_manager=ssh_manager,
            llm_client=llm_client,
            ai_decision_store=ai_decision_store,
            audit_store=audit_store,
            disk_history_store=disk_history_store,
            baseline_store=baseline_store,
        )

    async def _check_wave_health(state: BatchGraphState) -> dict[str, Any]:
        return await check_wave_health_node(
            state, ssh_manager=ssh_manager,
            health_check_command=_settings.health_check_command,
        )

    async def _approval_gate(state: BatchGraphState) -> dict[str, Any]:
        return await approval_gate_node(
            state,
            approval_manager=approval_manager,
            slack_client=slack_client,
            audit_store=audit_store,
            window=window,
            deferred_store=deferred_store,
            approval_timeout_seconds=_settings.approval_timeout_seconds,
            approval_poll_interval_seconds=_settings.approval_poll_interval_seconds,
            require_live_approval=_settings.require_live_approval,
            autonomous_live_apply_enabled=_settings.autonomous_live_apply_enabled,
        )

    async def _check_fleet(state: BatchGraphState) -> dict[str, Any]:
        return await check_fleet_health_node(
            state,
            audit_store=audit_store,
            fleet_failure_threshold=_settings.fleet_failure_threshold,
        )

    _dispatch_wave_fn, vm_compiled = make_wave_dispatcher(
        executor, locker, audit_store, ssh_manager,
        llm_client=llm_client,
        ai_decision_store=ai_decision_store,
        disk_history_store=disk_history_store,
        sre_disk_settings=sre_disk_settings,
        baseline_store=baseline_store,
        sre_drift_settings=sre_drift_settings,
        sre_failed_logins_settings=sre_failed_logins_settings,
        vm_state_store=vm_state_store,
    )

    async def _run_vm(state: VMGraphState) -> dict[str, Any]:
        return await run_vm_node(state, vm_compiled=vm_compiled)

    # Fan-out router: Send one plan_vm invocation per healthy target
    def _route_plan_vms(state: BatchGraphState) -> str | list[Send]:
        return route_plan_vms(state)

    # --- Nodes ---
    builder.add_node("init_batch", _init_batch)
    builder.add_node("validate_window", _validate_window)
    builder.add_node("validate_targets", _validate_targets)
    builder.add_node("check_fleet_health", _check_fleet)
    builder.add_node("plan_vm", _plan_vm)  # type: ignore[type-var]
    builder.add_node("collect_plans", collect_plans_node)

    async def _enrich_plan(state: BatchGraphState) -> dict[str, Any]:
        return await enrich_plan_node(state, ssh_manager=ssh_manager)

    builder.add_node("enrich_plan", _enrich_plan)
    builder.add_node("generate_plan_artifact", generate_plan_artifact_node)
    builder.add_node("load_deferred_artifact", load_deferred_artifact_node)
    builder.add_node("approval_gate", _approval_gate)
    builder.add_node("verify_plan_hash", verify_plan_hash_node)
    builder.add_node("prepare_waves", prepare_waves_node)
    builder.add_node("dispatch_wave", lambda state: {})   # no-op — routing does the work
    builder.add_node("run_vm", _run_vm)
    builder.add_node("check_wave_health", _check_wave_health)
    async def _generate_report(state: BatchGraphState) -> dict[str, Any]:
        return await generate_report_node(state, batch_store=_batch_store)

    builder.add_node("collect_results", collect_results_node)
    builder.add_node("generate_report", _generate_report)

    # --- Edges ---
    builder.set_entry_point("init_batch")
    builder.add_edge("init_batch", "validate_window")
    builder.add_conditional_edges(
        "validate_window", route_after_window, ["validate_targets", "generate_report"],
    )
    # Fleet health gate (finding #7): abort if too many targets failed validation
    builder.add_edge("validate_targets", "check_fleet_health")
    builder.add_conditional_edges(
        "check_fleet_health",
        route_after_fleet_check,
        ["plan_vms", "load_deferred_artifact", "generate_report"],
    )
    builder.add_edge("load_deferred_artifact", "approval_gate")
    # Planning fan-out: one plan_vm per healthy target, then collect
    builder.add_node("plan_vms", lambda state: {})   # no-op entry point for _route_plan_vms
    builder.add_conditional_edges(
        "plan_vms", _route_plan_vms, ["plan_vm", "generate_report"],
    )
    builder.add_edge("plan_vm", "collect_plans")
    builder.add_edge("collect_plans", "enrich_plan")
    builder.add_edge("enrich_plan", "generate_plan_artifact")
    # Approval happens BEFORE execution (finding #3)
    builder.add_edge("generate_plan_artifact", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_approval, ["verify_plan_hash", "generate_report", END],
    )
    builder.add_conditional_edges(
        "verify_plan_hash", route_after_hash_verify, ["prepare_waves", "generate_report"],
    )
    # Execution: wave-based fan-out
    builder.add_conditional_edges(
        "prepare_waves", route_after_prepare_waves, ["dispatch_wave", "generate_report"],
    )
    builder.add_conditional_edges(
        "dispatch_wave", _dispatch_wave_fn, ["run_vm", "check_wave_health"],
    )
    builder.add_edge("run_vm", "check_wave_health")
    builder.add_conditional_edges(
        "check_wave_health", route_after_wave_health, ["dispatch_wave", "collect_results"],
    )
    builder.add_edge("collect_results", "generate_report")
    # Report is the terminal node — no post-execution approval (finding #3)
    builder.add_edge("generate_report", END)

    return builder
