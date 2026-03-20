"""LLM-powered decision logic with hardcoded fallbacks.

All decision functions follow the pattern:
1. Try LLM call with structured JSON output (Pydantic model)
2. On LLM failure/timeout: fall back to hardcoded default logic

Decision points:
- Action prioritization: Given system state, order actions by urgency
- Failure analysis: Given an error, determine if retry/rollback/escalate
- Report generation: Summarize batch results in human-readable format

LLM modes:
- Thinking mode: Used for planning + failure analysis (complex reasoning)
- /no_think mode: Used for report generation (fast, structured output)
"""

from __future__ import annotations

from automaint.models.actions import Action


async def prioritize_actions(
    system_info: dict[str, object],
    available_actions: list[Action],
) -> list[Action]:
    """Order maintenance actions by priority using LLM, with fallback.

    Args:
        system_info: Discovered system state from the VM.
        available_actions: Actions that could be performed.

    Returns:
        Actions reordered by priority (lowest risk first as default).
    """
    raise NotImplementedError("Action prioritization not yet implemented")


async def analyze_failure(
    action_type: str,
    error: str,
    context: dict[str, object],
) -> str:
    """Analyze an action failure and recommend next steps using LLM.

    Args:
        action_type: What action failed.
        error: The error message/output.
        context: Additional context (VM info, action params).

    Returns:
        Recommendation: 'retry', 'rollback', or 'escalate'.
    """
    raise NotImplementedError("Failure analysis not yet implemented")


async def generate_report(
    results: list[dict[str, object]],
) -> str:
    """Generate a human-readable report from batch results.

    Uses /no_think mode for fast, structured output.
    Falls back to template-based report on LLM failure.

    Args:
        results: Aggregated action results from all VMs.

    Returns:
        Formatted report string for Slack posting.
    """
    raise NotImplementedError("Report generation not yet implemented")
