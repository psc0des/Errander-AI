"""Regression tests: disabled actions must not appear in the plan.

Finding 1 from SRE audit: prioritize_actions() was called without the
inventory-enabled action list, allowing disabled actions (e.g. docker_prune)
to appear in planned batches.

These tests verify the wire-up at two levels:
- prioritize_actions() correctly excludes disabled actions when passed a
  filtered available_actions list.
- plan_vm_node() reads enabled_actions from state and passes it through.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Send

from errander.agent.decisions import DEFAULT_PRIORITY, prioritize_actions
from errander.agent.graph import BatchGraphState, plan_vm_node, route_plan_vms
from errander.models.actions import ActionType
from errander.models.vm import OSFamily, VMInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vm_info(**overrides: Any) -> VMInfo:
    defaults: dict[str, Any] = {
        "os_family": OSFamily.UBUNTU,
        "os_version": "Ubuntu 22.04.3 LTS",
        "disk_usage": {"/": 60.0},
        "docker_available": True,
        "pending_packages": 5,
        "uptime_seconds": 86400.0,
    }
    defaults.update(overrides)
    return VMInfo(**defaults)


def _make_ssh_manager(docker_available: bool = True) -> MagicMock:
    ssh = MagicMock()

    async def _exec(*args: object, **kwargs: object) -> MagicMock:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        if "docker info" in cmd:
            return MagicMock(success=docker_available, stdout="")
        if "apt list" in cmd or "dnf check-update" in cmd or "dpkg" in cmd:
            return MagicMock(success=True, stdout="pkg1/focal 1.0 amd64 [upgradable]\n")
        if "df -h" in cmd or "df " in cmd:
            return MagicMock(success=True, stdout="/dev/sda1 50G 30G 20G 60%\n")
        if "uname" in cmd or "lsb_release" in cmd or "cat /etc/os-release" in cmd:
            return MagicMock(success=True, stdout="Ubuntu 22.04\n")
        return MagicMock(success=True, stdout="")

    ssh.execute = AsyncMock(side_effect=_exec)
    return ssh


# ---------------------------------------------------------------------------
# Tests: prioritize_actions respects available_actions filter
# ---------------------------------------------------------------------------

class TestPrioritizeActionsEnforcement:
    """prioritize_actions() must only return actions from available_actions."""

    @pytest.mark.asyncio
    async def test_docker_disabled_not_in_plan(self) -> None:
        """docker_prune.enabled: false must not produce docker_prune in the plan."""
        vm = _make_vm_info(docker_available=True)
        available = [
            ActionType.BACKUP_VERIFY,
            ActionType.DISK_CLEANUP,
            ActionType.LOG_ROTATION,
            ActionType.PATCHING,
        ]
        actions = await prioritize_actions(vm, available_actions=available)
        action_types = [a.action_type for a in actions]
        assert ActionType.DOCKER_PRUNE not in action_types, (
            "docker_prune must not be planned when excluded from available_actions"
        )

    @pytest.mark.asyncio
    async def test_only_explicitly_enabled_actions_planned(self) -> None:
        """If only disk_cleanup is enabled, plan must contain only disk_cleanup."""
        vm = _make_vm_info(docker_available=True, pending_packages=10)
        actions = await prioritize_actions(
            vm, available_actions=[ActionType.DISK_CLEANUP]
        )
        action_types = [a.action_type for a in actions]
        assert action_types == [ActionType.DISK_CLEANUP]
        assert ActionType.PATCHING not in action_types
        assert ActionType.DOCKER_PRUNE not in action_types

    @pytest.mark.asyncio
    async def test_backup_verify_disabled_not_in_plan(self) -> None:
        vm = _make_vm_info()
        available = [ActionType.DISK_CLEANUP, ActionType.LOG_ROTATION, ActionType.PATCHING]
        actions = await prioritize_actions(vm, available_actions=available)
        action_types = [a.action_type for a in actions]
        assert ActionType.BACKUP_VERIFY not in action_types

    @pytest.mark.asyncio
    async def test_service_restart_never_in_default_priority(self) -> None:
        """service_restart is operator-triggered — must never be in DEFAULT_PRIORITY."""
        assert ActionType.SERVICE_RESTART not in DEFAULT_PRIORITY


# ---------------------------------------------------------------------------
# Tests: plan_vm_node wires enabled_actions through to prioritize_actions
# ---------------------------------------------------------------------------

class TestPlanVmNodeEnabledActions:
    """plan_vm_node must pass state['enabled_actions'] to prioritize_actions."""

    @pytest.mark.asyncio
    async def test_docker_prune_excluded_when_not_in_enabled_actions(self) -> None:
        """Regression: docker_prune must not appear when state excludes it."""
        ssh = _make_ssh_manager(docker_available=True)

        _captured: dict[str, Any] = {}

        async def _fake_prioritize(vm_info: Any, **kwargs: Any) -> list[Any]:
            _captured["available_actions"] = kwargs.get("available_actions")
            # Return a real Action so the node doesn't error
            from errander.models.actions import Action, RiskTier
            return [Action(
                action_type=ActionType.DISK_CLEANUP,
                risk_tier=RiskTier.LOW,
                params={},
            )]

        state: dict[str, Any] = {
            "vm_id": "dev/web-01",
            "hostname": "10.0.20.10",
            "ssh_user": "devops",
            "ssh_key_path": "~/.ssh/errander_dev",
            "os_family": "ubuntu",
            "env_policy": "moderate",
            "batch_id": "batch-test-001",
            "ai_db_path": ":memory:",
            "enabled_actions": [
                "patching", "disk_cleanup", "log_rotation", "backup_verify"
            ],  # docker_prune deliberately excluded
        }

        from errander.agent.decisions import StoredSignalContext
        with (
            patch("errander.agent.graph.prioritize_actions", side_effect=_fake_prioritize),
            patch("errander.agent.graph.detect_os", new_callable=AsyncMock) as mock_detect,
            patch("errander.agent.graph._load_stored_signals", new_callable=AsyncMock) as mock_signals,
        ):
            mock_detect.return_value = _make_vm_info()
            mock_signals.return_value = StoredSignalContext()
            await plan_vm_node(
                state,
                ssh_manager=ssh,
                llm_client=None,
                ai_decision_store=None,
                audit_store=None,
            )

        passed = _captured.get("available_actions")
        assert passed is not None, "plan_vm_node must pass available_actions to prioritize_actions"
        passed_values = [a.value for a in passed]
        assert "docker_prune" not in passed_values, (
            "docker_prune must be excluded when not in state['enabled_actions']"
        )

    @pytest.mark.asyncio
    async def test_none_enabled_actions_uses_default_priority(self) -> None:
        """When enabled_actions is absent from state, prioritize_actions gets None (fallback)."""
        ssh = _make_ssh_manager()

        _captured: dict[str, Any] = {}

        async def _fake_prioritize(vm_info: Any, **kwargs: Any) -> list[Any]:
            _captured["available_actions"] = kwargs.get("available_actions")
            return []

        state: dict[str, Any] = {
            "vm_id": "dev/web-01",
            "hostname": "10.0.20.10",
            "ssh_user": "devops",
            "ssh_key_path": "~/.ssh/errander_dev",
            "os_family": "ubuntu",
            "env_policy": "moderate",
            "batch_id": "batch-test-001",
            "ai_db_path": ":memory:",
            # enabled_actions intentionally absent
        }

        from errander.agent.decisions import StoredSignalContext
        with (
            patch("errander.agent.graph.prioritize_actions", side_effect=_fake_prioritize),
            patch("errander.agent.graph.detect_os", new_callable=AsyncMock) as mock_detect,
            patch("errander.agent.graph._load_stored_signals", new_callable=AsyncMock) as mock_signals,
        ):
            mock_detect.return_value = _make_vm_info()
            mock_signals.return_value = StoredSignalContext()
            await plan_vm_node(
                state,
                ssh_manager=ssh,
                llm_client=None,
                ai_decision_store=None,
                audit_store=None,
            )

        assert _captured.get("available_actions") is None, (
            "When enabled_actions is absent from state, available_actions must be None "
            "(falls back to DEFAULT_PRIORITY in prioritize_actions)"
        )


# ---------------------------------------------------------------------------
# Tests: route_plan_vms includes enabled_actions in every Send payload
# (graph-level regression — the actual LangGraph fan-out path)
# ---------------------------------------------------------------------------

class TestRoutePlanVms:
    """route_plan_vms must pass enabled_actions through the LangGraph Send payload."""

    def _make_state(self, **overrides: Any) -> BatchGraphState:
        state: BatchGraphState = {
            "healthy_targets": [
                {"vm_id": "dev/web-01", "hostname": "10.0.20.10",
                 "ssh_user": "devops", "ssh_key_path": "~/.ssh/k"},
            ],
            "batch_id": "batch-test-001",
            "env_policy": "moderate",
            "enabled_actions": ["patching", "disk_cleanup", "log_rotation", "backup_verify"],
        }
        state.update(overrides)  # type: ignore[typeddict-item]
        return state

    def test_send_payload_includes_enabled_actions(self) -> None:
        """Every Send payload must carry enabled_actions from batch state."""
        state = self._make_state()
        result = route_plan_vms(state)
        assert isinstance(result, list)
        assert len(result) == 1
        send = result[0]
        assert isinstance(send, Send)
        assert "enabled_actions" in send.arg, (
            "Send payload must include 'enabled_actions' so plan_vm_node can filter planning"
        )
        assert send.arg["enabled_actions"] == [
            "patching", "disk_cleanup", "log_rotation", "backup_verify"
        ]

    def test_docker_prune_disabled_excluded_from_send_payload(self) -> None:
        """When docker_prune is not in enabled_actions, Send payload must not include it."""
        state = self._make_state(
            enabled_actions=["patching", "disk_cleanup", "log_rotation", "backup_verify"]
        )
        result = route_plan_vms(state)
        assert isinstance(result, list)
        send_payload = result[0].arg
        assert "docker_prune" not in send_payload["enabled_actions"]

    def test_empty_healthy_targets_returns_generate_report(self) -> None:
        """No healthy targets → route to generate_report, not a fan-out."""
        state = self._make_state(healthy_targets=[])
        result = route_plan_vms(state)
        assert result == "generate_report"

    def test_multiple_vms_each_get_enabled_actions(self) -> None:
        """All VMs in the fan-out must receive the same enabled_actions list."""
        state = self._make_state(
            healthy_targets=[
                {"vm_id": "dev/web-01", "hostname": "h1", "ssh_user": "u", "ssh_key_path": "k"},
                {"vm_id": "dev/web-02", "hostname": "h2", "ssh_user": "u", "ssh_key_path": "k"},
            ],
            enabled_actions=["disk_cleanup", "log_rotation"],
        )
        result = route_plan_vms(state)
        assert isinstance(result, list)
        assert len(result) == 2
        for send in result:
            assert send.arg["enabled_actions"] == ["disk_cleanup", "log_rotation"]

    def test_missing_enabled_actions_omits_key_from_send(self) -> None:
        """When enabled_actions is absent from state, Send payload must NOT include it.

        This ensures plan_vm_node falls back to DEFAULT_PRIORITY rather than
        receiving an empty list and planning zero actions.
        """
        state = self._make_state()
        del state["enabled_actions"]
        result = route_plan_vms(state)
        assert isinstance(result, list)
        assert "enabled_actions" not in result[0].arg, (
            "When enabled_actions is absent from batch state, it must be omitted "
            "from the Send payload so plan_vm_node uses DEFAULT_PRIORITY fallback"
        )
