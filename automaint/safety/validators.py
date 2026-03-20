"""Pre-execution validation checks.

Validators run before every action to ensure preconditions are met.
If validation fails, the action is skipped with a logged reason.

Validation checks include:
- Risk tier gate: action risk vs. policy approval requirements
- Kernel exclusion: reject any action targeting kernel packages
- Disk whitelist: reject cleanup of non-whitelisted paths
- OS compatibility: action is supported on the target OS
- Resource availability: sufficient disk/memory to proceed
"""

from __future__ import annotations

from automaint.models.actions import Action, RiskTier


async def validate_action(
    action: Action,
    vm_id: str,
    os_family: str,
    policy: str,
) -> tuple[bool, str]:
    """Validate whether an action should proceed.

    Args:
        action: The action to validate.
        vm_id: Target VM identifier.
        os_family: Detected OS family.
        policy: Maintenance policy name.

    Returns:
        Tuple of (is_valid, reason). If invalid, reason explains why.
    """
    raise NotImplementedError("Action validation not yet implemented")


def requires_approval(risk_tier: RiskTier, policy: str) -> bool:
    """Determine if an action at this risk tier needs human approval.

    Args:
        risk_tier: The action's risk classification.
        policy: The VM's maintenance policy.

    Returns:
        True if human approval is required before execution.
    """
    raise NotImplementedError("Approval requirement check not yet implemented")
