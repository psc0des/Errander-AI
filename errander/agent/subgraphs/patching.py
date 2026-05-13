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
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.agent.subgraphs.disk_cleanup import get_package_manager_by_name
from errander.execution.reboot_check import detect_reboot_required
from errander.execution.sandbox import SandboxExecutor
from errander.execution.service_check import check_services, find_regressions
from errander.models.actions import ActionStatus
from errander.safety.validators import validate_no_pkg_lock

if TYPE_CHECKING:
    from errander.safety.audit import AuditStore
    from errander.safety.vm_state import VMStateStore

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

    # Pre-flight lock detection (1.1)
    lock_holder_pid: int | None   # PID of process holding the lock, or None
    lock_holder_cmd: str | None   # command name of lock holder, or None

    # Post-patch reboot detection (1.2)
    reboot_status_detected: bool  # True when reboot required after successful patch

    # Service health monitoring (1.3)
    critical_services: list[str]            # services to probe pre/post action
    service_pre_snapshot: dict[str, str]    # {name: state} before execute
    service_regressions: list[str]          # services that regressed post-execute


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
    # dry_run=False: assessment must always inspect real VM state.
    refresh = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.refresh_package_lists(),
        dry_run=False,
    )
    if not refresh.success:
        logger.warning(
            "Package list refresh failed on %s (continuing with cached lists): %s",
            vm_id, refresh.stderr[:200],
        )

    # List upgradable packages — always read real state.
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_upgradable(),
        dry_run=False,
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

    # dry_run=False: snapshot must read real installed versions even in dry-run mode.
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_installed_versions(pending),
        dry_run=False,
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

    dry_run = state.get("dry_run", True)
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.upgrade_all(exclude_patterns=exclude_patterns),
        simulate_command=pkg_mgr.simulate_upgrade(),
        dry_run=dry_run,
    )

    status = ActionStatus.DRY_RUN_OK if dry_run else ActionStatus.SUCCESS
    if not result.success and not dry_run:
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

    Sets status=FAILED on any verification failure so route_after_verify
    can route to rollback (blocker #5).
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

    # dry_run=False: always read real post-patch versions from the VM.
    result = await executor.execute(
        vm_id, target["hostname"], target["username"], target["key_path"],
        command=pkg_mgr.list_installed_versions(pending),
        dry_run=False,
    )

    if not result.success:
        logger.error("Post-patch version check SSH failed on %s — triggering rollback", vm_id)
        return {
            "status": ActionStatus.FAILED.value,
            "error": "Failed to verify package versions after patching",
        }

    current_versions = _parse_versions(result.stdout)

    # Compare with snapshot — if no packages changed, the upgrade likely failed silently.
    changed: dict[str, str] = {}
    for pkg, new_ver in current_versions.items():
        old_ver = snapshot.get(pkg, "unknown")
        if old_ver != new_ver:
            changed[pkg] = f"{old_ver} -> {new_ver}"

    if changed:
        logger.info("Updated %d packages on %s: %s", len(changed), vm_id, changed)
    else:
        logger.warning(
            "No version changes detected on %s after patching — upgrade may have silently failed",
            vm_id,
        )
        # Only treat as failure if we expected changes (had a non-empty snapshot)
        if snapshot:
            return {
                "status": ActionStatus.FAILED.value,
                "error": "Patching verification failed: no package versions changed after upgrade",
            }

    return {"updated_versions": current_versions}


async def preflight_lock_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    """Check for an active package manager lock before patching begins.

    If a lock is held: sets status=BLOCKED and records holder info in state.
    If clear: returns empty dict (existing validate → assess flow continues).
    Emits PREFLIGHT_LOCK_DETECTED / PREFLIGHT_LOCK_CLEAR audit events when
    audit_store is provided.
    """
    from errander.models.events import AuditEvent, EventType

    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)
    pkg_mgr = get_package_manager_by_name(os_family)

    is_clear, holder = await validate_no_pkg_lock(
        executor, vm_id,
        target["hostname"], target["username"], target["key_path"],
        pkg_mgr,
    )

    if is_clear:
        logger.debug("Package manager lock clear on %s", vm_id)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.PREFLIGHT_LOCK_CLEAR,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="patching",
                detail="No package manager lock detected",
            ), dry_run=state.get("dry_run", True))
        return {"lock_holder_pid": None, "lock_holder_cmd": None}

    holder_pid = holder.pid if holder else None
    holder_cmd = holder.cmd if holder else None
    detail = f"Package manager lock held by pid={holder_pid} cmd={holder_cmd}"
    logger.warning("PREFLIGHT BLOCKED patching on %s: %s", vm_id, detail)

    if audit_store is not None:
        await audit_store.log_event(AuditEvent(
            event_type=EventType.PREFLIGHT_LOCK_DETECTED,
            batch_id=batch_id,
            vm_id=vm_id,
            action_type="patching",
            detail=detail,
            metadata={"holder_pid": holder_pid, "holder_cmd": holder_cmd},
        ), dry_run=state.get("dry_run", True))

    return {
        "status": ActionStatus.BLOCKED.value,
        "lock_holder_pid": holder_pid,
        "lock_holder_cmd": holder_cmd,
        "error": detail,
    }


def route_after_preflight_lock(state: PatchingGraphState) -> str:
    """Route BLOCKED to END, otherwise continue to kernel-exclusion validate."""
    if state.get("status") == ActionStatus.BLOCKED.value:
        return END
    return "validate"


async def reboot_check_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
    vm_state_store: VMStateStore | None = None,
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    """Probe reboot-required status after a successful upgrade.

    Only runs when the upgrade succeeded — not after BLOCKED, FAILED, or
    DRY_RUN_OK.  Persists the flag to VMStateStore and emits
    REBOOT_REQUIRED_DETECTED when a reboot is needed.  No auto-reboot.
    """
    from errander.models.events import AuditEvent, EventType

    vm_id = state["vm_id"]
    os_family = state.get("os_family", "ubuntu")
    target = _get_connection_params(state)

    status = await detect_reboot_required(
        executor, vm_id,
        target["hostname"], target["username"], target["key_path"],
        os_family,
    )

    if not status.needs_reboot:
        logger.debug("No reboot required on %s after patching", vm_id)
        return {"reboot_status_detected": False}

    logger.info(
        "Reboot required on %s: %s (pkgs: %s)",
        vm_id, status.reason, status.pkgs_requiring,
    )

    if vm_state_store is not None:
        await vm_state_store.set_needs_reboot(
            vm_id,
            status.reason or "packages require reboot",
            status.pkgs_requiring,
        )

    if audit_store is not None:
        await audit_store.log_event(AuditEvent(
            event_type=EventType.REBOOT_REQUIRED_DETECTED,
            batch_id=batch_id,
            vm_id=vm_id,
            action_type="patching",
            detail=f"Reboot required: {status.reason}",
            metadata={
                "reason": status.reason,
                "pkgs_requiring": list(status.pkgs_requiring),
            },
        ), dry_run=state.get("dry_run", True))

    return {"reboot_status_detected": True}


async def service_health_pre_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Capture service states before the upgrade begins.

    Stores a snapshot of each critical service's state so
    service_health_post_node can detect regressions.
    No-op when critical_services is empty — returns immediately.
    """
    services = tuple(state.get("critical_services") or [])
    if not services:
        return {"service_pre_snapshot": {}}

    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    statuses = await check_services(
        executor, vm_id,
        target["hostname"], target["username"], target["key_path"],
        services,
    )
    snapshot = {name: s.state for name, s in statuses.items()}
    logger.debug("Pre-patch service snapshot on %s: %s", vm_id, snapshot)
    return {"service_pre_snapshot": snapshot}


async def service_health_post_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
    audit_store: AuditStore | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    """Check service states after the upgrade and emit regressions.

    Compares current states with service_pre_snapshot.  Any service that was
    active before but is no longer active is a SERVICE_HEALTH_REGRESSION.
    No-op when critical_services is empty or pre_snapshot is absent.
    """
    from errander.execution.service_check import ServiceStatus
    from errander.models.events import AuditEvent, EventType

    services = tuple(state.get("critical_services") or [])
    pre_raw = state.get("service_pre_snapshot") or {}

    if not services or not pre_raw:
        return {"service_regressions": []}

    vm_id = state["vm_id"]
    target = _get_connection_params(state)

    pre = {
        name: ServiceStatus(name=name, active=(st == "active"), state=st)
        for name, st in pre_raw.items()
    }

    post = await check_services(
        executor, vm_id,
        target["hostname"], target["username"], target["key_path"],
        services,
    )

    regressions = find_regressions(pre, post)

    if regressions:
        detail = f"Services down after patching: {', '.join(regressions)}"
        logger.warning("SERVICE_HEALTH_REGRESSION on %s: %s", vm_id, detail)
        if audit_store is not None:
            await audit_store.log_event(AuditEvent(
                event_type=EventType.SERVICE_HEALTH_REGRESSION,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="patching",
                detail=detail,
                metadata={
                    "regressed_services": regressions,
                    "pre_states": {n: s.state for n, s in pre.items()},
                    "post_states": {n: s.state for n, s in post.items()},
                },
            ), dry_run=state.get("dry_run", True))
    else:
        logger.debug("No service regressions on %s after patching", vm_id)

    return {"service_regressions": regressions}


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


async def rollback_node(
    state: PatchingGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Attempt to rollback a failed patching operation (finding #5).

    Uses the version_snapshot captured before upgrade to restore previous
    package versions via apt-get install --allow-downgrades.
    Always sets status=FAILED (the upgrade itself failed); error details
    include rollback outcome for the audit trail.
    """
    from errander.safety.rollback import rollback_action
    from errander.models.actions import ActionType as _AT

    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    snapshot = state.get("version_snapshot", {})

    success, detail = await rollback_action(
        _AT.PATCHING,
        vm_id,
        snapshot,
        executor=executor,
        hostname=target["hostname"],
        username=target["username"],
        key_path=target["key_path"],
        os_family=state.get("os_family", "ubuntu"),
    )

    if success:
        logger.info("Patching rollback succeeded for %s: %s", vm_id, detail)
    else:
        logger.error(
            "CRITICAL: Patching rollback FAILED for %s: %s — manual intervention required",
            vm_id, detail,
        )

    original_error = state.get("error") or "upgrade failed"
    rollback_outcome = "succeeded" if success else "FAILED — MANUAL INTERVENTION REQUIRED"
    return {
        "status": ActionStatus.FAILED.value,
        "error": f"{original_error} | rollback {rollback_outcome}: {detail}",
    }


def route_after_execute(state: PatchingGraphState) -> str:
    """Route after execution: verify (success), rollback (failure), or finish (dry-run)."""
    status = state.get("status")
    if status == ActionStatus.DRY_RUN_OK.value:
        return END
    if status == ActionStatus.FAILED.value:
        return "rollback"
    return "verify"


def route_after_verify(state: PatchingGraphState) -> str:
    """Route after verify: rollback if verification failed, otherwise done (blocker #5)."""
    if state.get("status") == ActionStatus.FAILED.value or state.get("error"):
        return "rollback"
    return END


# --- Graph builder ---

def build_patching_subgraph(
    executor: SandboxExecutor,
    *,
    audit_store: AuditStore | None = None,
    vm_state_store: VMStateStore | None = None,
    batch_id: str = "",
    sre_preflight_lock_check: bool = True,
    sre_reboot_check: bool = True,
    sre_service_check: bool = True,
) -> StateGraph:
    """Construct the patching sub-graph.

    Args:
        executor: SandboxExecutor for SSH command execution.
        audit_store: Optional audit store for emitting SRE signal events.
        vm_state_store: Optional VM state store for persisting needs_reboot.
        batch_id: Batch identifier for audit events.
        sre_preflight_lock_check: When True (default), check for pkg manager
            lock before patching and BLOCK if held.
        sre_reboot_check: When True (default), probe reboot-required status
            after a successful upgrade.
        sre_service_check: When True (default), snapshot critical_services
            states before and after the upgrade; emit SERVICE_HEALTH_REGRESSION
            on any regression.

    Returns:
        StateGraph for patching (call .compile() to use).
    """
    builder: StateGraph = StateGraph(PatchingGraphState)

    async def _preflight_lock(state: PatchingGraphState) -> dict[str, Any]:
        return await preflight_lock_node(
            state, executor=executor, audit_store=audit_store, batch_id=batch_id,
        )

    async def _assess(state: PatchingGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _snapshot(state: PatchingGraphState) -> dict[str, Any]:
        return await snapshot_node(state, executor=executor)

    async def _service_pre(state: PatchingGraphState) -> dict[str, Any]:
        return await service_health_pre_node(state, executor=executor)

    async def _execute(state: PatchingGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    async def _verify(state: PatchingGraphState) -> dict[str, Any]:
        return await verify_node(state, executor=executor)

    async def _rollback(state: PatchingGraphState) -> dict[str, Any]:
        return await rollback_node(state, executor=executor)

    async def _reboot_check(state: PatchingGraphState) -> dict[str, Any]:
        return await reboot_check_node(
            state, executor=executor,
            vm_state_store=vm_state_store, audit_store=audit_store, batch_id=batch_id,
        )

    async def _service_post(state: PatchingGraphState) -> dict[str, Any]:
        return await service_health_post_node(
            state, executor=executor, audit_store=audit_store, batch_id=batch_id,
        )

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("snapshot", _snapshot)
    builder.add_node("execute", _execute)
    builder.add_node("verify", _verify)
    builder.add_node("rollback", _rollback)

    if sre_preflight_lock_check:
        builder.add_node("preflight_lock", _preflight_lock)
        builder.set_entry_point("preflight_lock")
        builder.add_conditional_edges(
            "preflight_lock", route_after_preflight_lock, ["validate", END],
        )
    else:
        builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_conditional_edges("assess", route_after_assess, ["snapshot", END])

    if sre_service_check:
        builder.add_node("service_pre", _service_pre)
        builder.add_node("service_post", _service_post)
        builder.add_edge("snapshot", "service_pre")
        builder.add_edge("service_pre", "execute")
    else:
        builder.add_edge("snapshot", "execute")

    builder.add_conditional_edges("execute", route_after_execute, ["verify", "rollback", END])

    # Determine the first post-verify SRE node (failure always → rollback).
    if sre_reboot_check:
        builder.add_node("reboot_check", _reboot_check)

        def _route_verify(state: PatchingGraphState) -> str:
            if state.get("status") == ActionStatus.FAILED.value or state.get("error"):
                return "rollback"
            return "reboot_check"

        builder.add_conditional_edges("verify", _route_verify, ["rollback", "reboot_check"])

        if sre_service_check:
            builder.add_edge("reboot_check", "service_post")
        else:
            builder.add_edge("reboot_check", END)

    elif sre_service_check:
        def _route_verify_svc(state: PatchingGraphState) -> str:
            if state.get("status") == ActionStatus.FAILED.value or state.get("error"):
                return "rollback"
            return "service_post"

        builder.add_conditional_edges(
            "verify", _route_verify_svc, ["rollback", "service_post"],
        )

    else:
        builder.add_conditional_edges("verify", route_after_verify, ["rollback", END])

    if sre_service_check:
        builder.add_edge("service_post", END)

    builder.add_edge("rollback", END)

    return builder
