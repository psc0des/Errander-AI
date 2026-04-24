"""Tests for the Docker prune sub-graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.subgraphs.docker_prune import (
    DockerPruneGraphState,
    assess_node,
    build_docker_prune_subgraph,
    execute_node,
    route_after_assess,
    route_after_execute,
    route_after_validate,
    validate_node,
    verify_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus


# --- Helpers ---

def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _base_state(**overrides: object) -> DockerPruneGraphState:
    defaults: DockerPruneGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "docker_available": True,
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


# --- Validate node tests ---

class TestValidateNode:
    def test_docker_available_passes(self) -> None:
        state = _base_state(docker_available=True)
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value

    def test_docker_not_available_skips(self) -> None:
        state = _base_state(docker_available=False)
        result = validate_node(state)
        assert result["status"] == ActionStatus.SKIPPED.value
        assert "not installed" in result["error"]


# --- Assess node tests ---

class TestAssessNode:
    async def test_finds_dangling_and_stopped(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("ok")  # docker info
            if call_count == 2:
                return _make_result("TYPE  TOTAL  ACTIVE  SIZE  RECLAIMABLE\nImages  5  2  1.2GB  800MB")  # docker system df
            if call_count == 3:
                return _make_result("3")  # dangling images
            return _make_result("2")  # stopped containers

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is False
        assert result["dangling_images"] == 3
        assert result["stopped_containers"] == 2

    async def test_nothing_to_do_when_clean(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("ok")  # docker info
            if call_count == 2:
                return _make_result("clean")  # docker system df
            if call_count == 3:
                return _make_result("0")  # dangling images
            return _make_result("0")  # stopped containers

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value

    async def test_docker_daemon_not_responding(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("", exit_code=1))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value


# --- Execute node tests ---

class TestExecuteNode:
    async def test_dry_run_returns_dry_run_ok(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("simulated df"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value

    async def test_live_returns_success(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("Total reclaimed: 500MB"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value


# --- Verify node tests ---

class TestVerifyNode:
    async def test_skipped_in_dry_run(self) -> None:
        executor = _make_executor(dry_run=True)
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        result = await verify_node(state, executor=executor)
        assert result == {}

    async def test_live_checks_docker_df(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("clean df output"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(status=ActionStatus.SUCCESS.value)
            result = await verify_node(state, executor=executor)

        assert "disk_after" in result


# --- Routing tests ---

class TestRouting:
    def test_route_after_validate_continues(self) -> None:
        state = _base_state(status=ActionStatus.PENDING.value)
        assert route_after_validate(state) == "assess"

    def test_route_after_validate_skips_when_no_docker(self) -> None:
        state = _base_state(status=ActionStatus.SKIPPED.value)
        assert route_after_validate(state) == "__end__"

    def test_route_after_assess_skips_when_nothing_to_do(self) -> None:
        state = _base_state(nothing_to_do=True)
        assert route_after_assess(state) == "__end__"

    def test_route_after_assess_continues(self) -> None:
        state = _base_state(nothing_to_do=False)
        assert route_after_assess(state) == "execute"

    def test_route_after_execute_finishes_dry_run(self) -> None:
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        assert route_after_execute(state) == "__end__"

    def test_route_after_execute_verifies_live(self) -> None:
        state = _base_state(status=ActionStatus.SUCCESS.value)
        assert route_after_execute(state) == "verify"


# --- Graph builder tests ---

class TestBuildSubgraph:
    def test_graph_builds_without_error(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_docker_prune_subgraph(executor)
        assert graph is not None

    def test_graph_compiles(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_docker_prune_subgraph(executor)
        compiled = graph.compile()
        assert compiled is not None

    async def test_graph_skips_when_no_docker(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_docker_prune_subgraph(executor)
        compiled = graph.compile()

        initial_state: DockerPruneGraphState = {
            "vm_id": "dev/web-01",
            "os_family": "ubuntu",
            "dry_run": True,
            "docker_available": False,
        }

        result = await compiled.ainvoke(initial_state)
        assert result["status"] == ActionStatus.SKIPPED.value

    async def test_graph_nothing_to_do(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("ok")
            if call_count == 2:
                return _make_result("clean")
            if call_count == 3:
                return _make_result("0")
            return _make_result("0")

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_docker_prune_subgraph(executor)
            compiled = graph.compile()

            initial_state: DockerPruneGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "docker_available": True,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value


# --- Phase 3 hardening tests (Step 5) ---

class TestAssessNodeEmptyOutput:
    """Step 5: assess_node must fail when docker wc -l returns empty stdout."""

    async def test_assess_handles_empty_stdout(self) -> None:
        """dangling-image wc -l returns empty stdout with exit_code=0 → FAILED."""
        executor = _make_executor(dry_run=True)

        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result(stdout="ok", exit_code=0)   # docker info
            if call_count == 2:
                return _make_result(stdout="ok", exit_code=0)   # docker system df
            return _make_result(stdout="", exit_code=0)          # wc -l returns empty

        state = _base_state()

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await assess_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "empty output" in result["error"]
