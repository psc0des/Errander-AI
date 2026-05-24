"""Tests for action models."""

from __future__ import annotations

from errander.models.actions import ACTION_RISK_TIERS, LEGACY_ACTION_TYPES, ActionType, RiskTier


class TestActionModels:
    """Tests for action type enums and risk tier mapping."""

    def test_all_active_action_types_have_risk_tiers(self) -> None:
        for action_type in ActionType:
            if action_type in LEGACY_ACTION_TYPES:
                continue  # legacy types kept for audit log read-back only
            assert action_type in ACTION_RISK_TIERS

    def test_kernel_never_in_action_types(self) -> None:
        for action_type in ActionType:
            assert "kernel" not in action_type.lower()

    def test_critical_tier_not_in_defaults(self) -> None:
        for tier in ACTION_RISK_TIERS.values():
            assert tier != RiskTier.CRITICAL
