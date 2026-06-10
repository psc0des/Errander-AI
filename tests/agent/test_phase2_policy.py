"""Phase 2 tests: policy enforcement, fleet abort, OS verification (findings #6, #7, #8)."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.graph import (
    BatchGraphState,
    check_fleet_health_node,
    route_after_fleet_check,
    validate_targets_node,
)
from errander.db.core import AsyncDatabase
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.execution.target_validation import TargetReadiness
from errander.models.actions import Action, ActionType, RiskTier
from errander.models.events import EventType
from errander.safety.audit import AuditStore
from errander.safety.validators import validate_action

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    from datetime import datetime
    now = datetime.now(tz=UTC)
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked",
                     started_at=now, completed_at=now)


_OS_RELEASE_UBUNTU = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n'
_OS_RELEASE_RHEL = 'ID=rhel\nVERSION_ID="9.0"\nPRETTY_NAME="Red Hat Enterprise Linux 9"\n'


def _make_target(vm_id: str = "dev/web-01", os_family: str = "ubuntu") -> dict:
    return {
        "vm_id": vm_id,
        "hostname": "10.0.1.10",
        "ssh_user": "errander-ai",
        "ssh_key_path": "/key",
        "os_family": os_family,
    }


def _batch_state(**overrides) -> BatchGraphState:
    state: BatchGraphState = {  # type: ignore[typeddict-unknown-key]
        "batch_id": "batch-test",
        "healthy_targets": [],
        "failed_targets": [],
        "targets": [_make_target()],
        "dry_run": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ---------------------------------------------------------------------------
# 2.1 — Policy-aware validate_action
# ---------------------------------------------------------------------------

class TestPolicyAwareValidation:
    """validate_action() now uses the policy parameter (finding #6)."""

    @pytest.mark.asyncio
    async def test_critical_blocked_regardless_of_policy(self) -> None:
        action = Action(action_type=ActionType.DISK_CLEANUP, risk_tier=RiskTier.CRITICAL)
        for policy in ("relaxed", "moderate", "strict"):
            valid, reason = await validate_action(action, "vm-01", "ubuntu", policy=policy)
            assert not valid, f"CRITICAL should be blocked under policy={policy}"
            assert "critical" in reason.lower()
            assert policy in reason

    @pytest.mark.asyncio
    async def test_low_risk_allowed_in_all_policies(self) -> None:
        action = Action(action_type=ActionType.DISK_CLEANUP, risk_tier=RiskTier.LOW)
        for policy in ("relaxed", "moderate", "strict"):
            valid, _ = await validate_action(action, "vm-01", "ubuntu", policy=policy)
            assert valid, f"LOW risk should be allowed under policy={policy}"

    @pytest.mark.asyncio
    async def test_medium_risk_requires_approval_in_strict(self) -> None:
        """MEDIUM risk requires approval in strict policy — validate_action logs but allows."""
        action = Action(action_type=ActionType.PATCHING, risk_tier=RiskTier.MEDIUM)
        # In strict mode, MEDIUM requires approval — but validate_action allows it
        # (approval was granted at the batch gate; this function is defense-in-depth)
        valid, _ = await validate_action(action, "vm-01", "ubuntu", policy="strict")
        assert valid  # not blocked — approval already handled at batch level

    @pytest.mark.asyncio
    async def test_policy_in_rejection_reason(self) -> None:
        """Rejection reasons include the policy name for audit clarity."""
        action = Action(action_type=ActionType.DISK_CLEANUP, risk_tier=RiskTier.CRITICAL)
        valid, reason = await validate_action(action, "vm-01", "ubuntu", policy="strict")
        assert not valid
        assert "strict" in reason

    @pytest.mark.asyncio
    async def test_unknown_policy_falls_back_to_moderate(self) -> None:
        """Unknown policy names default to moderate without crashing."""
        action = Action(action_type=ActionType.DISK_CLEANUP, risk_tier=RiskTier.LOW)
        valid, _ = await validate_action(action, "vm-01", "ubuntu", policy="nonexistent")
        assert valid  # LOW is safe under any policy


# ---------------------------------------------------------------------------
# 2.2 — Fleet abort (finding #7)
# ---------------------------------------------------------------------------

class TestFleetAbort:
    """check_fleet_health_node aborts when failure rate exceeds threshold."""

    @pytest.mark.asyncio
    async def test_abort_when_all_targets_fail(self) -> None:
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(
                healthy_targets=[],
                failed_targets=[_make_target("vm-01"), _make_target("vm-02")],
            )
            result = await check_fleet_health_node(state, audit_store=store, fleet_failure_threshold=0.5)

        assert "error" in result
        assert "abort" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_abort_emits_fleet_abort_audit_event(self) -> None:
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(
                batch_id="batch-fleet-test",
                healthy_targets=[],
                failed_targets=[_make_target("vm-01"), _make_target("vm-02")],
            )
            await check_fleet_health_node(state, audit_store=store, fleet_failure_threshold=0.5)
            events = await store.get_events(batch_id="batch-fleet-test")

        fleet_events = [e for e in events if e.event_type == EventType.FLEET_ABORT]
        assert len(fleet_events) == 1
        assert fleet_events[0].metadata["failed"] == 2

    @pytest.mark.asyncio
    async def test_no_abort_when_below_threshold(self) -> None:
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(
                healthy_targets=[_make_target("vm-01"), _make_target("vm-02")],
                failed_targets=[_make_target("vm-03")],  # 1/3 = 33% < 50%
            )
            result = await check_fleet_health_node(state, audit_store=store, fleet_failure_threshold=0.5)

        assert "error" not in result or not result.get("error")

    @pytest.mark.asyncio
    async def test_no_abort_when_all_healthy(self) -> None:
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(
                healthy_targets=[_make_target("vm-01"), _make_target("vm-02")],
                failed_targets=[],
            )
            result = await check_fleet_health_node(state, audit_store=store, fleet_failure_threshold=0.5)

        assert not result.get("error")

    @pytest.mark.asyncio
    async def test_empty_targets_returns_error(self) -> None:
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(healthy_targets=[], failed_targets=[])
            result = await check_fleet_health_node(state, audit_store=store)

        assert "error" in result

    def test_route_after_fleet_check_abort_to_report(self) -> None:
        state = _batch_state(error="Fleet pre-flight abort: 5/5 targets failed")
        assert route_after_fleet_check(state) == "generate_report"

    def test_route_after_fleet_check_continues_on_healthy(self) -> None:
        state = _batch_state(healthy_targets=[_make_target()], error=None)
        assert route_after_fleet_check(state) == "plan_vms"

    def test_route_after_fleet_check_no_healthy_to_report(self) -> None:
        state = _batch_state(healthy_targets=[], error=None)
        assert route_after_fleet_check(state) == "generate_report"

    @pytest.mark.asyncio
    async def test_threshold_boundary_inclusive(self) -> None:
        """Exactly at threshold does NOT abort (> threshold aborts, not >=)."""
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            state = _batch_state(
                healthy_targets=[_make_target("vm-01")],
                failed_targets=[_make_target("vm-02")],  # 1/2 = 50% == threshold
            )
            result = await check_fleet_health_node(state, audit_store=store, fleet_failure_threshold=0.5)

        # 0.5 > 0.5 is False → no abort
        assert not result.get("error")


# ---------------------------------------------------------------------------
# 2.3 — OS verification (finding #8)
# ---------------------------------------------------------------------------

class TestOSVerification:
    """validate_targets_node detects OS and verifies it matches inventory."""

    @pytest.mark.asyncio
    async def test_ubuntu_match_is_healthy(self) -> None:
        ssh = SSHConnectionManager()
        ready = TargetReadiness(vm_id="dev/web-01", hostname="10.0.1.10")
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with (
                patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(_OS_RELEASE_UBUNTU))),
                patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=ready)),
            ):
                state = _batch_state(targets=[_make_target(os_family="ubuntu")])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        assert len(result["healthy_targets"]) == 1
        assert result["failed_targets"] == []
        assert result["healthy_targets"][0]["os_family"] == "ubuntu"

    @pytest.mark.asyncio
    async def test_os_mismatch_goes_to_failed(self) -> None:
        """Inventory declares 'ubuntu' but host runs 'rhel' → OS_MISMATCH."""
        ssh = SSHConnectionManager()
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(_OS_RELEASE_RHEL))):
                # Declare ubuntu, but host returns rhel os-release
                state = _batch_state(targets=[_make_target(os_family="ubuntu")])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1

    @pytest.mark.asyncio
    async def test_os_mismatch_logs_audit_event(self) -> None:
        """OS_MISMATCH emits an audit event with declared vs detected."""
        ssh = SSHConnectionManager()
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(_OS_RELEASE_RHEL))):
                state = _batch_state(batch_id="batch-os-test", targets=[_make_target(os_family="ubuntu")])
                await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

            events = await store.get_events(batch_id="batch-os-test")

        mismatch_events = [e for e in events if e.event_type == EventType.OS_MISMATCH]
        assert len(mismatch_events) == 1
        assert mismatch_events[0].metadata["declared"] == "ubuntu"
        assert mismatch_events[0].metadata["detected"] == "rhel"

    @pytest.mark.asyncio
    async def test_unsupported_os_goes_to_failed(self) -> None:
        """os-release with unknown ID goes to failed_targets."""
        ssh = SSHConnectionManager()
        unknown_release = 'ID=freebsd\nVERSION_ID="13.0"\n'
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(unknown_release))):
                state = _batch_state(targets=[_make_target(os_family="ubuntu")])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1

    @pytest.mark.asyncio
    async def test_ssh_failure_goes_to_failed(self) -> None:
        """SSH connection error goes to failed_targets."""
        ssh = SSHConnectionManager()
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch.object(ssh, "execute", AsyncMock(side_effect=ConnectionError("refused"))):
                state = _batch_state(targets=[_make_target()])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        assert result["healthy_targets"] == []
        assert len(result["failed_targets"]) == 1

    @pytest.mark.asyncio
    async def test_detected_os_stored_in_target(self) -> None:
        """Detected OS family and version are stored in the target dict."""
        ssh = SSHConnectionManager()
        ready = TargetReadiness(vm_id="dev/web-01", hostname="10.0.1.10")
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with (
                patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(_OS_RELEASE_UBUNTU))),
                patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=ready)),
            ):
                state = _batch_state(targets=[_make_target(os_family="ubuntu")])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        target = result["healthy_targets"][0]
        assert target["os_family"] == "ubuntu"
        assert "os_version" in target

    @pytest.mark.asyncio
    async def test_rhel_declared_and_detected_is_healthy(self) -> None:
        """rhel declared + rhel detected → healthy."""
        ssh = SSHConnectionManager()
        ready = TargetReadiness(vm_id="dev/web-01", hostname="10.0.1.10")
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with (
                patch.object(ssh, "execute", AsyncMock(return_value=_ssh_result(_OS_RELEASE_RHEL))),
                patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=ready)),
            ):
                state = _batch_state(targets=[_make_target(os_family="rhel")])
                result = await validate_targets_node(state, ssh_manager=ssh, audit_store=store)

        assert len(result["healthy_targets"]) == 1
        assert result["failed_targets"] == []
