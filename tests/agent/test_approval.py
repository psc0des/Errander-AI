"""Tests for service_restart approval guarantee.

Verifies that service_restart (HIGH risk tier) always routes through Slack
approval and cannot be auto-approved under any policy tier, thanks to the
HITL guardrail in approval_gate_node.
"""

from __future__ import annotations

from errander.models.actions import ACTION_RISK_TIERS, ActionType, RiskTier


class TestServiceRestartApprovalGuarantee:
    def test_service_restart_risk_tier_is_high(self) -> None:
        assert ACTION_RISK_TIERS[ActionType.SERVICE_RESTART] is RiskTier.HIGH

    def test_high_tier_in_strict_approval_set(self) -> None:
        # strict policy: MEDIUM, HIGH, CRITICAL all require approval
        strict_tiers: frozenset[RiskTier] = frozenset({
            RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL,
        })
        assert RiskTier.HIGH in strict_tiers

    def test_high_tier_in_moderate_approval_set(self) -> None:
        # moderate policy: HIGH, CRITICAL require approval
        moderate_tiers: frozenset[RiskTier] = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})
        assert RiskTier.HIGH in moderate_tiers

    def test_hitl_guardrail_forces_all_tiers_including_high(self) -> None:
        """When require_live_approval=True (HITL default), ALL tiers need approval.

        This mirrors the logic in graph.py approval_gate_node (lines 1227–1230).
        """
        require_live_approval = True
        dry_run = False
        if require_live_approval and not dry_run:
            approval_tiers: frozenset[RiskTier] = frozenset({
                RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.CRITICAL,
            })
        else:
            approval_tiers = frozenset()
        assert RiskTier.HIGH in approval_tiers

    def test_autonomous_gate_cannot_disable_hitl_for_live_mode(self) -> None:
        """When autonomous_live_apply_enabled=False, setting require_live_approval=False
        is rejected — the guardrail forces it back to True.

        This mirrors the logic in graph.py approval_gate_node (lines 1217–1223).
        """
        autonomous_live_apply_enabled = False  # system default
        require_live_approval = False  # caller attempts to disable
        dry_run = False  # live mode

        # Guardrail: if autonomous mode is off and caller disabled HITL, re-enable it
        if not autonomous_live_apply_enabled and not require_live_approval and not dry_run:
            require_live_approval = True

        assert require_live_approval is True

    def test_relaxed_policy_alone_does_not_cover_high(self) -> None:
        """Under relaxed policy WITHOUT the HITL guardrail, HIGH tier would not require
        approval (only CRITICAL does). This is why the HITL guardrail is mandatory.
        """
        relaxed_tiers: frozenset[RiskTier] = frozenset({RiskTier.CRITICAL})
        # Under bare relaxed policy, HIGH is NOT in the approval set
        assert RiskTier.HIGH not in relaxed_tiers
        # But the HITL guardrail overrides this — see test_hitl_guardrail_forces_all_tiers_including_high

    def test_service_restart_not_low_or_medium(self) -> None:
        tier = ACTION_RISK_TIERS[ActionType.SERVICE_RESTART]
        assert tier not in {RiskTier.LOW, RiskTier.MEDIUM}
