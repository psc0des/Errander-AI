"""Action result tracking — updates Prometheus metrics from ActionResult.

Call record_action_result() after every action execution. The metrics
module owns the counters and histograms; this module knows the mapping
from ActionResult fields to metric labels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from errander.observability.metrics import (
    ACTION_DURATION,
    ACTIONS_TOTAL,
    LLM_REQUESTS_TOTAL,
    SSH_ERRORS_TOTAL,
)

if TYPE_CHECKING:
    from errander.models.actions import ActionResult


def record_action_result(result: ActionResult) -> None:
    """Update Prometheus counters and histograms from a completed action.

    Records:
    - errander_actions_total (action_type, status, vm_id)
    - errander_action_duration_seconds (action_type) — only when completed_at set

    Args:
        result: The completed action result.
    """
    ACTIONS_TOTAL.labels(
        action_type=result.action_type,
        status=result.status,
        vm_id=result.vm_id,
    ).inc()

    if result.completed_at is not None and result.started_at is not None:
        duration = (result.completed_at - result.started_at).total_seconds()
        ACTION_DURATION.labels(action_type=result.action_type).observe(duration)


def record_ssh_error(vm_id: str, reason: str) -> None:
    """Increment SSH error counter.

    Args:
        vm_id: Target VM identifier.
        reason: Short error category (e.g., "connection_failed", "timeout", "auth_error").
    """
    SSH_ERRORS_TOTAL.labels(vm_id=vm_id, reason=reason).inc()


def record_llm_outcome(outcome: str) -> None:
    """Increment LLM request counter.

    Args:
        outcome: One of "success", "fallback", "timeout", "error".
    """
    LLM_REQUESTS_TOTAL.labels(outcome=outcome).inc()
