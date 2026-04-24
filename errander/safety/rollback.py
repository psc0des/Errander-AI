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

import logging

from errander.models.actions import ActionType

logger = logging.getLogger(__name__)


async def rollback_action(
    action_type: ActionType,
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Attempt to rollback a failed action.

    Uses a strategy-per-action-type dispatch. Not all action types
    require or support rollback.

    Args:
        action_type: The type of action to rollback.
        vm_id: Target VM identifier.
        pre_snapshot: State snapshot taken before execution.

    Returns:
        Tuple of (success, detail). If failed, detail explains what went wrong.
    """
    strategy = _ROLLBACK_STRATEGIES.get(action_type)
    if strategy is None:
        detail = f"Unknown action type for rollback: {action_type.value}"
        logger.error("Rollback failed for %s on %s: %s", action_type.value, vm_id, detail)
        return False, detail

    return await strategy(vm_id, pre_snapshot)


async def _rollback_patching(
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Rollback patching by reinstalling previous package versions.

    Not yet implemented — requires SSH execution to run
    `apt-get install -y --allow-downgrades pkg=version` for each
    package in the pre_snapshot.
    """
    logger.warning(
        "Patching rollback requested for %s with snapshot keys: %s — not yet implemented",
        vm_id, list(pre_snapshot.keys()),
    )
    return False, "Patching rollback not yet implemented — requires package version restore via SSH"


async def _rollback_docker_prune(
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Docker prune has no true rollback — pruned resources are gone.

    If needed, images can be re-pulled from the registry.
    """
    return True, "Docker prune is low-risk — re-pull images from registry if needed"


async def _rollback_disk_cleanup(
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Disk cleanup targets only safe paths — no rollback needed."""
    return True, "No rollback needed for disk cleanup — only targets whitelisted paths"


async def _rollback_log_rotation(
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Log rotation compresses data — original data still exists."""
    return True, "No rollback needed for log rotation — data is compressed, not deleted"


async def _rollback_backup_verify(
    vm_id: str,
    pre_snapshot: dict[str, object],
) -> tuple[bool, str]:
    """Backup verification is read-only — nothing to rollback."""
    return True, "Backup verify is read-only — no state changes to rollback"


_ROLLBACK_STRATEGIES: dict[
    ActionType,
    type[None]  # placeholder for the callable type
] = {}  # type: ignore[assignment]

# Register strategies — done after function definitions to avoid forward refs
_ROLLBACK_STRATEGIES = {
    ActionType.PATCHING: _rollback_patching,
    ActionType.DOCKER_PRUNE: _rollback_docker_prune,
    ActionType.DISK_CLEANUP: _rollback_disk_cleanup,
    ActionType.LOG_ROTATION: _rollback_log_rotation,
    ActionType.BACKUP_VERIFY: _rollback_backup_verify,
}
