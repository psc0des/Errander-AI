"""LLM-powered decision logic with hardcoded fallbacks.

Decision points:
- Action prioritization: deterministic, hardcoded — plan membership and
  ordering never depend on an LLM (R1)
- Planning note: LLM-generated advisory commentary on the deterministic
  plan, shown to the operator as informational-only context
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
    generate_planning_note() so the LLM sees trend data, not just current state.
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

class _PlanningNote(BaseModel):
    """LLM response schema for the advisory planning note."""
    note: str


class _Report(BaseModel):
    """LLM response schema for report generation."""
    report: str


#: Hard cap on the rendered planning note — keeps the operator-facing
#: AI-analysis section short and bounds the approval artifact size.
_PLANNING_NOTE_MAX_CHARS = 700


def _sanitize_note(note: str, max_chars: int = _PLANNING_NOTE_MAX_CHARS) -> str:
    """Strip backticks and cap length — defense-in-depth for AI-generated text."""
    cleaned = note.replace("`", "").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


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
) -> list[Action]:
    """Order maintenance actions by priority — deterministic, hardcoded (R1).

    Plan membership and ordering never depend on an LLM. See
    generate_planning_note() for the advisory LLM commentary on this plan.

    Args:
        vm_info: Discovered system state from the VM.
        available_actions: Action types to consider. Defaults to DEFAULT_PRIORITY.

    Returns:
        Actions ordered by priority with risk tiers assigned.
    """
    if available_actions is None:
        available_actions = list(DEFAULT_PRIORITY)
    return _hardcoded_priority(available_actions, vm_info)


async def generate_planning_note(
    vm_info: VMInfo,
    plan: list[Action],
    llm_client: LLMClient | None = None,
    batch_id: str = "unknown",
    vm_id: str | None = None,
    ai_store: AIDecisionStore | None = None,
    stored_signals: StoredSignalContext | None = None,
) -> str | None:
    """Generate an advisory note about the already-finalized plan.

    Layer A, informational only — the note can never change plan membership
    or ordering. Returns None when the LLM is unavailable or returns an
    empty/unparseable response; never raises, never blocks planning.
    """
    from errander.safety.ai_audit import AIDecision

    common_ctx = json.dumps({
        "vm_info": asdict(vm_info),
        "plan": [a.action_type.value for a in plan],
    })

    if llm_client is None:
        if ai_store is not None:
            await ai_store.log(AIDecision(
                batch_id=batch_id,
                vm_id=vm_id,
                decision_type="planning_note",
                model="none",
                base_url="",
                prompt_template_id="planning_note_v1",
                prompt_hash="",
                outcome="no_llm",
                context_snapshot=common_ctx,
            ))
        return None

    prompt = _build_planning_note_prompt(vm_info, plan, stored_signals)
    prompt, _rc = _REDACTOR.redact(prompt)
    if _rc:
        logger.warning("Redacted %d secret(s) from planning_note prompt", _rc)
    prompt_hash = AIDecision.hash_prompt(prompt)
    t0 = time.monotonic()
    result = await llm_client.complete(prompt, _PlanningNote)
    latency_ms = round((time.monotonic() - t0) * 1000, 1)

    model_params = json.dumps({
        "temperature": _as_float(getattr(llm_client, "_temperature", None)),
    })

    if result is None or not result.note.strip():
        if ai_store is not None:
            await ai_store.log(AIDecision(
                batch_id=batch_id,
                vm_id=vm_id,
                decision_type="planning_note",
                model=getattr(llm_client, "_model", "unknown"),
                base_url=getattr(llm_client, "_base_url", ""),
                prompt_template_id="planning_note_v1",
                prompt_hash=prompt_hash,
                response_raw=result.model_dump_json() if result is not None else None,
                outcome="fallback",
                latency_ms=latency_ms,
                prompt_full=prompt,
                context_snapshot=common_ctx,
                model_params=model_params,
            ))
        return None

    note = _sanitize_note(result.note)
    if ai_store is not None:
        await ai_store.log(AIDecision(
            batch_id=batch_id,
            vm_id=vm_id,
            decision_type="planning_note",
            model=getattr(llm_client, "_model", "unknown"),
            base_url=getattr(llm_client, "_base_url", ""),
            prompt_template_id="planning_note_v1",
            prompt_hash=prompt_hash,
            response_raw=result.model_dump_json(),
            outcome="success",
            latency_ms=latency_ms,
            prompt_full=prompt,
            context_snapshot=common_ctx,
            model_params=model_params,
        ))
    return note


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

def _build_planning_note_prompt(
    vm_info: VMInfo,
    plan: list[Action],
    stored_signals: StoredSignalContext | None = None,
) -> str:
    lines = [
        "A maintenance plan has already been determined for this VM "
        "(deterministic, not your decision).",
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
        f"\nPlanned actions (in execution order): {[a.action_type.value for a in plan]}",
        "\nWrite a 1-4 sentence note for the human operator highlighting anything"
        " noteworthy about this plan given the state above (e.g. risk context,"
        " trends, why an action matters now). Do not propose changes to the"
        " plan — it is fixed.",
        'Respond with JSON: {"note": "<1-4 sentences>"}',
    ]
    return "\n".join(lines)


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
