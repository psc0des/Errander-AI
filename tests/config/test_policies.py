"""Tests for maintenance policies."""

from __future__ import annotations

import pytest

from automaint.config.policies import BUILTIN_POLICIES, get_policy
from automaint.models.actions import RiskTier


class TestPolicies:
    """Tests for policy lookup and definitions."""

    def test_builtin_policies_exist(self) -> None:
        assert "relaxed" in BUILTIN_POLICIES
        assert "moderate" in BUILTIN_POLICIES
        assert "strict" in BUILTIN_POLICIES

    def test_get_policy_valid(self) -> None:
        policy = get_policy("moderate")
        assert policy.name == "moderate"

    def test_get_policy_invalid(self) -> None:
        with pytest.raises(ValueError, match="Unknown policy"):
            get_policy("nonexistent")

    def test_strict_requires_all_approval(self) -> None:
        policy = get_policy("strict")
        assert len(policy.auto_approve_tiers) == 0

    def test_relaxed_auto_approves_low_and_medium(self) -> None:
        policy = get_policy("relaxed")
        assert RiskTier.LOW in policy.auto_approve_tiers
        assert RiskTier.MEDIUM in policy.auto_approve_tiers
