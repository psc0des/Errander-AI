"""Standalone daily probe runner — runs outside maintenance batches.

Calls the existing SRE signal node functions (disk_snapshot_node,
drift_baseline_node, failed_logins_node) directly as plain async functions.
No LangGraph StateGraph needed; the nodes are just async dict->dict functions.

Layer B: deterministic, no LLM, no MCP. Read-only on target VMs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from errander.agent.vm_graph import (
    discover_node,
    disk_snapshot_node,
    drift_baseline_node,
    failed_logins_node,
)
from errander.models.events import AuditEvent, EventType
from errander.models.reports import DigestReport, ProbeVMResult

if TYPE_CHECKING:
    from errander.config.settings import SRESignalSettings
    from errander.execution.sandbox import SandboxExecutor
    from errander.execution.ssh import SSHConnectionManager
    from errander.safety.audit import AuditStore

logger = logging.getLogger(__name__)


async def probe_vm(
    *,
    vm_id: str,
    hostname: str,
    ssh_user: str,
    ssh_key_path: str,
    os_family: str,
    disable_failed_login_check: bool = False,
    ssh_manager: SSHConnectionManager,
    executor: SandboxExecutor,
    disk_history_store: object,
    baseline_store: object,
    audit_store: AuditStore,
    sre_settings: SRESignalSettings,
) -> ProbeVMResult:
    """Run all signal probes against one VM. Read-only — never modifies state.

    Mirrors the vm_graph node ordering: discover first (SSH pre-check + vm_info),
    then signal probes. Returns reachable=False immediately if discover fails.
    """
    probe_state: dict[str, object] = {
        "vm_id": vm_id,
        "batch_id": "",
        "dry_run": False,
        "hostname": hostname,
        "ssh_user": ssh_user,
        "ssh_key_path": ssh_key_path,
        "os_family": os_family,
        "disable_failed_login_check": disable_failed_login_check,
        "disk_growth_alerts": [],
        "drift_changes": [],
        "failed_login_summary": None,
    }
    try:
        # Mirrors vm_graph: discover verifies SSH connectivity and populates vm_info.
        # Signal nodes prefer vm_info fields; fall back to state-level fields when absent.
        discover_result = await discover_node(
            probe_state,  # type: ignore[arg-type]
            ssh_manager=ssh_manager,
        )
        if discover_result.get("error"):
            return ProbeVMResult(
                vm_id=vm_id,
                hostname=hostname,
                reachable=False,
                error=str(discover_result["error"]),
            )
        probe_state.update(discover_result)

        disk_result = await disk_snapshot_node(
            probe_state,  # type: ignore[arg-type]
            executor=executor,
            disk_history_store=disk_history_store,
            audit_store=audit_store,
            settings=sre_settings.disk_growth_trend,
        )
        probe_state.update(disk_result)

        drift_result = await drift_baseline_node(
            probe_state,  # type: ignore[arg-type]
            executor=executor,
            baseline_store=baseline_store,
            audit_store=audit_store,
            settings=sre_settings.drift,
        )
        probe_state.update(drift_result)

        login_result = await failed_logins_node(
            probe_state,  # type: ignore[arg-type]
            executor=executor,
            audit_store=audit_store,
            settings=sre_settings.failed_ssh_logins,
        )
        probe_state.update(login_result)

        raw_disk = probe_state.get("disk_growth_alerts")
        raw_drift = probe_state.get("drift_changes")
        raw_login = probe_state.get("failed_login_summary")
        return ProbeVMResult(
            vm_id=vm_id,
            hostname=hostname,
            reachable=True,
            disk_growth_alerts=raw_disk if isinstance(raw_disk, list) else [],
            drift_changes=raw_drift if isinstance(raw_drift, list) else [],
            failed_login_summary=raw_login if isinstance(raw_login, dict) else None,
        )
    except Exception as exc:
        logger.warning("probe_vm failed for %s: %s", vm_id, exc)
        return ProbeVMResult(vm_id=vm_id, hostname=hostname, reachable=False, error=str(exc))


async def run_env_probe(
    *,
    env_name: str,
    vms: list[dict[str, object]],
    ssh_manager: SSHConnectionManager,
    executor: SandboxExecutor,
    disk_history_store: object,
    baseline_store: object,
    audit_store: AuditStore,
    sre_settings: SRESignalSettings,
) -> DigestReport:
    """Probe every VM in an environment concurrently and return a DigestReport."""
    probe_id = f"probe-{env_name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    await audit_store.log_event(AuditEvent(
        event_type=EventType.DAILY_PROBE_STARTED,
        batch_id=probe_id,
        vm_id="",
        action_type="probe",
        detail=f"Daily probe started: env={env_name}, vms={len(vms)}",
    ))

    tasks = [
        probe_vm(
            vm_id=str(vm["vm_id"]),
            hostname=str(vm["hostname"]),
            ssh_user=str(vm["ssh_user"]),
            ssh_key_path=str(vm["ssh_key_path"]),
            os_family=str(vm.get("os_family", "ubuntu")),
            disable_failed_login_check=bool(vm.get("disable_failed_login_check", False)),
            ssh_manager=ssh_manager,
            executor=executor,
            disk_history_store=disk_history_store,
            baseline_store=baseline_store,
            audit_store=audit_store,
            sre_settings=sre_settings,
        )
        for vm in vms
    ]
    results: list[ProbeVMResult] = list(await asyncio.gather(*tasks))

    report = DigestReport(
        probe_id=probe_id,
        env_name=env_name,
        generated_at=datetime.now(UTC),
        vm_results=results,
    )

    await audit_store.log_event(AuditEvent(
        event_type=EventType.DAILY_PROBE_COMPLETE,
        batch_id=probe_id,
        vm_id="",
        action_type="probe",
        detail=(
            f"Probe complete: {len(results)} VMs, "
            f"{report.reachable_count} reachable, "
            f"{len(report.all_disk_alerts)} disk alerts, "
            f"{len(report.all_drift_changes)} drift changes"
        ),
    ))
    return report
