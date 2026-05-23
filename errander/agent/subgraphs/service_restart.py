"""Service restart sub-graph — operator-triggered systemd unit restart.

Lifecycle:
1. Validate: Confirm unit is in the inventory allowlist; confirm wrapper responds to --check.
2. Snapshot: Capture pre-restart status and journal (read-only, no side effects).
3. Execute: Invoke wrapper with unit name; parse pre/post context from output.
4. Verify: Check post_active field for "active". Emit SERVICE_RESTART_VERIFY_FAILED
   on failure and return FAILED — no automatic re-restart attempt.

Risk tier: HIGH (always requires Slack approval regardless of policy).
Rollback strategy: No rollback — restarting again could make a bad situation worse.
  Verify failure emits SERVICE_RESTART_VERIFY_FAILED for human follow-up.

Privilege model: root-owned wrapper + sudoers entry (same pattern as docker_prune).
  Wrapper also enforces a target-side allowlist (/etc/errander/restart-allowlist).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

from errander.execution.command_builder import CommandBuildError, safe_systemd_unit_name
from errander.execution.privilege import privileged
from errander.models.actions import ActionStatus
from errander.models.manifest import ActionManifest
from errander.models.service_restart import RestartContext, ServiceRestartState

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor
    from errander.safety.audit import AuditStore

logger = logging.getLogger(__name__)

MANIFEST = ActionManifest(
    name="service_restart",
    default_enabled=False,
    risk_tier="HIGH",
    command_modes=None,
    required_binaries=("/bin/systemctl", "/bin/journalctl"),
    required_wrappers=("/usr/local/sbin/errander-systemctl-restart",),
    setup_doc="SETUP.md#optional-service-restart",
)

_WRAPPER = "/usr/local/sbin/errander-systemctl-restart"


# --- Parser ---

def parse_restart_output(stdout: str) -> RestartContext:
    """Parse the errander-systemctl-restart wrapper output.

    Handles both full restart output (5 sections) and --snapshot-only output
    (pre_status + pre_journal only). Missing sections default to empty strings.

    Expected format:
        pre_status_begin
        <systemctl status output>
        pre_status_end
        pre_journal_begin
        <journalctl output>
        pre_journal_end
        post_active_begin
        <systemctl is-active output>
        post_active_end
        post_status_begin
        <systemctl status output>
        post_status_end
        post_journal_begin
        <journalctl output>
        post_journal_end
    """
    begin_to_field: dict[str, str] = {
        "pre_status_begin": "pre_status",
        "pre_journal_begin": "pre_journal",
        "post_active_begin": "post_active",
        "post_status_begin": "post_status",
        "post_journal_begin": "post_journal",
    }
    end_markers: set[str] = {
        "pre_status_end", "pre_journal_end",
        "post_active_end", "post_status_end", "post_journal_end",
    }
    sections: dict[str, list[str]] = {
        "pre_status": [], "pre_journal": [],
        "post_active": [], "post_status": [], "post_journal": [],
    }
    current: str | None = None

    for raw in stdout.splitlines():
        line = raw.rstrip()
        if line in begin_to_field:
            current = begin_to_field[line]
        elif line in end_markers:
            current = None
        elif current is not None:
            sections[current].append(line)

    return RestartContext(
        pre_status="\n".join(sections["pre_status"]),
        pre_journal="\n".join(sections["pre_journal"]),
        post_active="\n".join(sections["post_active"]),
        post_status="\n".join(sections["post_status"]),
        post_journal="\n".join(sections["post_journal"]),
    )


# --- Node functions ---

async def validate_node(
    state: ServiceRestartState,
    *,
    executor: SandboxExecutor,
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    """Validate the restart request.

    Two checks:
    1. Inventory-side allowlist: unit_name must be in restartable_units.
    2. Wrapper liveness: sudo -n <wrapper> --check must exit 0.

    Emits SERVICE_RESTART_UNIT_NOT_ALLOWED when the allowlist check fails.
    """
    from errander.models.events import AuditEvent, EventType

    vm_id = state.get("vm_id", "unknown")
    unit_name = state.get("unit_name", "")
    restartable_units: list[str] = list(state.get("restartable_units") or [])

    # 1. Inventory allowlist check
    if unit_name not in restartable_units:
        detail = (
            f"Unit '{unit_name}' is not in restartable_units for {vm_id}. "
            f"Allowed: {restartable_units}. "
            "Add it to actions.service_restart.restartable_units in inventory.yaml."
        )
        logger.error("SERVICE_RESTART blocked on %s: %s", vm_id, detail)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.SERVICE_RESTART_UNIT_NOT_ALLOWED,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="service_restart",
                detail=detail,
            ), dry_run=False)
        return {"status": ActionStatus.FAILED.value, "error": detail}

    # 2. Wrapper liveness check
    target = _get_connection_params(state)
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged(f"{_WRAPPER} --check"),
        dry_run=False,
    )
    if not result.success or "ok" not in result.stdout:
        detail = (
            f"Wrapper {_WRAPPER} --check failed on {vm_id}. "
            "Install the wrapper — see SETUP.md#optional-service-restart."
        )
        logger.error("SERVICE_RESTART wrapper unavailable on %s", vm_id)
        return {"status": ActionStatus.FAILED.value, "error": detail}

    return {"status": ActionStatus.PENDING.value}


async def snapshot_node(
    state: ServiceRestartState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Capture pre-restart service status and recent journal lines.

    Read-only — does not execute restart. Uses --snapshot-only mode of wrapper.
    Runs even in dry_run mode (read-only SSH is acceptable).
    """
    vm_id = state.get("vm_id", "unknown")
    unit_name = state.get("unit_name", "")
    target = _get_connection_params(state)

    try:
        safe_systemd_unit_name(unit_name)
    except CommandBuildError as exc:
        logger.error("Unsafe unit_name rejected in snapshot_node on %s: %s", vm_id, exc)
        return {"pre_status": "", "pre_journal": ""}

    import shlex
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged(f"{_WRAPPER} --snapshot-only {shlex.quote(unit_name)}"),
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "Pre-restart snapshot failed for %s on %s: %s",
            unit_name, vm_id, result.stderr[:200],
        )
        return {"pre_status": "", "pre_journal": ""}

    ctx = parse_restart_output(result.stdout)
    return {"pre_status": ctx.pre_status, "pre_journal": ctx.pre_journal}


async def execute_node(
    state: ServiceRestartState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Execute the service restart via the wrapper script.

    Dry-run: returns DRY_RUN_OK without SSHing.
    Live: invokes wrapper with unit name, parses full pre/post context.
    """
    vm_id = state.get("vm_id", "unknown")
    unit_name = state.get("unit_name", "")
    dry_run = state.get("dry_run", True)
    target = _get_connection_params(state)

    try:
        safe_systemd_unit_name(unit_name)
    except CommandBuildError as exc:
        detail = f"Unsafe unit_name rejected in execute_node on {vm_id}: {exc}"
        logger.error("%s", detail)
        return {"status": ActionStatus.FAILED.value, "error": detail}

    import shlex
    if dry_run:
        logger.info("DRY RUN: would restart %s on %s", unit_name, vm_id)
        return {"status": ActionStatus.DRY_RUN_OK.value}

    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=privileged(f"{_WRAPPER} {shlex.quote(unit_name)}"),
        dry_run=False,
    )

    if not result.success:
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"Wrapper exited non-zero: {result.stderr[:200]}",
        }

    ctx = parse_restart_output(result.stdout)
    return {
        "status": ActionStatus.SUCCESS.value,
        "pre_status": ctx.pre_status,
        "pre_journal": ctx.pre_journal,
        "post_active": ctx.post_active,
        "post_status": ctx.post_status,
        "post_journal": ctx.post_journal,
    }


async def verify_node(
    state: ServiceRestartState,
    *,
    executor: SandboxExecutor,  # noqa: ARG001  # kept for consistent node signature
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    """Verify the service is active after restart.

    Reads post_active from state (captured by execute_node).
    Emits SERVICE_RESTART_VERIFY_FAILED on failure — no automatic re-restart.
    A failed restart is a paging event; humans take it from there.
    """
    from errander.models.events import AuditEvent, EventType

    if state.get("status") == ActionStatus.DRY_RUN_OK.value:
        return {}

    vm_id = state.get("vm_id", "unknown")
    unit_name = state.get("unit_name", "")
    post_active = state.get("post_active", "").strip()

    if "active" in post_active and "inactive" not in post_active:
        logger.info("Service %s is active on %s after restart", unit_name, vm_id)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.SERVICE_RESTART_VERIFY_OK,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="service_restart",
                detail=f"Unit '{unit_name}' is active after restart",
            ), dry_run=False)
        return {}

    detail = (
        f"Unit '{unit_name}' is NOT active after restart on {vm_id}. "
        f"Post-restart is-active output: {post_active!r}. "
        "Manual investigation required — no automatic re-restart."
    )
    logger.error("SERVICE_RESTART_VERIFY_FAILED for %s on %s", unit_name, vm_id)
    if audit_store is not None:
        await audit_store.log_event(AuditEvent(
            event_type=EventType.SERVICE_RESTART_VERIFY_FAILED,
            batch_id=batch_id,
            vm_id=vm_id,
            action_type="service_restart",
            detail=detail,
        ), dry_run=False)
    return {"status": ActionStatus.FAILED.value, "error": detail}


# --- Helpers ---

def _get_connection_params(state: ServiceRestartState) -> dict[str, str]:
    return {
        "hostname": str(state.get("hostname", "")),
        "username": str(state.get("username", "")),
        "key_path": str(state.get("key_path", "")),
    }


# --- Routing ---

def route_after_validate(state: ServiceRestartState) -> str:
    if state.get("status") == ActionStatus.FAILED.value:
        return END
    return "snapshot"


def route_after_snapshot(state: ServiceRestartState) -> str:
    if state.get("status") == ActionStatus.FAILED.value:
        return END
    return "execute"


def route_after_execute(state: ServiceRestartState) -> str:
    status = state.get("status")
    if status == ActionStatus.DRY_RUN_OK.value:
        return END
    if status == ActionStatus.FAILED.value:
        return END
    return "verify"


# --- Graph builder ---

def build_service_restart_subgraph(
    executor: SandboxExecutor,
    *,
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> StateGraph[ServiceRestartState]:
    """Construct the service restart sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.
        audit_store: Optional audit store for emitting restart events.
        batch_id: Batch identifier for audit events.

    Returns:
        StateGraph for service restart (call .compile() to use).
    """
    builder: StateGraph[ServiceRestartState] = StateGraph(ServiceRestartState)

    async def _validate(state: ServiceRestartState) -> dict[str, Any]:
        return await validate_node(
            state, executor=executor, audit_store=audit_store, batch_id=batch_id,
        )

    async def _snapshot(state: ServiceRestartState) -> dict[str, Any]:
        return await snapshot_node(state, executor=executor)

    async def _execute(state: ServiceRestartState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: ServiceRestartState) -> dict[str, Any]:
        return await verify_node(
            state, executor=executor, audit_store=audit_store, batch_id=batch_id,
        )

    builder.add_node("validate", _validate)
    builder.add_node("snapshot", _snapshot)
    builder.add_node("execute", _execute)
    builder.add_node("verify", _verify)

    builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["snapshot", END])
    builder.add_conditional_edges("snapshot", route_after_snapshot, ["execute", END])
    builder.add_conditional_edges("execute", route_after_execute, ["verify", END])
    builder.add_edge("verify", END)

    return builder
