"""Operator Assistant — Layer A investigation and recommendation engine.

Queries existing data stores (audit trail, disk history, drift baselines)
to build a fleet context, then calls the LLM to synthesize findings into
actionable recommendations.

Layer A contract:
  - Read-only: never writes to any store
  - No SandboxExecutor, no FileLocker, no ApprovalManager
  - No SSH connections — queries stores, not live VMs
  - LLM synthesizes; humans decide; Layer B executes

Fallback: when the LLM is unavailable, returns a deterministic summary
built directly from the FleetContext. The agent never blocks on LLM.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.models.analysis import AssistantResponse, FleetContext, VMSignalSummary

if TYPE_CHECKING:
    from errander.config.schema import InventoryConfig
    from errander.integrations.elk import ElkClient
    from errander.integrations.llm import LLMClient
    from errander.integrations.prometheus import PrometheusClient
    from errander.safety.audit import AuditStore
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore

logger = logging.getLogger(__name__)

_DRIFT_KINDS = ("sudoers", "authorized_keys", "listening_ports", "scheduled_jobs")
_DISK_WINDOW_DAYS = 7
_EVENTS_LIMIT = 100


class OperatorAssistant:
    """Layer A investigative assistant.

    Call investigate() with a natural-language question and existing store
    handles. Returns an AssistantResponse with summary, findings, and
    recommendations — never execution instructions.
    """

    async def investigate(
        self,
        question: str,
        *,
        audit_store: AuditStore,
        disk_history_store: VMDiskHistoryStore,
        baseline_store: BaselineStore,
        inventory: InventoryConfig,
        env_name: str | None = None,
        llm_client: LLMClient | None = None,
        prometheus_client: PrometheusClient | None = None,
        elk_client: ElkClient | None = None,
    ) -> AssistantResponse:
        """Build fleet context, call LLM, return structured findings.

        Falls back to deterministic summary if LLM is unavailable or fails.
        """
        context = await self._build_context(
            audit_store=audit_store,
            disk_history_store=disk_history_store,
            baseline_store=baseline_store,
            inventory=inventory,
            env_name=env_name,
            prometheus_client=prometheus_client,
            elk_client=elk_client,
        )

        if llm_client is not None:
            result = await llm_client.complete(
                _format_prompt(question, context),
                AssistantResponse,
            )
            if result is not None:
                return result
            logger.warning("LLM unavailable or response unparseable -- using fallback")

        return _fallback_response(question, context)

    async def _build_context(
        self,
        *,
        audit_store: AuditStore,
        disk_history_store: VMDiskHistoryStore,
        baseline_store: BaselineStore,
        inventory: InventoryConfig,
        env_name: str | None,
        prometheus_client: PrometheusClient | None = None,
        elk_client: ElkClient | None = None,
    ) -> FleetContext:
        """Query stores and assemble a FleetContext for the LLM prompt."""
        from errander.models.events import EventType
        from errander.safety.baselines import BaselineStore as _BaselineStore
        from errander.safety.disk_history import VMDiskHistoryStore as _DiskStore

        if env_name is not None:
            env = inventory.environments.get(env_name)
            targets = list(env.targets) if env else []
        else:
            targets = [t for e in inventory.environments.values() for t in e.targets]

        recent_batches = await audit_store.get_recent_batches(limit=5)
        last_batch_at: str | None = None
        if recent_batches:
            raw = recent_batches[0].get("started_at")
            last_batch_at = str(raw) if raw is not None else None

        vm_summaries: list[VMSignalSummary] = []
        total_failures = 0

        for target in targets:
            summary = VMSignalSummary(vm_id=target.name, hostname=target.host)

            # Recent action failures
            failures = await audit_store.get_events(
                vm_id=target.name,
                event_type=EventType.ACTION_FAILED,
                limit=_EVENTS_LIMIT,
            )
            summary.recent_failure_count = len(failures)
            total_failures += len(failures)

            # Last 3 action types started
            started = await audit_store.get_events(
                vm_id=target.name,
                event_type=EventType.ACTION_STARTED,
                limit=3,
            )
            summary.last_action_types = [
                str(e.action_type) for e in started if e.action_type
            ]

            # Disk growth trends
            if isinstance(disk_history_store, _DiskStore):
                mountpoints = await disk_history_store.get_distinct_mountpoints(target.name)
                for mp in mountpoints:
                    points = await disk_history_store.get_window(
                        target.name, mp, _DISK_WINDOW_DAYS
                    )
                    if len(points) >= 2:
                        start_pct = points[0].used_pct
                        end_pct = points[-1].used_pct
                        delta = end_pct - start_pct
                        if delta >= 5.0 or end_pct >= 80.0:
                            summary.disk_alerts.append(
                                f"{mp}: {start_pct:.0f}% -> {end_pct:.0f}%"
                                f" (+{delta:.0f}%) over {_DISK_WINDOW_DAYS}d"
                            )

            # Configuration drift — collect changed kinds from audit events
            if isinstance(baseline_store, _BaselineStore):
                drift_events = await audit_store.get_events(
                    vm_id=target.name,
                    event_type=EventType.DRIFT_KIND_CHANGED,
                    limit=20,
                )
                changed_kinds = {
                    str(e.metadata.get("kind", ""))
                    for e in drift_events
                    if e.metadata.get("kind")
                }
                summary.drift_kinds = sorted(changed_kinds)

            # Failed SSH logins
            login_events = await audit_store.get_events(
                vm_id=target.name,
                event_type=EventType.FAILED_SSH_LOGINS_OBSERVED,
                limit=5,
            )
            for event in login_events:
                count_raw = event.metadata.get("total_count", 0)
                summary.failed_login_count += int(str(count_raw))

            # Prometheus metrics — optional, best-effort
            if prometheus_client is not None:
                summary.prometheus_metrics = await prometheus_client.fetch_vm_metrics(target.host)

            # ELK error patterns — optional, best-effort (Tier 1 external observability)
            if elk_client is not None:
                summary.elk_errors = await elk_client.fetch_vm_errors(target.host)

            vm_summaries.append(summary)

        return FleetContext(
            env_name=env_name,
            vm_summaries=vm_summaries,
            recent_batch_count=len(recent_batches),
            last_batch_at=last_batch_at,
            total_failures_7d=total_failures,
        )


def _format_prompt(question: str, context: FleetContext) -> str:
    """Build the LLM investigation prompt from a question and fleet context."""
    lines: list[str] = [
        "You are Errander-AI's Operator Assistant (Layer A).",
        "You analyze fleet health data and produce structured findings.",
        "You NEVER suggest executing commands directly -- only what the human operator should consider.",
        "",
        "## Fleet context",
        f"Environment: {context.env_name or 'all environments'}",
        f"VMs surveyed: {len(context.vm_summaries)}",
        f"Recent maintenance batches: {context.recent_batch_count}",
        f"Last batch started: {context.last_batch_at or 'none recorded'}",
        f"Total action failures in data: {context.total_failures_7d}",
        "",
        "## Per-VM signals",
    ]

    for vm in context.vm_summaries:
        lines.append(f"\n### {vm.vm_id} ({vm.hostname})")
        lines.append(f"  Action failures: {vm.recent_failure_count}")
        lines.append(f"  Last actions run: {', '.join(vm.last_action_types) or 'none'}")
        if vm.disk_alerts:
            lines.append(f"  Disk growth: {'; '.join(vm.disk_alerts)}")
        if vm.drift_kinds:
            lines.append(f"  Config drift detected: {', '.join(vm.drift_kinds)}")
        if vm.failed_login_count > 0:
            lines.append(f"  Failed SSH logins: {vm.failed_login_count}")
        if vm.prometheus_metrics:
            lines.append(f"  Prometheus: {', '.join(vm.prometheus_metrics)}")
        if vm.elk_errors:
            lines.append(f"  ELK errors (24h): {'; '.join(vm.elk_errors[:3])}")

    lines += [
        "",
        "## Operator question",
        question,
        "",
        "Respond with valid JSON matching this schema exactly:",
        '{"summary": "<1-2 sentences>", "findings": ["<observation>", ...], '
        '"recommendations": ["<action for operator to consider>", ...], '
        '"risk_level": "low|medium|high|unknown"}',
    ]
    return "\n".join(lines)


def _fallback_response(question: str, context: FleetContext) -> AssistantResponse:
    """Deterministic summary when LLM is unavailable. Never blocks the CLI."""
    findings: list[str] = []
    recommendations: list[str] = []

    alarm_vms = [v for v in context.vm_summaries if v.recent_failure_count > 0]
    disk_vms = [v for v in context.vm_summaries if v.disk_alerts]
    drift_vms = [v for v in context.vm_summaries if v.drift_kinds]
    login_vms = [v for v in context.vm_summaries if v.failed_login_count > 0]
    elk_vms = [v for v in context.vm_summaries if v.elk_errors]

    if alarm_vms:
        findings.append(
            f"{len(alarm_vms)} VM(s) have recent action failures: "
            + ", ".join(v.vm_id for v in alarm_vms)
        )
        recommendations.append(
            "Review audit trail for failed VMs: --audit --vm-id <id>"
        )
    if disk_vms:
        for v in disk_vms:
            findings.append(
                f"{v.vm_id}: disk growth detected -- {'; '.join(v.disk_alerts)}"
            )
        recommendations.append(
            "Run a maintenance batch with disk_cleanup or review retention policies"
        )
    if drift_vms:
        for v in drift_vms:
            findings.append(
                f"{v.vm_id}: configuration drift in {', '.join(v.drift_kinds)}"
            )
        recommendations.append(
            "Run --probe-now to refresh drift baselines and review unified diffs"
        )
    if login_vms:
        findings.append(
            f"{len(login_vms)} VM(s) have failed SSH login attempts: "
            + ", ".join(v.vm_id for v in login_vms)
        )
        recommendations.append(
            "Review /var/log/auth.log on affected VMs; consider IP allowlisting"
        )

    if elk_vms:
        findings.append(
            f"{len(elk_vms)} VM(s) have recent ELK error events: "
            + ", ".join(v.vm_id for v in elk_vms)
        )
        recommendations.append(
            "Review ELK dashboard for error patterns before next maintenance batch"
        )

    if not findings:
        findings.append("No significant signals detected in available store data")
        recommendations.append(
            "Run --probe-now to collect fresh signal data before re-asking"
        )

    if alarm_vms or drift_vms:
        risk = "high"
    elif disk_vms or login_vms:
        risk = "medium"
    else:
        risk = "low"

    summary = (
        f"LLM unavailable -- deterministic summary: "
        f"{len(context.vm_summaries)} VM(s) surveyed, "
        f"{context.total_failures_7d} action failure(s), "
        f"{len(disk_vms)} disk alert(s), "
        f"{len(drift_vms)} drift change(s)."
    )
    return AssistantResponse(
        summary=summary,
        findings=findings,
        recommendations=recommendations,
        risk_level=risk,
    )
