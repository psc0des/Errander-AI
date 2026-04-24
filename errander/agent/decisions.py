"""LLM-powered decision logic with hardcoded fallbacks.

All decision functions follow the pattern:
1. Try LLM call with structured JSON output (Pydantic model)
2. On LLM failure/timeout: fall back to hardcoded default logic

Decision points:
- Action prioritization: Given system state, order actions by urgency
- Failure analysis: Given an error, determine if retry/rollback/escalate
- Report generation: Summarize batch results in human-readable format
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel

from errander.models.actions import (
    ACTION_RISK_TIERS,
    Action,
    ActionResult,
    ActionStatus,
    ActionType,
    RiskTier,
)
from errander.models.vm import VMInfo

if TYPE_CHECKING:
    from errander.integrations.llm import LLMClient

logger = logging.getLogger(__name__)

#: Default priority ordering — lowest risk first, then by operational value.
#: Disk cleanup and log rotation free space (enables other actions).
#: Docker prune is low risk. Patching is medium risk.
#: Backup verify is high risk (may restart services).
DEFAULT_PRIORITY: list[ActionType] = [
    ActionType.DISK_CLEANUP,
    ActionType.LOG_ROTATION,
    ActionType.DOCKER_PRUNE,
    ActionType.PATCHING,
    ActionType.BACKUP_VERIFY,
]


# --- Pydantic models for structured LLM responses ---

class _PrioritizedActions(BaseModel):
    """LLM response schema for action prioritization."""
    action_types: list[str]  # ordered list of ActionType values


class _FailureAnalysis(BaseModel):
    """LLM response schema for failure analysis."""
    recommendation: str  # "retry", "rollback", or "escalate"
    reason: str


class _Report(BaseModel):
    """LLM response schema for report generation."""
    report: str


# --- Helpers ---

def _is_action_applicable(action_type: ActionType, vm_info: VMInfo) -> bool:
    """Check if an action type is applicable given discovered VM state."""
    if action_type == ActionType.DOCKER_PRUNE:
        return vm_info.docker_available
    if action_type == ActionType.PATCHING:
        return vm_info.pending_packages > 0
    return True


def filter_applicable_actions(
    action_types: list[ActionType],
    vm_info: VMInfo,
) -> list[ActionType]:
    """Filter action types to only those applicable for a VM."""
    return [a for a in action_types if _is_action_applicable(a, vm_info)]


def _hardcoded_priority(
    available_actions: list[ActionType],
    vm_info: VMInfo,
) -> list[Action]:
    """Hardcoded fallback: filter by applicability, sort by DEFAULT_PRIORITY."""
    applicable = filter_applicable_actions(available_actions, vm_info)
    priority_index = {a: i for i, a in enumerate(DEFAULT_PRIORITY)}
    applicable.sort(key=lambda a: priority_index.get(a, len(DEFAULT_PRIORITY)))
    return [
        Action(
            action_type=a,
            risk_tier=ACTION_RISK_TIERS.get(a, RiskTier.MEDIUM),
        )
        for a in applicable
    ]


# --- Decision functions ---

async def prioritize_actions(
    vm_info: VMInfo,
    available_actions: list[ActionType] | None = None,
    llm_client: LLMClient | None = None,
) -> list[Action]:
    """Order maintenance actions by priority using LLM, with hardcoded fallback.

    Args:
        vm_info: Discovered system state from the VM.
        available_actions: Action types to consider. Defaults to all types.
        llm_client: Optional LLM client. If None, uses hardcoded fallback.

    Returns:
        Actions ordered by priority with risk tiers assigned.
    """
    if available_actions is None:
        available_actions = list(DEFAULT_PRIORITY)

    if llm_client is not None:
        prompt = _build_prioritize_prompt(vm_info, available_actions)
        result = await llm_client.complete(prompt, _PrioritizedActions)
        if result is not None:
            # Validate and convert LLM response
            try:
                ordered = _parse_action_types(result.action_types, available_actions)
                if ordered:
                    logger.info("LLM prioritization: %s", [a.value for a in ordered])
                    return [
                        Action(
                            action_type=a,
                            risk_tier=ACTION_RISK_TIERS.get(a, RiskTier.MEDIUM),
                        )
                        for a in ordered
                    ]
            except ValueError as exc:
                logger.warning("LLM returned invalid action types: %s — using fallback", exc)

        logger.info("LLM unavailable or returned invalid response — using hardcoded priority")

    return _hardcoded_priority(available_actions, vm_info)


async def analyze_failure(
    action_type: str,
    error: str,
    context: dict[str, object],
    llm_client: LLMClient | None = None,
) -> str:
    """Analyze an action failure and recommend next steps.

    Args:
        action_type: What action failed.
        error: The error message/output.
        context: Additional context (VM info, action params).
        llm_client: Optional LLM client.

    Returns:
        Recommendation: 'retry', 'rollback', or 'escalate'.
    """
    if llm_client is not None:
        prompt = _build_failure_prompt(action_type, error, context)
        result = await llm_client.complete(prompt, _FailureAnalysis)
        if result is not None:
            rec = result.recommendation.lower().strip()
            if rec in ("retry", "rollback", "escalate"):
                logger.info("LLM failure analysis: %s (reason: %s)", rec, result.reason)
                return rec
            logger.warning("LLM returned unknown recommendation '%s' — using fallback", rec)

    # Hardcoded fallback heuristics
    error_lower = error.lower()
    if any(term in error_lower for term in ("timeout", "connection", "temporary")):
        return "retry"
    if action_type == ActionType.PATCHING and any(
        term in error_lower for term in ("dpkg", "broken", "conflict", "held")
    ):
        return "rollback"
    return "escalate"


async def generate_report(
    results: list[ActionResult],
    batch_id: str = "",
    llm_client: LLMClient | None = None,
) -> str:
    """Generate a human-readable report from batch results.

    Uses /no_think mode for fast, structured output.
    Falls back to template-based report on LLM failure.

    Args:
        results: Aggregated action results from all VMs.
        batch_id: Batch run identifier.
        llm_client: Optional LLM client.

    Returns:
        Formatted report string for Slack posting.
    """
    if llm_client is not None:
        prompt = _build_report_prompt(results, batch_id)
        result = await llm_client.complete(prompt, _Report)
        if result is not None and result.report.strip():
            logger.info("LLM-generated report for batch %s", batch_id)
            return result.report

    return _template_report(results, batch_id)


# --- Prompt builders ---

def _build_prioritize_prompt(
    vm_info: VMInfo,
    available_actions: list[ActionType],
) -> str:
    applicable = filter_applicable_actions(available_actions, vm_info)
    return (
        f"Prioritize these maintenance actions for a VM with the following state:\n"
        f"- OS: {vm_info.os_family.value} {vm_info.os_version}\n"
        f"- Disk usage: {vm_info.disk_usage}\n"
        f"- Docker available: {vm_info.docker_available}\n"
        f"- Pending packages: {vm_info.pending_packages}\n"
        f"- Uptime seconds: {vm_info.uptime_seconds}\n\n"
        f"Available actions: {[a.value for a in applicable]}\n\n"
        f"Order them from highest to lowest urgency.\n"
        f"Respond with JSON: {{\"action_types\": [\"<action1>\", \"<action2>\", ...]}}"
    )


def _build_failure_prompt(
    action_type: str,
    error: str,
    context: dict[str, object],
) -> str:
    return (
        f"A maintenance action failed. Analyze and recommend next steps.\n\n"
        f"Action: {action_type}\n"
        f"Error: {error}\n"
        f"Context: {context}\n\n"
        f"Choose one: retry (transient error), rollback (state corrupted), "
        f"escalate (needs human).\n"
        f"Respond with JSON: {{\"recommendation\": \"<retry|rollback|escalate>\", "
        f"\"reason\": \"<brief reason>\"}}"
    )


def _build_report_prompt(results: list[ActionResult], batch_id: str) -> str:
    summary = [
        {
            "vm_id": r.vm_id,
            "action": r.action_type.value,
            "status": r.status.value,
            "detail": r.detail,
            "error": r.error,
        }
        for r in results
    ]
    return (
        f"Generate a concise maintenance report for Slack.\n"
        f"Batch ID: {batch_id}\n"
        f"Results: {summary}\n\n"
        f"Respond with JSON: {{\"report\": \"<report text>\"}}\n"
        f"Keep the report under 1500 characters. Use markdown formatting (headers, bullet points)."
    )


def _parse_action_types(
    raw: list[str],
    available: list[ActionType],
) -> list[ActionType]:
    """Parse and validate LLM-returned action type strings."""
    available_set = set(available)
    parsed: list[ActionType] = []
    for item in raw:
        try:
            action = ActionType(item)
            if action in available_set:
                parsed.append(action)
        except ValueError:
            logger.warning("LLM returned unknown action type: %s", item)
    return parsed


# --- Template report (fallback) ---

def _template_report(results: list[ActionResult], batch_id: str) -> str:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_vm: dict[str, list[ActionResult]] = {}
    for r in results:
        by_vm.setdefault(r.vm_id, []).append(r)

    total = len(results)
    succeeded = sum(1 for r in results if r.status == ActionStatus.SUCCESS)
    dry_run_ok = sum(1 for r in results if r.status == ActionStatus.DRY_RUN_OK)
    failed = sum(1 for r in results if r.status == ActionStatus.FAILED)
    skipped = sum(1 for r in results if r.status == ActionStatus.SKIPPED)

    lines: list[str] = [
        f"# Errander-AI Report — {batch_id or 'unknown'}",
        f"Generated: {now}",
        "",
        "## Summary",
        f"- Total actions: {total}",
        f"- Succeeded: {succeeded}",
        f"- Dry-run OK: {dry_run_ok}",
        f"- Failed: {failed}",
        f"- Skipped: {skipped}",
        f"- VMs processed: {len(by_vm)}",
        "",
    ]

    for vm_id, vm_results in sorted(by_vm.items()):
        lines.append(f"## {vm_id}")
        for r in vm_results:
            icon = _status_icon(r.status)
            line = f"  {icon} {r.action_type}: {r.status.value}"
            if r.detail:
                line += f" — {r.detail}"
            if r.error:
                line += f" [ERROR: {r.error}]"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


def _status_icon(status: ActionStatus) -> str:
    return {
        ActionStatus.SUCCESS: "[OK]",
        ActionStatus.DRY_RUN_OK: "[DRY]",
        ActionStatus.FAILED: "[FAIL]",
        ActionStatus.SKIPPED: "[SKIP]",
        ActionStatus.ROLLED_BACK: "[ROLL]",
        ActionStatus.ROLLBACK_FAILED: "[RFAIL]",
        ActionStatus.NEEDS_MANUAL: "[MANUAL]",
        ActionStatus.PENDING: "[PEND]",
    }.get(status, "[?]")
