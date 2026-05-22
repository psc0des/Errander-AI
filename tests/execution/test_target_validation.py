"""Tests for target_validation — per-VM readiness probes for --check-targets CLI."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.execution.target_validation import (
    TargetReadiness,
    check_target,
    render_readiness_report,
)


@pytest.mark.asyncio
async def test_all_binaries_present_and_sudo_ok() -> None:
    ssh = MagicMock()

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = args[4] if len(args) > 4 else kwargs.get("command", "")
        if "command -v" in str(cmd):
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert r.verdict == "ready"
    assert r.issues == []


@pytest.mark.asyncio
async def test_missing_binary_blocks() -> None:
    ssh = MagicMock()

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        if "command -v /usr/bin/apt-get" in cmd:
            return MagicMock(success=True, stdout="missing\n")
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert r.verdict == "blocked"
    assert any("apt-get" in issue for issue in r.issues)


@pytest.mark.asyncio
async def test_docker_hygiene_wrapper_probed_when_enabled() -> None:
    """docker_hygiene wrapper scripts are probed when enabled_actions includes docker_hygiene."""
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="wrapper",
        ssh_manager=ssh,
        enabled_actions=["docker_hygiene"],
    )
    assert any("errander-docker-assess-v2 --check" in c for c in calls)
    assert any("errander-docker-remove-v2 --check" in c for c in calls)


@pytest.mark.asyncio
async def test_disabled_mode_skips_docker_checks() -> None:
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert not any("docker" in c for c in calls)


@pytest.mark.asyncio
async def test_enabled_actions_skips_disabled_action_binaries() -> None:
    """When patching is not in enabled_actions, apt-get must not be checked."""
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
        enabled_actions=["disk_cleanup", "log_rotation"],  # patching disabled
    )
    assert r.verdict == "ready"
    assert not any("apt-get" in c for c in calls), "apt-get must not be checked when patching is disabled"
    assert not any("apt-mark" in c for c in calls)
    assert any("logrotate" in c for c in calls)


@pytest.mark.asyncio
async def test_enabled_actions_docker_wrapper_not_checked_when_disabled() -> None:
    """docker_hygiene not in enabled_actions + mode=wrapper must skip wrapper checks."""
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="wrapper",
        ssh_manager=ssh,
        enabled_actions=["disk_cleanup", "patching"],  # docker_hygiene absent
    )
    # docker_command_mode=wrapper is irrelevant when docker_prune is not enabled —
    # the binary filtering path returns no docker entries, so no wrapper checks run.
    # (The wrapper check in check_target fires only when mode=="wrapper" AND the
    # docker wrapper binary is in the checked set — it is not, so no call is made.)
    assert not any("errander-docker" in c for c in calls)


@pytest.mark.asyncio
async def test_service_restart_wrapper_probed_when_enabled() -> None:
    """When service_restart is enabled, errander-systemctl-restart --check must be called."""
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
        enabled_actions=["disk_cleanup", "service_restart"],
    )
    assert r.verdict == "ready"
    assert any("errander-systemctl-restart --check" in c for c in calls), (
        "errander-systemctl-restart --check must be called when service_restart is enabled"
    )


@pytest.mark.asyncio
async def test_service_restart_wrapper_not_probed_when_disabled() -> None:
    """When service_restart is absent from enabled_actions, wrapper must not be probed."""
    ssh = MagicMock()
    calls: list[str] = []

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
        enabled_actions=["disk_cleanup", "patching", "log_rotation"],
    )
    assert not any("errander-systemctl-restart" in c for c in calls), (
        "errander-systemctl-restart must not be probed when service_restart is not enabled"
    )


@pytest.mark.asyncio
async def test_service_restart_wrapper_fail_blocks_verdict() -> None:
    """A failing wrapper --check must add an issue and set verdict to blocked."""
    ssh = MagicMock()

    def fake_exec(*args: object, **kwargs: object) -> object:
        cmd = str(args[4] if len(args) > 4 else kwargs.get("command", ""))
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        if "errander-systemctl-restart --check" in cmd:
            return MagicMock(success=False, stdout="")
        return MagicMock(success=True, stdout="ok\n")

    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
        enabled_actions=["disk_cleanup", "service_restart"],
    )
    assert r.verdict == "blocked"
    assert any("errander-systemctl-restart" in issue for issue in r.issues)


def test_render_readiness_report_format() -> None:
    results = [
        TargetReadiness(vm_id="v1", hostname="h1", verdict="ready"),
        TargetReadiness(vm_id="v2", hostname="h2", verdict="blocked",
                        issues=["missing binary: /usr/bin/apt-get"]),
    ]
    output = render_readiness_report(results)
    assert "v1" in output
    assert "v2" in output
    assert "ready" in output
    assert "blocked" in output
    assert "1 ready, 1 blocked" in output
