"""Tests for pre-execution validators."""

from __future__ import annotations

import pytest

from errander.config.policies import BUILTIN_POLICIES, MaintenancePolicy
from errander.models.actions import Action, ActionType, RiskTier
from errander.safety.validators import requires_approval, validate_action


class TestValidateAction:
    """Tests for validate_action()."""

    @pytest.mark.asyncio
    async def test_critical_risk_tier_blocked(self) -> None:
        action = Action(action_type=ActionType.PATCHING, risk_tier=RiskTier.CRITICAL)
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "never automated" in reason.lower()

    @pytest.mark.asyncio
    async def test_kernel_packages_blocked(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["linux-image-5.15.0-generic", "curl"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "kernel" in reason.lower()

    @pytest.mark.asyncio
    async def test_kernel_devel_blocked(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["kernel-devel"]},
        )
        valid, reason = await validate_action(action, "vm-01", "rhel")
        assert not valid
        assert "kernel" in reason.lower()

    @pytest.mark.asyncio
    async def test_patching_without_kernel_allowed(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["curl", "nginx"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid
        assert reason == ""

    @pytest.mark.asyncio
    async def test_disk_cleanup_whitelisted_paths_allowed(self) -> None:
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={"paths": ["/tmp", "apt-cache"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid

    @pytest.mark.asyncio
    async def test_disk_cleanup_non_whitelisted_blocked(self) -> None:
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={"paths": ["/tmp", "/var/lib/postgresql"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "/var/lib/postgresql" in reason

    @pytest.mark.asyncio
    async def test_disk_cleanup_no_explicit_paths_allowed(self) -> None:
        """When no paths in params, whitelist check is skipped (sub-graph uses defaults)."""
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid

    @pytest.mark.asyncio
    async def test_low_risk_action_allowed(self) -> None:
        action = Action(action_type=ActionType.LOG_ROTATION, risk_tier=RiskTier.LOW)
        valid, reason = await validate_action(action, "vm-01", "debian")
        assert valid
        assert reason == ""

    @pytest.mark.asyncio
    async def test_high_risk_non_critical_allowed(self) -> None:
        """HIGH risk actions pass validation — approval is a separate concern."""
        action = Action(action_type=ActionType.BACKUP_VERIFY, risk_tier=RiskTier.HIGH)
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid


class TestRequiresApproval:
    """Tests for requires_approval()."""

    def test_low_tier_relaxed_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert not requires_approval(RiskTier.LOW, policy)

    def test_medium_tier_relaxed_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert not requires_approval(RiskTier.MEDIUM, policy)

    def test_high_tier_relaxed_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert requires_approval(RiskTier.HIGH, policy)

    def test_low_tier_moderate_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["moderate"]
        assert not requires_approval(RiskTier.LOW, policy)

    def test_medium_tier_moderate_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["moderate"]
        assert requires_approval(RiskTier.MEDIUM, policy)

    def test_low_tier_strict_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["strict"]
        assert requires_approval(RiskTier.LOW, policy)

    def test_critical_always_needs_approval(self) -> None:
        for name, policy in BUILTIN_POLICIES.items():
            assert requires_approval(RiskTier.CRITICAL, policy), f"CRITICAL should need approval under {name}"
