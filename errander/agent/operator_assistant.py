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
from errander.safety.context_budget import ContextBudgeter
from errander.safety.context_redactor import ContextRedactor

if TYPE_CHECKING:
    from errander.config.schema import InventoryConfig
    from errander.integrations.elk import ElkClient
    from errander.integrations.llm import LLMClient
    from errander.integrations.prometheus import PrometheusClient
    from errander.safety.audit import AuditStore
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore
    from errander.safety.vm_facts import (
        ActionOutcomeFact,
        ActionRejectionFact,
        VMFactsStore,
        VMRebootPatternFact,
    )

logger = logging.getLogger(__name__)

_DRIFT_KINDS = ("sudoers", "authorized_keys", "listening_ports", "scheduled_jobs")
_BUDGETER = ContextBudgeter()
_REDACTOR = ContextRedactor()
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
        vm_facts_store: VMFactsStore | None = None,
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
            vm_facts_store=vm_facts_store,
        )

        context, budget_stats = _BUDGETER.apply(context)
        if budget_stats.vms_dropped:
            logger.info(
                "Context budget: dropped %d VM(s) from prompt (%d included)",
                budget_stats.vms_dropped,
                budget_stats.vms_included,
            )

        if llm_client is not None:
            prompt = _format_prompt(question, context)
            prompt, redaction_stats = _REDACTOR.redact_prompt(prompt)
            if redaction_stats.total_redactions:
                logger.warning(
                    "Redacted %d secret pattern(s) from LLM prompt",
                    redaction_stats.total_redactions,
                )
            result = await llm_client.complete(prompt, AssistantResponse)
            if result is not None:
                result.data_sources = context.sources_used
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
        vm_facts_store: VMFactsStore | None = None,
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

        sources: list[str] = ["audit_store"]
        if any(v.disk_alerts for v in vm_summaries):
            sources.append("disk_history")
        if any(v.drift_kinds for v in vm_summaries):
            sources.append("drift_baselines")
        if prometheus_client is not None:
            if any(v.prometheus_metrics for v in vm_summaries):
                sources.append(f"prometheus({prometheus_client._base_url})")
            else:
                sources.append("prometheus(no_data)")
        if elk_client is not None:
            if any(v.elk_errors for v in vm_summaries):
                sources.append(f"elk({elk_client._base_url})")
            else:
                sources.append("elk(no_data)")
        if any(v.journal_errors or v.failed_services for v in vm_summaries):
            sources.append("live_ssh_probe")

        action_outcomes: list[ActionOutcomeFact] = []
        reboot_patterns: list[VMRebootPatternFact] = []
        rejection_facts: list[ActionRejectionFact] = []
        if vm_facts_store is not None:
            try:
                for target in targets:
                    outcomes = await vm_facts_store.action_outcomes(target.name)
                    action_outcomes.extend(outcomes)
                    rp = await vm_facts_store.reboot_pattern(target.name)
                    if rp is not None:
                        reboot_patterns.append(rp)
                rejection_facts = await vm_facts_store.rejection_facts()
                sources.append("vm_facts")
            except Exception as exc:  # noqa: BLE001
                logger.warning("VMFactsStore query failed: %s", exc)

        return FleetContext(
            env_name=env_name,
            vm_summaries=vm_summaries,
            recent_batch_count=len(recent_batches),
            last_batch_at=last_batch_at,
            total_failures_7d=total_failures,
            sources_used=sources,
            action_outcomes=action_outcomes,
            reboot_patterns=reboot_patterns,
            frequently_rejected_actions=rejection_facts,
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
        if vm.failed_services:
            lines.append(f"  Failed services: {', '.join(vm.failed_services)}")
        if vm.journal_errors and not vm.elk_errors:
            lines.append(f"  Journal errors: {'; '.join(vm.journal_errors[:3])}")

    if context.action_outcomes or context.reboot_patterns or context.frequently_rejected_actions:
        lines += ["", "## Operational history facts"]

        if context.action_outcomes:
            lines.append("Action outcomes (last 20 attempts per VM/action):")
            for fact in context.action_outcomes:
                pct = f"{fact.success_rate * 100:.0f}%"
                base = (
                    f"  {fact.vm_id} {fact.action_type}: {pct} success"
                    f" ({fact.sample_size} samples, confidence: {fact.confidence})"
                )
                if fact.last_failure_reason:
                    base += f" — last failure: {fact.last_failure_reason[:80]}"
                lines.append(base)

        if context.reboot_patterns:
            lines.append("Reboot patterns after patching:")
            for rp in context.reboot_patterns:
                lines.append(
                    f"  {rp.vm_id}: {rp.reboots_required_after_patching} reboots required"
                    f" ({rp.sample_size} patching runs, confidence: {rp.confidence})"
                )

        if context.frequently_rejected_actions:
            lines.append("Frequently rejected actions (last 90 days):")
            for rf in context.frequently_rejected_actions:
                reasons = "; ".join(rf.rejection_reasons[:3])
                lines.append(
                    f"  {rf.action_type}: {rf.rejections_last_90d} rejection(s)"
                    f" [confidence: {rf.confidence}]"
                    + (f" — {reasons[:120]}" if reasons else "")
                )

    if context.sources_used:
        lines += ["", "## Data sources consulted", ", ".join(context.sources_used)]

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

    service_fail_vms = [v for v in context.vm_summaries if v.failed_services]
    if service_fail_vms:
        findings.append(
            f"{len(service_fail_vms)} VM(s) have failed systemd services: "
            + ", ".join(v.vm_id for v in service_fail_vms)
        )
        recommendations.append(
            "Run --probe-now --live to capture current service state; "
            "investigate with: systemctl status <unit>"
        )

    low_success_rate_facts = [
        f for f in context.action_outcomes if f.success_rate < 0.8 and f.sample_size >= 3
    ]
    if low_success_rate_facts:
        for f in low_success_rate_facts:
            findings.append(
                f"{f.vm_id} {f.action_type}: {f.success_rate * 100:.0f}% success rate"
                + (f" — last failure: {f.last_failure_reason}" if f.last_failure_reason else "")
            )
        recommendations.append(
            "Review audit trail for VMs with low action success rates"
        )

    frequently_rejected = [
        f for f in context.frequently_rejected_actions if f.rejections_last_90d >= 2
    ]
    if frequently_rejected:
        for rf in frequently_rejected:
            findings.append(
                f"{rf.action_type} has been rejected {rf.rejections_last_90d} time(s) in 90 days"
            )
        recommendations.append(
            "Investigate why actions are being repeatedly rejected before scheduling"
        )

    if not findings:
        findings.append("No significant signals detected in available store data")
        recommendations.append(
            "Run --probe-now to collect fresh signal data before re-asking"
        )

    if alarm_vms or drift_vms or low_success_rate_facts:
        risk = "high"
    elif disk_vms or login_vms or frequently_rejected:
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
        data_sources=context.sources_used,
    )
