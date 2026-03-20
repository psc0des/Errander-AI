"""Rollback capabilities per action type.

Each action type has a defined rollback strategy (see CLAUDE.md Rollback Tiers):
- Full rollback: patching (reinstall previous package versions)
- Re-pull: Docker prune (re-pull images if needed)
- No rollback needed: log rotation, disk cleanup
- Never touch: kernel, active data dirs

Rollback is attempted automatically on action failure.
If rollback itself fails, the action is marked NEEDS_MANUAL and a critical
alert is sent to Slack.
"""

from __future__ import annotations

from automaint.models.actions import ActionType


async def rollback_action(
    action_type: ActionType,
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Attempt to rollback a failed action.

    Args:
        action_type: The type of action to rollback.
        vm_id: Target VM identifier.
        pre_snapshot: State snapshot taken before execution.

    Returns:
        Tuple of (success, detail). If failed, detail explains what went wrong.
    """
    raise NotImplementedError("Rollback not yet implemented")
