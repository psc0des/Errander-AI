"""Tests for target_validation — per-VM readiness probes for --check-targets CLI."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

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
async def test_wrapper_mode_checks_wrapper_scripts() -> None:
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
    )
    assert any("errander-docker-assess --check" in c for c in calls)
    assert any("errander-docker-prune-safe --check" in c for c in calls)
    assert any("errander-docker-prune-aggressive --check" in c for c in calls)


@pytest.mark.asyncio
async def test_direct_sudo_mode_checks_raw_docker() -> None:
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
        os_family="ubuntu", docker_command_mode="direct_sudo",
        ssh_manager=ssh,
    )
    assert any("sudo -n /usr/bin/docker version" in c for c in calls)


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
