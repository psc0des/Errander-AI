"""Pre-execution validation checks.

Validators run before every action to ensure preconditions are met.
If validation fails, the action is skipped with a logged reason.

Validation checks include:
- Risk tier gate: block CRITICAL actions (never automated)
- Kernel exclusion: reject any action targeting kernel packages
- Disk whitelist: reject cleanup of non-whitelisted paths
- OS compatibility: action is supported on the target OS
"""

from __future__ import annotations

import logging

from errander.agent.subgraphs.disk_cleanup import ALLOWED_CLEANUP_PATHS
from errander.config.policies import MaintenancePolicy
from errander.models.actions import Action, ActionType, RiskTier

logger = logging.getLogger(__name__)

#: Patterns that indicate kernel packages — always excluded from patching.
_KERNEL_PATTERNS: frozenset[str] = frozenset({
    "linux-image",
    "linux-headers",
    "linux-modules",
    "kernel",
    "kernel-core",
    "kernel-devel",
})


def _contains_kernel_packages(params: dict[str, object]) -> bool:
    """Check if action params reference kernel packages."""
    packages = params.get("packages", [])
    if not isinstance(packages, list):
        return False
    exclude = params.get("exclude_patterns", [])
    if not isinstance(exclude, list):
        exclude = []
    # Check if any package name starts with a kernel pattern
    for pkg in packages:
        pkg_str = str(pkg).lower()
        if any(pkg_str.startswith(kp) for kp in _KERNEL_PATTERNS):
            return True
    return False


async def validate_action(
    action: Action,
    vm_id: str,
    os_family: str,
    policy: str = "moderate",
) -> tuple[bool, str]:
    """Validate whether an action should proceed.

    Checks (in order):
    1. Critical risk tier → always blocked.
    2. Kernel exclusion → patching actions with kernel packages blocked.
    3. Disk whitelist → cleanup paths must be whitelisted.

    Args:
        action: The action to validate.
        vm_id: Target VM identifier.
        os_family: Detected OS family.
        policy: Maintenance policy name (unused in current checks,
            reserved for policy-based gating).

    Returns:
        Tuple of (is_valid, reason). If invalid, reason explains why.
    """
    # 1. Critical actions are NEVER automated
    if action.risk_tier == RiskTier.CRITICAL:
        reason = f"Critical-risk action {action.action_type.value} is never automated"
        logger.warning("BLOCKED %s on %s: %s", action.action_type.value, vm_id, reason)
        return False, reason

    # 2. Kernel exclusion for patching
    if action.action_type == ActionType.PATCHING and _contains_kernel_packages(action.params):
        reason = "Kernel packages detected — kernel patching is never automated"
        logger.warning("BLOCKED patching on %s: %s", vm_id, reason)
        return False, reason

    # 3. Disk cleanup whitelist enforcement
    if action.action_type == ActionType.DISK_CLEANUP:
        paths = action.params.get("paths", [])
        if isinstance(paths, list) and paths:
            rejected = [p for p in paths if str(p) not in ALLOWED_CLEANUP_PATHS]
            if rejected:
                reason = f"Paths not on cleanup whitelist: {rejected}"
                logger.warning("BLOCKED disk_cleanup on %s: %s", vm_id, reason)
                return False, reason

    return True, ""


def requires_approval(risk_tier: RiskTier, policy: MaintenancePolicy) -> bool:
    """Determine if an action at this risk tier needs human approval.

    Args:
        risk_tier: The action's risk classification.
        policy: The VM's maintenance policy.

    Returns:
        True if human approval is required before execution.
    """
    return risk_tier not in policy.auto_approve_tiers
