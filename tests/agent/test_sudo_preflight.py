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


# --- Docker command mode preflight tests ---

@pytest.mark.asyncio
async def test_wrapper_mode_preflight_checks_wrapper_paths() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "wrapper",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/local/sbin/errander-docker-assess\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    called_cmd = executor.execute.await_args.kwargs.get("command") or ""
    assert "errander-docker-assess" in called_cmd
    assert "/usr/bin/docker" not in called_cmd


@pytest.mark.asyncio
async def test_direct_sudo_mode_preflight_checks_usr_bin_docker() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "direct_sudo",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/docker\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    called_cmd = executor.execute.await_args.kwargs.get("command") or ""
    assert "/usr/bin/docker" in called_cmd


@pytest.mark.asyncio
async def test_direct_sudo_mode_emits_warning_audit_event() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "direct_sudo",
        "batch_id": "b1",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/docker\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()
    await sudo_preflight_node(state, executor=executor, audit_store=audit_store)
    # At least one logged event should contain the direct_sudo warning
    logged_events = [c.args[0] for c in audit_store.log_event.await_args_list]
    assert any("direct_sudo" in (e.detail or "") for e in logged_events)


@pytest.mark.asyncio
async def test_disabled_mode_preflight_skips_docker() -> None:
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "disabled",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    # Either executor was not called at all (no other actions), or docker paths absent
    if executor.execute.await_count > 0:
        called_cmd = executor.execute.await_args.kwargs.get("command") or ""
        assert "docker" not in called_cmd
