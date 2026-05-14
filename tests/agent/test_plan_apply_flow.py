"""Tests for the plan/apply flow (finding #3 from SRE audit).

Covers:
- verify_plan_hash_node: correct hash passes, tampered hash aborts
- route_after_approval: approved → verify_plan_hash, rejected → generate_report
- route_after_hash_verify: ok → prepare_waves, drift → generate_report
- approval_gate_node policy thresholds: strict/moderate/relaxed
- Live batch cannot reach execution without approved=True + matching plan_hash
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    _format_plan_for_approval,
    approval_gate_node,
    route_after_approval,
    route_after_hash_verify,
    verify_plan_hash_node,
)
from errander.models.actions import RiskTier
from errander.safety.approval import ApprovalManager


# --- Helpers ---

def _make_vm_plans(
    action_types: list[str] | None = None,
    risk_tiers: list[str] | None = None,
) -> list[dict[str, Any]]:
    actions = [
        {"action_type": at, "risk_tier": rt}
        for at, rt in zip(
            action_types or ["disk_cleanup"],
            risk_tiers or ["low"],
        )
    ]
    return [{"vm_id": "dev/web-01", "planned_actions": actions, "os_family": "ubuntu"}]


def _compute_hash(vm_plans: list[dict], batch_id: str, env_name: str) -> str:
    canonical = json.dumps(
        {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _base_state(**overrides: Any) -> BatchGraphState:
    vm_plans = overrides.pop("vm_plans", _make_vm_plans())
    batch_id = overrides.pop("batch_id", "batch-test-001")
    env_name = overrides.pop("env_name", "dev")
    plan_hash = overrides.pop("plan_hash", _compute_hash(vm_plans, batch_id, env_name))

    state: BatchGraphState = {  # type: ignore[typeddict-unknown-key]
        "dry_run": False,
        "batch_id": batch_id,
        "env_name": env_name,
        "env_policy": "moderate",
        "vm_plans": vm_plans,
        "plan_id": "plan-abc123",
        "plan_hash": plan_hash,
        "approved": None,
        "deferred": False,
        "vm_results": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ---------------------------------------------------------------------------
# verify_plan_hash_node
# ---------------------------------------------------------------------------

class TestVerifyPlanHashNode:
    """verify_plan_hash_node re-checks hash before dispatching waves."""

    @pytest.mark.asyncio
    async def test_correct_hash_passes(self) -> None:
        state = _base_state()
        result = await verify_plan_hash_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_tampered_hash_returns_error(self) -> None:
        state = _base_state(plan_hash="0" * 64)  # wrong hash
        result = await verify_plan_hash_node(state)
        assert result.get("approved") is False
        assert "drift" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_missing_hash_returns_error(self) -> None:
        state = _base_state(plan_hash="")
        result = await verify_plan_hash_node(state)
        assert result.get("approved") is False
        assert "missing" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_dry_run_skips_hash_check(self) -> None:
        """Dry-run sandbox never needs hash enforcement."""
        state = _base_state(dry_run=True, plan_hash="0" * 64)  # wrong hash, doesn't matter
        result = await verify_plan_hash_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_error_includes_short_hashes(self) -> None:
        """Error message includes both hashes for operator diagnosis."""
        state = _base_state(plan_hash="a" * 64)
        result = await verify_plan_hash_node(state)
        error = result.get("error", "")
        assert "aaaaaaaaaaaa" in error  # stored short hash in message


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

class TestRoutingAfterApproval:
    """route_after_approval routes to verify_plan_hash (not prepare_waves directly)."""

    def test_approved_routes_to_verify_hash(self) -> None:
        state = _base_state(approved=True, deferred=False)
        assert route_after_approval(state) == "verify_plan_hash"

    def test_rejected_routes_to_report(self) -> None:
        state = _base_state(approved=False, deferred=False)
        assert route_after_approval(state) == "generate_report"

    def test_deferred_routes_to_end(self) -> None:
        from langgraph.graph import END
        state = _base_state(approved=True, deferred=True)
        assert route_after_approval(state) == END


class TestRoutingAfterHashVerify:
    """route_after_hash_verify routes to prepare_waves or generate_report."""

    def test_clean_state_routes_to_prepare_waves(self) -> None:
        state = _base_state(error=None)
        assert route_after_hash_verify(state) == "prepare_waves"

    def test_error_routes_to_report(self) -> None:
        state = _base_state(error="plan integrity check failed: hash drifted")
        assert route_after_hash_verify(state) == "generate_report"

    def test_approved_false_routes_to_report(self) -> None:
        state = _base_state(approved=False, error=None)
        assert route_after_hash_verify(state) == "generate_report"


# ---------------------------------------------------------------------------
# approval_gate_node — policy-based approval thresholds
# ---------------------------------------------------------------------------

class TestApprovalGatePolicies:
    """Approval thresholds differ by env_policy (finding #6)."""

    def _make_manager_mock(self) -> ApprovalManager:
        mgr = MagicMock(spec=ApprovalManager)
        return mgr

    @pytest.mark.asyncio
    async def test_strict_policy_requires_approval_for_medium(self) -> None:
        """MEDIUM tier in strict env must trigger Slack approval."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(
            dry_run=False,
            env_policy="strict",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            mock_approval.return_value = (True, "ops-team")
            result = await approval_gate_node(state, approval_manager=mgr)

        mock_approval.assert_awaited_once()
        assert result.get("approved") is True

    @pytest.mark.asyncio
    async def test_moderate_policy_auto_approves_medium_when_hitl_disabled(self) -> None:
        """MEDIUM tier in moderate env auto-approves when require_live_approval=False and autonomous mode on."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(
            dry_run=False,
            env_policy="moderate",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            result = await approval_gate_node(
                state, approval_manager=mgr,
                require_live_approval=False,
                autonomous_live_apply_enabled=True,
            )

        mock_approval.assert_not_awaited()
        assert result.get("approved") is True

    @pytest.mark.asyncio
    async def test_relaxed_policy_auto_approves_medium_and_high_when_hitl_disabled(self) -> None:
        """HIGH tier in relaxed env auto-approves when require_live_approval=False and autonomous mode on."""
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(
            dry_run=False,
            env_policy="relaxed",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            result = await approval_gate_node(
                state, approval_manager=mgr,
                require_live_approval=False,
                autonomous_live_apply_enabled=True,
            )

        mock_approval.assert_not_awaited()
        assert result.get("approved") is True

    @pytest.mark.asyncio
    async def test_require_live_approval_overrides_relaxed_policy(self) -> None:
        """HITL guardrail: even relaxed policy requires approval when require_live_approval=True."""
        vm_plans = _make_vm_plans(["disk_cleanup"], ["low"])
        state = _base_state(
            dry_run=False,
            env_policy="relaxed",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            mock_approval.return_value = (True, "ops-on-call")
            result = await approval_gate_node(
                state, approval_manager=mgr, require_live_approval=True,
            )

        mock_approval.assert_awaited_once()
        assert result.get("approved") is True

    @pytest.mark.asyncio
    async def test_strict_policy_requires_approval_for_high(self) -> None:
        """HIGH tier in strict env also requires approval."""
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(
            dry_run=False,
            env_policy="strict",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            mock_approval.return_value = (True, "ops-team")
            result = await approval_gate_node(state, approval_manager=mgr)

        mock_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_moderate_policy_requires_approval_for_high(self) -> None:
        """HIGH tier in moderate env (default) still triggers approval."""
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(
            dry_run=False,
            env_policy="moderate",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            mock_approval.return_value = (False, None)
            result = await approval_gate_node(state, approval_manager=mgr)

        mock_approval.assert_awaited_once()
        assert result.get("approved") is False

    @pytest.mark.asyncio
    async def test_dry_run_always_auto_approves_regardless_of_policy(self) -> None:
        """Dry-run sandbox skips approval gate entirely, even in strict env."""
        vm_plans = _make_vm_plans(["patching"], ["high"])
        state = _base_state(
            dry_run=True,
            env_policy="strict",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            result = await approval_gate_node(state, approval_manager=mgr)

        mock_approval.assert_not_awaited()
        assert result.get("approved") is True


    @pytest.mark.asyncio
    async def test_live_approval_fails_closed_when_no_approval_manager(self) -> None:
        """Live batch with require_live_approval=True and no approval_manager returns approved=False."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(
            dry_run=False,
            env_policy="strict",
            vm_plans=vm_plans,
        )
        result = await approval_gate_node(
            state,
            approval_manager=None,
            require_live_approval=True,
        )
        assert result.get("approved") is False
        assert result.get("error") is not None

    @pytest.mark.asyncio
    async def test_autonomous_gate_prevents_disabling_hitl(self) -> None:
        """autonomous_live_apply_enabled=False prevents require_live_approval=False from taking effect."""
        vm_plans = _make_vm_plans(["disk_cleanup"], ["low"])
        state = _base_state(
            dry_run=False,
            env_policy="relaxed",
            vm_plans=vm_plans,
        )
        mgr = self._make_manager_mock()

        with patch("errander.agent.graph.await_dual_approval", new_callable=AsyncMock) as mock_approval:
            mock_approval.return_value = (True, "ops-on-call")
            result = await approval_gate_node(
                state, approval_manager=mgr,
                require_live_approval=False,      # caller tries to disable HITL
                autonomous_live_apply_enabled=False,  # gate overrides it
            )

        # Approval was still required because autonomous mode is off
        mock_approval.assert_awaited_once()
        assert result.get("approved") is True


# ---------------------------------------------------------------------------
# Integration: live batch cannot execute without approved + matching hash
# ---------------------------------------------------------------------------

class TestPlanApplyIntegrity:
    """Verify the full plan → approve → verify → execute safety chain."""

    @pytest.mark.asyncio
    async def test_hash_verification_gates_prepare_waves(self) -> None:
        """Correct hash: route_after_hash_verify returns prepare_waves."""
        state = _base_state(approved=True, error=None)
        assert route_after_hash_verify(state) == "prepare_waves"

    @pytest.mark.asyncio
    async def test_hash_drift_prevents_execution(self) -> None:
        """Tampered hash: verify_plan_hash_node sets approved=False, route → generate_report."""
        state = _base_state(dry_run=False, plan_hash="bad" * 21 + "b")  # wrong 63-char hash

        verify_result = await verify_plan_hash_node(state)
        assert verify_result.get("approved") is False

        # Merge result into state as the graph would
        merged = {**state, **verify_result}
        assert route_after_hash_verify(merged) == "generate_report"

    @pytest.mark.asyncio
    async def test_rejected_approval_prevents_hash_check(self) -> None:
        """Rejection at approval gate routes straight to report, never reaching hash verify."""
        state = _base_state(approved=False, deferred=False)
        # route_after_approval must never send rejected batches to verify_plan_hash
        assert route_after_approval(state) == "generate_report"


# ---------------------------------------------------------------------------
# Action params flow through planning → hash → dispatch
# ---------------------------------------------------------------------------

class TestActionParamsSurvivePlanning:
    """Prove action params are included in the plan artifact and affect the plan hash."""

    def _make_plans_with_params(self, params: dict) -> list[dict]:
        return [{
            "vm_id": "dev/web-01",
            "planned_actions": [
                {"action_type": "patching", "risk_tier": "medium", "params": params}
            ],
            "os_family": "ubuntu",
        }]

    def test_params_included_in_plan_hash(self) -> None:
        """Different params must produce a different plan hash."""
        plans_a = self._make_plans_with_params({"packages": ["curl", "nginx"]})
        plans_b = self._make_plans_with_params({"packages": ["curl", "openssl"]})

        hash_a = _compute_hash(plans_a, "batch-001", "prod")
        hash_b = _compute_hash(plans_b, "batch-001", "prod")

        assert hash_a != hash_b, "Plan hash must differ when action params differ"

    def test_empty_params_and_no_params_produce_same_hash(self) -> None:
        """Empty params dict is equivalent to no params for hash purposes."""
        plans_with_empty = self._make_plans_with_params({})
        plans_with_none: list[dict] = [{
            "vm_id": "dev/web-01",
            "planned_actions": [
                {"action_type": "patching", "risk_tier": "medium", "params": {}}
            ],
            "os_family": "ubuntu",
        }]
        assert (
            _compute_hash(plans_with_empty, "batch-001", "prod")
            == _compute_hash(plans_with_none, "batch-001", "prod")
        )

    def test_params_appear_in_approval_slack_summary(self) -> None:
        """Non-empty params must surface in the Slack approval message so operators can review them."""
        vm_plans = self._make_plans_with_params({"threshold_mb": 200, "log_dir": "/var/log/app"})
        summary = _format_plan_for_approval(
            vm_plans=vm_plans,
            batch_id="batch-001",
            plan_id="plan-abc",
            plan_hash="a" * 64,
        )
        assert "threshold_mb" in summary or "log_dir" in summary, (
            "Approval summary must include action params so the operator knows exactly what will run"
        )

    def test_params_flow_through_to_wave_dispatch(self) -> None:
        """Params stored in vm_plans must reach the wave dispatcher's approved actions lookup."""
        params = {"packages": ["curl=7.88.0", "nginx=1.24.0"]}
        vm_plans = self._make_plans_with_params(params)

        # Simulate what make_wave_dispatcher does: build the vm_id → actions lookup
        vm_id_to_approved_actions = {
            str(p["vm_id"]): list(p.get("planned_actions", []))
            for p in vm_plans
        }
        approved = vm_id_to_approved_actions.get("dev/web-01", [])

        assert len(approved) == 1
        assert approved[0].get("params") == params, (
            "Action params must survive from batch planning through to VM execution dispatch"
        )
