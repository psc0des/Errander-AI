"""Tests for docker prune scope changes (finding #12)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.subgraphs.docker_prune import (
    DockerPruneGraphState,
    execute_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus


def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor(dry_run: bool = False) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


def _base_state(**overrides: object) -> DockerPruneGraphState:
    state: DockerPruneGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": False,
        "status": ActionStatus.PENDING.value,
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class TestDockerPruneScope:
    """Default prune is dangling-only; aggressive=True runs system prune -af."""

    @pytest.mark.asyncio
    async def test_default_uses_dangling_only_commands(self) -> None:
        """Without aggressive=True, only dangling images + stopped containers are pruned."""
        executor = _make_executor(dry_run=False)
        calls: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            cmd = str(kwargs.get("command", args[4] if len(args) > 4 else ""))
            calls.append(cmd)
            return _make_result("ok")

        with patch.object(executor, "execute", side_effect=capture):
            state = _base_state(docker_prune_aggressive=False)
            await execute_node(state, executor=executor)

        combined = " ".join(calls)
        assert "image prune" in combined
        assert "container prune" in combined
        # Must NOT use -a (all images)
        assert "system prune -af" not in combined

    @pytest.mark.asyncio
    async def test_aggressive_uses_system_prune(self) -> None:
        """With aggressive=True, docker system prune -af is used."""
        executor = _make_executor(dry_run=False)
        calls: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            cmd = str(kwargs.get("command", args[4] if len(args) > 4 else ""))
            calls.append(cmd)
            return _make_result("ok")

        with patch.object(executor, "execute", side_effect=capture):
            state = _base_state(docker_prune_aggressive=True)
            await execute_node(state, executor=executor)

        assert any("system prune -af" in c for c in calls)

    @pytest.mark.asyncio
    async def test_default_aggressive_is_false(self) -> None:
        """State without docker_prune_aggressive key defaults to safe (non-aggressive)."""
        executor = _make_executor(dry_run=False)
        calls: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            cmd = str(kwargs.get("command", args[4] if len(args) > 4 else ""))
            calls.append(cmd)
            return _make_result("ok")

        with patch.object(executor, "execute", side_effect=capture):
            # No docker_prune_aggressive key in state
            state = _base_state()
            await execute_node(state, executor=executor)

        combined = " ".join(calls)
        assert "system prune -af" not in combined

    @pytest.mark.asyncio
    async def test_dry_run_uses_simulate_command(self) -> None:
        """Dry-run always uses docker system df regardless of aggressive setting."""
        executor = _make_executor(dry_run=True)
        calls: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            sim = str(kwargs.get("simulate_command", ""))
            calls.append(sim)
            return _make_result("TYPE IMAGES ...")

        with patch.object(executor, "execute", side_effect=capture):
            state = _base_state(dry_run=True, docker_prune_aggressive=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert any("docker system df" in c for c in calls)
