"""Behavior tests for sudo_preflight_node and routing.

Per ai_sre_audit_v2.md Phase A.4 — the SRE explicitly asked for a test that
proves missing sudo causes a failed action, not silent success.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from errander.agent.vm_graph import sudo_preflight_node, route_after_sudo_preflight
from errander.models.events import EventType


# --- Routing tests ---

def test_route_after_preflight_routes_to_audit_on_error() -> None:
    assert route_after_sudo_preflight({"error": "anything"}) == "audit_results"


def test_route_after_preflight_routes_to_dispatch_when_clear() -> None:
    assert route_after_sudo_preflight({}) == "dispatch_action"


# --- Behavior tests ---

@pytest.mark.asyncio
async def test_dry_run_skips_preflight() -> None:
    state = {"dry_run": True, "vm_id": "v1", "planned_actions": [{"action_type": "patching"}]}
    executor = MagicMock()
    executor.execute = AsyncMock()
    result = await sudo_preflight_node(state, executor=executor)
    assert result == {}
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_passes_when_all_ok() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" not in result
    executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_fails_closed_when_binary_fails() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_FAIL /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" in result
    assert "journalctl" in result["error"]


@pytest.mark.asyncio
async def test_preflight_fails_on_ssh_error() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=False, stdout="", stderr="SSH connection refused")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" in result


@pytest.mark.asyncio
async def test_preflight_emits_sudo_preflight_failed_event() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "batch_id": "b1",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_FAIL /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()

    await sudo_preflight_node(state, executor=executor, audit_store=audit_store)

    audit_store.log_event.assert_awaited_once()
    logged_event = audit_store.log_event.await_args.args[0]
    assert logged_event.event_type == EventType.SUDO_PREFLIGHT_FAILED


# --- Regression / contract tests ---

def test_no_env_in_privileged_apt_commands() -> None:
    """SRE-explicit: /usr/bin/env must not appear in privileged apt commands."""
    from errander.execution.commands import AptManager
    apt = AptManager()
    install_cmd = apt.install_version("nginx", "1.18.0-0ubuntu1")
    assert "/usr/bin/env" not in install_cmd
    assert "DEBIAN_FRONTEND" not in install_cmd


def test_apt_simulate_no_sudo() -> None:
    """Dry-run simulate command should not require sudo escalation."""
    from errander.execution.commands import AptManager
    apt = AptManager()
    cmd = apt.simulate_upgrade()
    assert "sudo" not in cmd


# --- Registry-driven tests ---

@pytest.mark.asyncio
async def test_missing_binary_emits_sudo_preflight_failed_not_target_preflight_failed() -> None:
    """Missing system binary → SUDO_PREFLIGHT_FAILED, not TARGET_PREFLIGHT_FAILED."""
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "batch_id": "b1",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(
        success=True,
        stdout="SUDO_FAIL /usr/bin/journalctl\n",
        stderr="",
    )
    executor.execute = AsyncMock(return_value=mock_result)
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()

    result = await sudo_preflight_node(state, executor=executor, audit_store=audit_store)

    assert "error" in result
    logged_events = [c.args[0] for c in audit_store.log_event.await_args_list]
    event_types = [e.event_type for e in logged_events]
    assert EventType.SUDO_PREFLIGHT_FAILED in event_types
    assert EventType.TARGET_PREFLIGHT_FAILED not in event_types
