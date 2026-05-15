"""Tests for docker_command_mode dispatch in docker_prune sub-graph."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from errander.agent.subgraphs.docker_prune import (
    assess_node,
    execute_node,
    parse_assess_output,
    validate_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus


def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor(dry_run: bool = False) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


def _base_state(**overrides: object) -> dict:  # type: ignore[type-arg]
    state = {
        "vm_id": "v1",
        "os_family": "ubuntu",
        "dry_run": False,
        "status": ActionStatus.PENDING.value,
        "docker_available": True,
        "hostname": "h",
        "username": "u",
        "key_path": "k",
    }
    state.update(overrides)
    return state


# --- Mode dispatch ---

def test_disabled_mode_skips_validation() -> None:
    state = _base_state(docker_command_mode="disabled")
    result = validate_node(state)  # type: ignore[arg-type]
    assert result["status"] == ActionStatus.SKIPPED.value


def test_wrapper_mode_passes_validation() -> None:
    state = _base_state(docker_command_mode="wrapper")
    result = validate_node(state)  # type: ignore[arg-type]
    assert result["status"] == ActionStatus.PENDING.value


def test_direct_sudo_mode_passes_validation() -> None:
    state = _base_state(docker_command_mode="direct_sudo")
    result = validate_node(state)  # type: ignore[arg-type]
    assert result["status"] == ActionStatus.PENDING.value


def test_wrapper_mode_default_when_not_set() -> None:
    # No docker_command_mode → defaults to wrapper, which passes validation
    state = _base_state()  # no mode key
    result = validate_node(state)  # type: ignore[arg-type]
    assert result["status"] == ActionStatus.PENDING.value


@pytest.mark.asyncio
async def test_wrapper_mode_calls_assess_wrapper() -> None:
    executor = _make_executor()
    calls: list[str] = []

    async def capture(*args: object, **kwargs: object) -> SSHResult:
        cmd = str(kwargs.get("command", ""))
        calls.append(cmd)
        return _make_result(
            "reachable=yes\ndangling_images=2\nstopped_containers=1\nerror=\n"
            "system_df_begin\n\nsystem_df_end\n"
        )

    with patch.object(executor, "execute", side_effect=capture):
        await assess_node(_base_state(docker_command_mode="wrapper"), executor=executor)  # type: ignore[arg-type]

    assert any("errander-docker-assess" in c for c in calls)
    assert not any("/usr/bin/docker info" in c for c in calls)


@pytest.mark.asyncio
async def test_direct_sudo_mode_calls_raw_docker() -> None:
    executor = _make_executor()
    calls: list[str] = []
    call_count = 0

    async def capture(*args: object, **kwargs: object) -> SSHResult:
        nonlocal call_count
        call_count += 1
        cmd = str(kwargs.get("command", ""))
        calls.append(cmd)
        if call_count == 1:
            return _make_result("ok")  # docker info
        if call_count == 2:
            return _make_result("TYPE TOTAL\n")  # docker system df
        return _make_result("3")  # dangling/stopped count

    with patch.object(executor, "execute", side_effect=capture):
        await assess_node(_base_state(docker_command_mode="direct_sudo"), executor=executor)  # type: ignore[arg-type]

    assert any("/usr/bin/docker info" in c for c in calls)


# --- Output parsing ---

def test_parse_assess_output_key_value() -> None:
    sample = (
        "reachable=yes\n"
        "dangling_images=5\n"
        "stopped_containers=2\n"
        "error=\n"
        "system_df_begin\n"
        "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\n"
        "Images 12 4 8.2GB 2.1GB\n"
        "system_df_end\n"
    )
    parsed = parse_assess_output(sample)
    assert parsed["reachable"] is True
    assert parsed["dangling_images"] == 5
    assert parsed["stopped_containers"] == 2
    assert parsed["error"] is None
    assert "Images 12" in parsed["system_df"]


def test_parse_assess_output_handles_error() -> None:
    sample = "reachable=no\nerror=docker daemon not reachable\n"
    parsed = parse_assess_output(sample)
    assert parsed["reachable"] is False
    assert parsed["error"] == "docker daemon not reachable"


def test_parse_assess_output_handles_missing_fields() -> None:
    parsed = parse_assess_output("")
    assert parsed["reachable"] is False
    assert parsed["dangling_images"] == 0
    assert parsed["stopped_containers"] == 0


# --- Execute wrapper dispatch ---

@pytest.mark.asyncio
async def test_wrapper_mode_prune_safe_call() -> None:
    executor = _make_executor(dry_run=False)
    calls: list[str] = []

    async def capture(*args: object, **kwargs: object) -> SSHResult:
        cmd = str(kwargs.get("command", ""))
        calls.append(cmd)
        return _make_result("")

    with patch.object(executor, "execute", side_effect=capture):
        await execute_node(  # type: ignore[arg-type]
            _base_state(
                docker_command_mode="wrapper",
                docker_prune_aggressive=False,
            ),
            executor=executor,
        )

    assert any("errander-docker-prune-safe" in c for c in calls)
    assert not any("errander-docker-prune-aggressive" in c for c in calls)


@pytest.mark.asyncio
async def test_wrapper_mode_prune_aggressive_call() -> None:
    executor = _make_executor(dry_run=False)
    calls: list[str] = []

    async def capture(*args: object, **kwargs: object) -> SSHResult:
        cmd = str(kwargs.get("command", ""))
        calls.append(cmd)
        return _make_result("")

    with patch.object(executor, "execute", side_effect=capture):
        await execute_node(  # type: ignore[arg-type]
            _base_state(
                docker_command_mode="wrapper",
                docker_prune_aggressive=True,
            ),
            executor=executor,
        )

    assert any("errander-docker-prune-aggressive" in c for c in calls)
    assert not any("errander-docker-prune-safe" in c for c in calls)
