"""Named maintenance policies — relaxed, moderate, strict.

Policies control:
- Which risk tiers are auto-approved vs. require human approval
- Maintenance window constraints
- Retry limits
- Rollback behavior

Each VM in the inventory references a policy by name.
"""

from __future__ import annotations

from dataclasses import dataclass

from automaint.models.actions import RiskTier


@dataclass(frozen=True)
class MaintenancePolicy:
    """A named maintenance policy.

    Attributes:
        name: Policy identifier (relaxed/moderate/strict).
        auto_approve_tiers: Risk tiers that execute without human approval.
        max_retries: Maximum retry attempts per action.
        rollback_on_failure: Whether to auto-rollback on failure.
    """

    name: str
    auto_approve_tiers: frozenset[RiskTier]
    max_retries: int
    rollback_on_failure: bool


#: Built-in policy definitions.
BUILTIN_POLICIES: dict[str, MaintenancePolicy] = {
    "relaxed": MaintenancePolicy(
        name="relaxed",
        auto_approve_tiers=frozenset({RiskTier.LOW, RiskTier.MEDIUM}),
        max_retries=2,
        rollback_on_failure=True,
    ),
    "moderate": MaintenancePolicy(
        name="moderate",
        auto_approve_tiers=frozenset({RiskTier.LOW}),
        max_retries=1,
        rollback_on_failure=True,
    ),
    "strict": MaintenancePolicy(
        name="strict",
        auto_approve_tiers=frozenset(),
        max_retries=0,
        rollback_on_failure=True,
    ),
}


def get_policy(name: str) -> MaintenancePolicy:
    """Look up a maintenance policy by name.

    Args:
        name: Policy name.

    Returns:
        MaintenancePolicy.

    Raises:
        ValueError: If policy name is not recognized.
    """
    policy = BUILTIN_POLICIES.get(name)
    if policy is None:
        msg = f"Unknown policy: {name}. Available: {list(BUILTIN_POLICIES)}"
        raise ValueError(msg)
    return policy
