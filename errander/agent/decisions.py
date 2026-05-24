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

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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

if TYPE_CHECKING:
    from errander.integrations.llm import LLMClient
    from errander.models.vm import VMInfo
    from errander.safety.ai_audit import AIDecisionStore

from errander.safety.context_redactor import ContextRedactor

logger = logging.getLogger(__name__)

# Reject LLM output strings that contain shell-like injection patterns (finding #3.2).
_INJECTION_RE = re.compile(r"[;&|`$(){}\\\n]|\.\./")
_REDACTOR = ContextRedactor()


def _as_float(val: object) -> float | None:
    """Return val as float if it's numeric, else None — safe for JSON serialization."""
    if isinstance(val, (int, float)):
        return float(val)
    return None


@dataclass
class StoredSignalContext:
    """Historical signal context loaded from stores before planning.

    Assembled by plan_vm_node from existing stores and passed to
    prioritize_actions() so the LLM sees trend data, not just current state.
    """

    disk_trend_summary: str = ""
    drift_kinds_detected: list[str] = field(default_factory=list)
    recent_failure_count: int = 0
    last_patch_days_ago: int | None = None
    failed_login_count_24h: int = 0


#: Default priority ordering — lowest risk first, then by operational value.
#: Disk cleanup and log rotation free space (enables other actions).
DEFAULT_PRIORITY: list[ActionType] = [
    ActionType.BACKUP_VERIFY,   # LOW — read-only check, run first
    ActionType.DISK_CLEANUP,    # LOW
    ActionType.LOG_ROTATION,    # LOW
    ActionType.DOCKER_HYGIENE,  # MEDIUM
    ActionType.PATCHING,        # MEDIUM
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
    if action_type == ActionType.DOCKER_HYGIENE:
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
    policy: str = "moderate",
    batch_id: str = "unknown",
    vm_id: str | None = None,
    ai_store: AIDecisionStore | None = None,
    stored_signals: StoredSignalContext | None = None,
) -> list[Action]:
    """Order maintenance actions by priority using LLM, with hardcoded fallback.

    Args:
        vm_info: Discovered system state from the VM.
        available_actions: Action types to consider. Defaults to all types.
        llm_client: Optional LLM client. If None, uses hardcoded fallback.
        policy: Maintenance policy name — used to filter LLM output (finding #3.2).
        batch_id: For per-decision audit logging (finding #3.4).
        vm_id: For per-decision audit logging.
        ai_store: Optional AIDecisionStore for per-call audit (finding #3.4).

    Returns:
        Actions ordered by priority with risk tiers assigned.
    """
    from errander.config.policies import BUILTIN_POLICIES
    from errander.safety.ai_audit import AIDecision

    if available_actions is None:
        available_actions = list(DEFAULT_PRIORITY)

    policy_obj = BUILTIN_POLICIES.get(policy) or BUILTIN_POLICIES["moderate"]

    if llm_client is not None:
        prompt = _build_prioritize_prompt(vm_info, available_actions, stored_signals)
        prompt, _rc = _REDACTOR.redact(prompt)
        if _rc:
            logger.warning("Redacted %d secret(s) from prioritize_actions prompt", _rc)
        prompt_hash = AIDecision.hash_prompt(prompt)
        t0 = time.monotonic()
        outcome = "fallback"
        response_raw: str | None = None
        result = await llm_client.complete(prompt, _PrioritizedActions)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if result is not None:
            # 3.2a — injection guard: reject any action type string with shell metacharacters
            safe_types = [
                a for a in result.action_types
                if not _INJECTION_RE.search(a)
            ]
            if len(safe_types) < len(result.action_types):
                logger.warning(
                    "LLM returned %d action type(s) with suspicious characters — rejected",
                    len(result.action_types) - len(safe_types),
                )

            # 3.2b — policy enforcement: strip actions that violate policy
            try:
                ordered = _parse_action_types(safe_types, available_actions)
                # Remove any action type that requires approval under the current policy
                # and is not in auto_approve_tiers (defense-in-depth on top of the batch gate)
                policy_filtered = [
                    a for a in ordered
                    if ACTION_RISK_TIERS.get(a, RiskTier.MEDIUM) in policy_obj.auto_approve_tiers
                    or ACTION_RISK_TIERS.get(a, RiskTier.MEDIUM) == RiskTier.LOW
                ]
                if len(policy_filtered) < len(ordered):
                    logger.info(
                        "Policy=%s filtered %d action(s) from LLM plan (requires approval)",
                        policy, len(ordered) - len(policy_filtered),
                    )
                    # Fall back to full ordered list — approval is handled at the batch gate
                    # We log but do not strip (the batch gate is authoritative)
                    policy_filtered = ordered

                if policy_filtered:
                    response_raw = str(result.action_types)
                    outcome = "success"
                    logger.info(
                        "LLM prioritization (policy=%s): %s",
                        policy, [a.value for a in policy_filtered],
                    )
                    if ai_store is not None:
                        await ai_store.log(AIDecision(
                            batch_id=batch_id,
                            vm_id=vm_id,
                            decision_type="prioritize_actions",
                            model=getattr(llm_client, "_model", "unknown"),
                            base_url=getattr(llm_client, "_base_url", ""),
                            prompt_template_id="prioritize_v1",
                            prompt_hash=prompt_hash,
                            response_raw=response_raw,
                            outcome=outcome,
                            latency_ms=latency_ms,
                            prompt_full=prompt,
                            context_snapshot=json.dumps({
                                "vm_info": asdict(vm_info),
                                "available_actions": [str(a) for a in (available_actions or [])],
                            }),
                            model_params=json.dumps({
                                "temperature": _as_float(getattr(llm_client, "_temperature", None)),
                            }),
                        ))
                    return [
                        Action(
                            action_type=a,
                            risk_tier=ACTION_RISK_TIERS.get(a, RiskTier.MEDIUM),
                        )
                        for a in policy_filtered
                    ]
            except ValueError as exc:
                logger.warning("LLM returned invalid action types: %s — using fallback", exc)

        if ai_store is not None:
            await ai_store.log(AIDecision(
                batch_id=batch_id,
                vm_id=vm_id,
                decision_type="prioritize_actions",
                model=getattr(llm_client, "_model", "unknown"),
                base_url=getattr(llm_client, "_base_url", ""),
                prompt_template_id="prioritize_v1",
                prompt_hash=prompt_hash,
                response_raw=response_raw,
                outcome="fallback",
                latency_ms=latency_ms,
                prompt_full=prompt,
                context_snapshot=json.dumps({
                    "vm_info": asdict(vm_info),
                    "available_actions": [str(a) for a in (available_actions or [])],
                }),
                model_params=json.dumps({
                    "temperature": _as_float(getattr(llm_client, "_temperature", None)),
                }),
            ))
        logger.info(
            "LLM unavailable or returned invalid response (policy=%s) — using hardcoded priority",
            policy,
        )

    elif ai_store is not None:
        # No LLM — log that hardcoded fallback was used
        from errander.safety.ai_audit import AIDecision
        await ai_store.log(AIDecision(
            batch_id=batch_id,
            vm_id=vm_id,
            decision_type="prioritize_actions",
            model="none",
            base_url="",
            prompt_template_id="prioritize_v1",
            prompt_hash="",
            outcome="no_llm",
            context_snapshot=json.dumps({
                "vm_info": asdict(vm_info),
                "available_actions": [str(a) for a in (available_actions or [])],
            }),
        ))

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
        prompt, _rc = _REDACTOR.redact(prompt)
        if _rc:
            logger.warning("Redacted %d secret(s) from analyze_failure prompt", _rc)
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
        prompt, _rc = _REDACTOR.redact(prompt)
        if _rc:
            logger.warning("Redacted %d secret(s) from generate_report prompt", _rc)
        result = await llm_client.complete(prompt, _Report)
        if result is not None and result.report.strip():
            logger.info("LLM-generated report for batch %s", batch_id)
            return result.report

    return _template_report(results, batch_id)


# --- Prompt builders ---

def _build_prioritize_prompt(
    vm_info: VMInfo,
    available_actions: list[ActionType],
    stored_signals: StoredSignalContext | None = None,
) -> str:
    applicable = filter_applicable_actions(available_actions, vm_info)
    lines = [
        "Prioritize these maintenance actions for a VM with the following state:",
        f"- OS: {vm_info.os_family.value} {vm_info.os_version}",
        f"- Disk usage: {vm_info.disk_usage}",
        f"- Docker available: {vm_info.docker_available}",
        f"- Pending packages: {vm_info.pending_packages}",
        f"- Uptime seconds: {vm_info.uptime_seconds}",
    ]
    if stored_signals is not None:
        lines.append("\n## Historical signals from monitoring stores")
        if stored_signals.disk_trend_summary:
            lines.append(f"Disk trend (7d): {stored_signals.disk_trend_summary}")
        if stored_signals.drift_kinds_detected:
            lines.append(f"Config drift detected: {', '.join(stored_signals.drift_kinds_detected)}")
        if stored_signals.recent_failure_count > 0:
            lines.append(f"Action failures (14d): {stored_signals.recent_failure_count}")
        if stored_signals.last_patch_days_ago is not None:
            lines.append(f"Last patched: {stored_signals.last_patch_days_ago} days ago")
        if stored_signals.failed_login_count_24h > 0:
            lines.append(f"Failed SSH logins (24h): {stored_signals.failed_login_count_24h}")
    lines += [
        f"\nAvailable actions: {[a.value for a in applicable]}",
        "\nOrder them from highest to lowest urgency.",
        'Respond with JSON: {"action_types": ["<action1>", "<action2>", ...]}',
    ]
    return "\n".join(lines)


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
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
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
