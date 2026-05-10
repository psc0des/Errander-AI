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

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from errander.agent.decisions import generate_report, prioritize_actions
from errander.agent.vm_graph import VMGraphState, build_vm_graph
from errander.execution.os_detection import detect_os
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.integrations.slack import SlackClient
from errander.models.actions import ACTION_RISK_TIERS, ActionStatus, ActionType, RiskTier
from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import BATCH_DURATION
from errander.safety.approval import ApprovalManager, await_dual_approval
from errander.safety.audit import AuditStore
from errander.safety.deferred import DeferredExecutionStore
from errander.safety.locking import FileLocker
from errander.scheduling.windows import (
    MaintenanceWindow,
    check_window_from_config,
    next_window_open,
)

logger = logging.getLogger(__name__)


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

    # Plan artifact (set by generate_plan_artifact_node)
    plan_id: str
    plan_hash: str

    # Aggregated results from all VM graphs (append-only via reducer)
    vm_results: Annotated[list[dict[str, object]], _merge_vm_results]

    # Final report
    report: str
    error: str | None

    # Approval (set by approval_gate_node, gates wave dispatch)
    approved: bool | None

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


# --- Node functions ---

async def init_batch_node(
    state: BatchGraphState,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Generate a unique batch ID and inject rolling/canary/drift settings."""
    from errander.config.settings import Settings as _Settings
    _s: _Settings = settings if settings is not None else _Settings()

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    logger.info("Starting batch %s (dry_run=%s)", batch_id, state.get("dry_run", True))

    return {
        "batch_id": batch_id,
        "batch_started_at": datetime.now(tz=timezone.utc).isoformat(),
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

    now = datetime.now(tz=timezone.utc)
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
    """SSH-verify each target and partition into healthy/failed.

    A target is healthy if SSH connects and returns exit_code 0 for
    a simple connectivity test (echo ok).
    """
    batch_id = state.get("batch_id", "unknown")
    targets = state.get("targets", [])
    healthy: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    for t in targets:
        vm_id = str(t["vm_id"])
        hostname = str(t["hostname"])
        ssh_user = str(t["ssh_user"])
        key_path = str(t["ssh_key_path"])

        try:
            result: SSHResult = await ssh_manager.execute(
                vm_id, hostname, ssh_user, key_path, "echo ok",
            )
            if result.success:
                healthy.append(t)
                logger.info("Target %s validated OK", vm_id)
            else:
                failed.append(t)
                logger.warning("Target %s failed connectivity check", vm_id)
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.error("Target %s unreachable: %s", vm_id, exc)
            failed.append(t)
            await audit_store.log_event(
                AuditEvent(
                    event_type=EventType.ACTION_FAILED,
                    batch_id=batch_id,
                    vm_id=vm_id,
                    detail=f"Target validation failed: {exc}",
                    timestamp=datetime.now(tz=timezone.utc),
                )
            )

    return {
        "healthy_targets": healthy,
        "failed_targets": failed,
    }


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
        return {"vm_results": final.get("results", [])}
    except Exception as exc:  # noqa: BLE001
        logger.exception("VM graph crashed for %s", state.get("vm_id"))
        return {"vm_results": [{
            "action_type": "unknown",
            "status": ActionStatus.FAILED.value,
            "vm_id": state.get("vm_id", "unknown"),
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "detail": "vm graph raised exception",
            "error": str(exc),
        }]}


async def collect_results_node(state: BatchGraphState) -> dict[str, Any]:
    """Collect and log aggregated results. (Passthrough — reducer handles merge.)"""
    vm_results = state.get("vm_results", [])
    batch_id = state.get("batch_id", "unknown")
    logger.info(
        "Batch %s collected %d action results across all VMs",
        batch_id, len(vm_results),
    )
    return {}


async def generate_report_node(state: BatchGraphState) -> dict[str, Any]:
    """Generate a human-readable report from aggregated results."""
    from errander.models.actions import ActionResult, ActionStatus, ActionType

    raw_results = state.get("vm_results", [])
    batch_id = state.get("batch_id", "")

    # Deserialise raw dicts back to ActionResult objects
    action_results: list[ActionResult] = []
    for r in raw_results:
        try:
            action_results.append(
                ActionResult(
                    action_type=ActionType(str(r.get("action_type", "disk_cleanup"))),
                    status=ActionStatus(str(r.get("status", "failed"))),
                    vm_id=str(r.get("vm_id", "unknown")),
                    started_at=datetime.fromisoformat(str(r["started_at"])),
                    completed_at=datetime.fromisoformat(str(r["completed_at"]))
                    if r.get("completed_at") else None,
                    detail=str(r.get("detail", "")),
                    error=str(r["error"]) if r.get("error") else None,
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Could not deserialise result %s: %s", r, exc)

    report = await generate_report(action_results, batch_id=batch_id)

    # Record batch duration
    started_at_str = state.get("batch_started_at")
    if started_at_str and isinstance(started_at_str, str):
        try:
            started_at = datetime.fromisoformat(started_at_str)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            batch_duration = (datetime.now(tz=timezone.utc) - started_at).total_seconds()
            BATCH_DURATION.observe(batch_duration)
        except (ValueError, TypeError):
            pass  # Skip metric if timestamp is unparseable

    return {"report": report}


# --- Planning phase nodes ---

async def plan_vm_node(
    state: dict[str, Any],
    *,
    ssh_manager: SSHConnectionManager,
) -> dict[str, Any]:
    """Plan actions for a single VM without any execution.

    Runs OS detection + action prioritization via SSH (read-only), then
    returns the planned actions for inclusion in the ImmutableBatchPlan.
    No package upgrades, no file changes — purely informational SSH calls.
    """
    vm_id = str(state.get("vm_id", ""))
    hostname = str(state.get("hostname", ""))
    ssh_user = str(state.get("ssh_user", ""))
    key_path = str(state.get("ssh_key_path", ""))

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

    actions = await prioritize_actions(vm_info)

    return {
        "vm_plans": [{
            "vm_id": vm_id,
            "planned_actions": [
                {"action_type": a.action_type.value, "risk_tier": a.risk_tier.value}
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


async def generate_plan_artifact_node(state: BatchGraphState) -> dict[str, Any]:
    """Build ImmutableBatchPlan from collected VM plans and compute hash.

    The plan_hash is stored in state and included in the Slack approval
    request. Live execution validates hash stability (finding #3).
    """
    import hashlib
    import json
    import uuid

    vm_plans = state.get("vm_plans", [])
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
        for action in plan.get("planned_actions", []):
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
) -> str:
    """Format an ImmutableBatchPlan for the Slack approval message."""
    lines = [
        f"Batch `{batch_id}` — Live Execution Plan",
        f"Plan: {plan_id} | Hash: `{plan_hash[:12]}`",
        f"{len(vm_plans)} VM(s):",
    ]
    for plan in vm_plans:
        vm_id = plan.get("vm_id", "?")
        actions = [a.get("action_type", "?") for a in plan.get("planned_actions", [])]
        lines.append(f"  • `{vm_id}`: {', '.join(actions) or 'no actions planned'}")
    lines.extend(["", "Reply :white_check_mark: to approve or :x: to reject (timeout -> auto-REJECT)"])
    return "\n".join(lines)


async def approval_gate_node(
    state: BatchGraphState,
    *,
    approval_manager: ApprovalManager | None = None,
    slack_client: SlackClient | None = None,
    audit_store: AuditStore | None = None,
    window: MaintenanceWindow | None = None,
    deferred_store: DeferredExecutionStore | None = None,
) -> dict[str, Any]:
    """Gate live execution behind Slack approval of the ImmutableBatchPlan.

    Approval now happens BEFORE execution (finding #3):
    - Dry-run: auto-approve — sandbox execution is always safe.
    - Live HIGH/CRITICAL: require Slack dual-approval on the plan.
    - Live LOW/MEDIUM: auto-approve (policy enforcement is Phase 2).

    For live batches approved while outside the maintenance window,
    execution is deferred: a record is saved to DeferredExecutionStore
    and the window-opener scheduler job picks it up at window start.
    Dry-run batches always execute immediately regardless of window.
    """
    vm_plans = state.get("vm_plans", [])
    batch_id = state.get("batch_id", "unknown")
    env_name = state.get("env_name", "unknown")
    plan_id = state.get("plan_id", "unknown")
    plan_hash = state.get("plan_hash", "")
    dry_run = state.get("dry_run", True)

    max_tier = _max_risk_tier_from_plans(vm_plans)

    if dry_run:
        # Sandbox execution is safe — no operator approval needed.
        # Graduating dry-run → live is a separate operator-initiated action (finding #4).
        approved = True
        approver = None
        logger.info(
            "Batch %s dry-run — proceeding to sandbox execution (max risk tier: %s)",
            batch_id, max_tier.value,
        )
    elif max_tier in (RiskTier.HIGH, RiskTier.CRITICAL) and approval_manager is not None:
        plan_summary = _format_plan_for_approval(vm_plans, batch_id, plan_id, plan_hash)
        logger.info(
            "Batch %s requires live approval before execution (max risk tier: %s)",
            batch_id, max_tier.value,
        )
        approved, approver = await await_dual_approval(
            approval_manager, slack_client, batch_id, plan_summary,
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
            "Batch %s live auto-approved (max risk tier: %s)",
            batch_id, max_tier.value,
        )

    # Live approved outside window → defer execution to next window start.
    # Dry-run always runs immediately (sandbox is window-agnostic).
    if approved and not dry_run and window is not None:
        now = datetime.now(tz=timezone.utc)
        if not check_window_from_config(now, window):
            next_open = next_window_open(now, window)
            if deferred_store is not None:
                await deferred_store.save(
                    batch_id=batch_id,
                    env_name=env_name,
                    approved_by=approver,
                    window_start=next_open,
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
    """Block if window check sets an error (future use)."""
    if state.get("error"):
        return "generate_report"
    return "validate_targets"


def route_after_approval(state: BatchGraphState) -> str:
    """Route after plan approval: execute (approved), report (rejected), end (deferred)."""
    if state.get("deferred"):
        return END
    if not state.get("approved"):
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
                    locked=False,
                    results=[],
                    current_action_index=0,
                    planned_actions=[],
                    error=None,
                    drift_detection_enabled=state.get("drift_detection_enabled", False),
                    drift_abort_on_detection=state.get("drift_abort_on_detection", False),
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
) -> tuple[Any, Any]:
    """Build the wave dispatch routing function.

    Returns (dispatch_fn, vm_compiled). The dispatch_fn emits
    Send() for only the current wave's targets.
    """
    vm_compiled = build_vm_graph(executor, locker, audit_store, ssh_manager).compile()

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
                    locked=False,
                    results=[],
                    current_action_index=0,
                    planned_actions=[],
                    error=None,
                    drift_detection_enabled=state.get("drift_detection_enabled", False),
                    drift_abort_on_detection=state.get("drift_abort_on_detection", False),
                ),
            )
            for t in wave_targets
        ]

    return dispatch_current_wave, vm_compiled


def route_after_validate(state: BatchGraphState) -> str:
    """Standalone routing function used in tests (no Send, no dependencies)."""
    if not state.get("healthy_targets"):
        return "generate_report"
    return "fan_out"  # only used in unit test context


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
) -> StateGraph:
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

    builder: StateGraph = StateGraph(BatchGraphState)

    # --- Closures capturing injected dependencies ---

    async def _init_batch(state: BatchGraphState) -> dict[str, Any]:
        return await init_batch_node(state, settings=_settings)

    async def _validate_window(state: BatchGraphState) -> dict[str, Any]:
        return await validate_window_node(state, window=window)

    async def _validate_targets(state: BatchGraphState) -> dict[str, Any]:
        return await validate_targets_node(
            state, ssh_manager=ssh_manager, audit_store=audit_store,
        )

    async def _plan_vm(state: dict[str, Any]) -> dict[str, Any]:
        return await plan_vm_node(state, ssh_manager=ssh_manager)

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
        )

    _dispatch_wave_fn, vm_compiled = make_wave_dispatcher(
        executor, locker, audit_store, ssh_manager,
    )

    async def _run_vm(state: VMGraphState) -> dict[str, Any]:
        return await run_vm_node(state, vm_compiled=vm_compiled)

    # Fan-out router: Send one plan_vm invocation per healthy target
    def _route_plan_vms(state: BatchGraphState) -> str | list[Send]:
        healthy = state.get("healthy_targets", [])
        if not healthy:
            return "generate_report"
        batch_id = state.get("batch_id", "unknown")
        return [
            Send("plan_vm", {
                "vm_id": str(t["vm_id"]),
                "hostname": str(t["hostname"]),
                "ssh_user": str(t["ssh_user"]),
                "ssh_key_path": str(t["ssh_key_path"]),
                "batch_id": batch_id,
            })
            for t in healthy
        ]

    # --- Nodes ---
    builder.add_node("init_batch", _init_batch)
    builder.add_node("validate_window", _validate_window)
    builder.add_node("validate_targets", _validate_targets)
    builder.add_node("plan_vm", _plan_vm)
    builder.add_node("collect_plans", collect_plans_node)
    builder.add_node("generate_plan_artifact", generate_plan_artifact_node)
    builder.add_node("approval_gate", _approval_gate)
    builder.add_node("prepare_waves", prepare_waves_node)
    builder.add_node("dispatch_wave", lambda state: {})   # no-op — routing does the work
    builder.add_node("run_vm", _run_vm)
    builder.add_node("check_wave_health", _check_wave_health)
    builder.add_node("collect_results", collect_results_node)
    builder.add_node("generate_report", generate_report_node)

    # --- Edges ---
    builder.set_entry_point("init_batch")
    builder.add_edge("init_batch", "validate_window")
    builder.add_conditional_edges(
        "validate_window", route_after_window, ["validate_targets", "generate_report"],
    )
    # Planning fan-out: one plan_vm per healthy target, then collect
    builder.add_conditional_edges(
        "validate_targets", _route_plan_vms, ["plan_vm", "generate_report"],
    )
    builder.add_edge("plan_vm", "collect_plans")
    builder.add_edge("collect_plans", "generate_plan_artifact")
    # Approval happens BEFORE execution (finding #3)
    builder.add_edge("generate_plan_artifact", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_approval, ["prepare_waves", "generate_report", END],
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
