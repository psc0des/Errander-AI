"""Tests for the plan/apply flow (finding #3 from SRE audit).

Covers:
- verify_plan_hash_node: correct hash passes, tampered hash aborts
- route_after_approval: approved → verify_plan_hash, rejected → generate_report
- route_after_hash_verify: ok → prepare_waves, drift → generate_report
- approval_gate_node policy thresholds: strict/moderate/relaxed
- Live batch cannot reach execution without approved=True + matching plan_hash
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest

from errander.agent.graph import (
    BatchGraphState,
    _format_plan_for_approval,
    approval_gate_node,
    route_after_approval,
    route_after_hash_verify,
    verify_plan_hash_node,
)
from errander.safety.approval_store import ApprovalRequestStore

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
            strict=False,
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
# approval_gate_node — policy-based approval thresholds (durable store, R3)
# ---------------------------------------------------------------------------

def _make_store() -> ApprovalRequestStore:
    from tests.conftest import make_test_db
    return ApprovalRequestStore(make_test_db())


def _decide_when_pending(
    store: ApprovalRequestStore,
    approved: bool,
    decided_by: str,
) -> asyncio.Task[None]:
    """Background operator: decides the request once the gate persists it."""

    async def _decider() -> None:
        for _ in range(200):
            pending = await store.get_pending()
            if pending:
                await store.decide(
                    pending[0].batch_id, approved=approved, decided_by=decided_by,
                )
                return
            await asyncio.sleep(0.01)

    return asyncio.create_task(_decider())


class TestApprovalGatePolicies:
    """Approval thresholds differ by env_policy (finding #6)."""

    @pytest.mark.asyncio
    async def test_strict_policy_requires_approval_for_medium(self) -> None:
        """MEDIUM tier in strict env must persist an approval request."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(dry_run=False, env_policy="strict", vm_plans=vm_plans)
        store = _make_store()
        decider = _decide_when_pending(store, approved=True, decided_by="ui:ops-team")

        result = await approval_gate_node(state, approval_store=store)
        await decider

        assert result.get("approved") is True
        req = await store.get("batch-test-001")
        assert req is not None and req.status == "approved"
        assert req.decided_by == "ui:ops-team"
        # Execution was claimed atomically before proceeding
        assert req.execution_started_at is not None

    @pytest.mark.asyncio
    async def test_moderate_policy_auto_approves_medium_when_hitl_disabled(self) -> None:
        """MEDIUM tier in moderate env auto-approves when require_live_approval=False and autonomous mode on."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(dry_run=False, env_policy="moderate", vm_plans=vm_plans)
        store = _make_store()

        result = await approval_gate_node(
            state, approval_store=store,
            require_live_approval=False,
            autonomous_live_apply_enabled=True,
        )

        assert result.get("approved") is True
        # No approval request was ever persisted — auto-approved below threshold
        assert await store.get("batch-test-001") is None

    @pytest.mark.asyncio
    async def test_relaxed_policy_auto_approves_high_when_hitl_disabled(self) -> None:
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(dry_run=False, env_policy="relaxed", vm_plans=vm_plans)
        store = _make_store()

        result = await approval_gate_node(
            state, approval_store=store,
            require_live_approval=False,
            autonomous_live_apply_enabled=True,
        )

        assert result.get("approved") is True
        assert await store.get("batch-test-001") is None

    @pytest.mark.asyncio
    async def test_require_live_approval_overrides_relaxed_policy(self) -> None:
        vm_plans = _make_vm_plans(["disk_cleanup"], ["low"])
        state = _base_state(dry_run=False, env_policy="relaxed", vm_plans=vm_plans)
        store = _make_store()
        decider = _decide_when_pending(store, approved=True, decided_by="ui:ops-on-call")

        result = await approval_gate_node(
            state, approval_store=store, require_live_approval=True,
        )
        await decider

        assert result.get("approved") is True
        req = await store.get("batch-test-001")
        assert req is not None and req.status == "approved"

    @pytest.mark.asyncio
    async def test_strict_policy_requires_approval_for_high(self) -> None:
        """HIGH tier in strict env also requires approval."""
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(dry_run=False, env_policy="strict", vm_plans=vm_plans)
        store = _make_store()
        decider = _decide_when_pending(store, approved=True, decided_by="ui:ops-team")

        await approval_gate_node(state, approval_store=store)
        await decider

        req = await store.get("batch-test-001")
        assert req is not None and req.is_decided()

    @pytest.mark.asyncio
    async def test_moderate_policy_requires_approval_for_high(self) -> None:
        """HIGH tier in moderate env (default) still triggers approval; timeout rejects."""
        vm_plans = _make_vm_plans(["backup_verify"], ["high"])
        state = _base_state(dry_run=False, env_policy="moderate", vm_plans=vm_plans)
        store = _make_store()

        # Nobody decides — timeout_seconds=0 auto-rejects via the store
        result = await approval_gate_node(
            state, approval_store=store, approval_timeout_seconds=0,
        )

        assert result.get("approved") is False
        req = await store.get("batch-test-001")
        assert req is not None and req.status == "timeout"

    @pytest.mark.asyncio
    async def test_dry_run_always_auto_approves_regardless_of_policy(self) -> None:
        """Dry-run sandbox skips approval gate entirely, even in strict env."""
        vm_plans = _make_vm_plans(["patching"], ["high"])
        state = _base_state(dry_run=True, env_policy="strict", vm_plans=vm_plans)
        store = _make_store()

        result = await approval_gate_node(state, approval_store=store)

        assert result.get("approved") is True
        assert await store.get("batch-test-001") is None

    @pytest.mark.asyncio
    async def test_live_approval_fails_closed_when_no_approval_store(self) -> None:
        """Live batch with require_live_approval=True and no approval_store returns approved=False."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(dry_run=False, env_policy="strict", vm_plans=vm_plans)
        result = await approval_gate_node(
            state,
            approval_store=None,
            require_live_approval=True,
        )
        assert result.get("approved") is False
        assert result.get("error") is not None

    @pytest.mark.asyncio
    async def test_autonomous_gate_prevents_disabling_hitl(self) -> None:
        """autonomous_live_apply_enabled=False prevents require_live_approval=False from taking effect."""
        vm_plans = _make_vm_plans(["disk_cleanup"], ["low"])
        state = _base_state(dry_run=False, env_policy="relaxed", vm_plans=vm_plans)
        store = _make_store()
        decider = _decide_when_pending(store, approved=True, decided_by="ui:ops-on-call")

        result = await approval_gate_node(
            state, approval_store=store,
            require_live_approval=False,      # caller tries to disable HITL
            autonomous_live_apply_enabled=False,  # gate overrides it
        )
        await decider

        # Approval was still required because autonomous mode is off
        assert result.get("approved") is True
        req = await store.get("batch-test-001")
        assert req is not None and req.status == "approved"

    @pytest.mark.asyncio
    async def test_rejection_recorded_and_not_claimed(self) -> None:
        """A rejected request is never claimed for execution."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(dry_run=False, env_policy="strict", vm_plans=vm_plans)
        store = _make_store()
        decider = _decide_when_pending(store, approved=False, decided_by="ui:ops-team")

        result = await approval_gate_node(state, approval_store=store)
        await decider

        assert result.get("approved") is False
        req = await store.get("batch-test-001")
        assert req is not None
        assert req.status == "rejected"
        assert req.execution_started_at is None

    @pytest.mark.asyncio
    async def test_per_item_selection_flows_into_state(self) -> None:
        """approved_items recorded by the UI become operator_approved_packages."""
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        state = _base_state(dry_run=False, env_policy="strict", vm_plans=vm_plans)
        store = _make_store()

        async def _decide_with_items() -> None:
            for _ in range(200):
                pending = await store.get_pending()
                if pending:
                    await store.decide(
                        pending[0].batch_id, approved=True, decided_by="ui:admin",
                        approved_items=[{
                            "vm_id": "dev/web-01",
                            "action_type": "patching",
                            "packages": [{"name": "openssl", "current": "1", "target": "2"}],
                        }],
                    )
                    return
                await asyncio.sleep(0.01)

        decider = asyncio.create_task(_decide_with_items())
        result = await approval_gate_node(state, approval_store=store)
        await decider

        assert result.get("approved") is True
        op_pkgs = result.get("operator_approved_packages")
        assert op_pkgs == {"dev/web-01": [{"name": "openssl", "current": "1", "target": "2"}]}


# ---------------------------------------------------------------------------
# _format_plan_for_approval — smoke check kept from the original suite
# ---------------------------------------------------------------------------

class TestFormatPlanForApproval:
    def test_includes_hash_and_plan_id(self) -> None:
        vm_plans = _make_vm_plans(["patching"], ["medium"])
        text = _format_plan_for_approval(vm_plans, "batch-x", "plan-y", "f" * 64)
        assert "batch-x" in text
        assert "plan-y" in text
        assert ("f" * 16) in text
