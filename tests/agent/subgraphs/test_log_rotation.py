"""Tests for the log rotation sub-graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.agent.subgraphs.log_rotation import (
    LogRotationGraphState,
    assess_node,
    build_log_rotation_subgraph,
    execute_node,
    is_valid_log_path,
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


def _base_state(**overrides: object) -> LogRotationGraphState:
    defaults: LogRotationGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "log_paths": ["/var/log"],
        "size_threshold_mb": 100,
        "compress": True,
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


# --- Path validation tests ---

class TestPathValidation:
    def test_var_log_allowed(self) -> None:
        assert is_valid_log_path("/var/log")

    def test_var_log_subdir_allowed(self) -> None:
        assert is_valid_log_path("/var/log/nginx")

    def test_root_not_allowed(self) -> None:
        assert not is_valid_log_path("/")

    def test_home_not_allowed(self) -> None:
        assert not is_valid_log_path("/home/user/logs")

    def test_etc_not_allowed(self) -> None:
        assert not is_valid_log_path("/etc")


# --- Validate node tests ---

class TestValidateNode:
    def test_valid_paths_pass(self) -> None:
        state = _base_state(log_paths=["/var/log"])
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value

    def test_invalid_path_blocked(self) -> None:
        state = _base_state(log_paths=["/var/log", "/home/user/logs"])
        result = validate_node(state)
        assert result["status"] == ActionStatus.FAILED.value
        assert "/home/user/logs" in result["error"]

    def test_default_paths_used(self) -> None:
        state: LogRotationGraphState = {
            "vm_id": "vm-1",
            "os_family": "ubuntu",
            "dry_run": True,
        }
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value


# --- Assess node tests ---

class TestAssessNode:
    async def test_finds_large_files(self) -> None:
        executor = _make_executor(dry_run=True)
        ls_output = "-rw-r--r-- 1 root root 500M Jan 1 00:00 /var/log/syslog"
        execute_mock = AsyncMock(return_value=_make_result(ls_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is False
        assert len(result["large_files"]) == 1
        assert "/var/log/syslog" in result["large_files"]

    async def test_nothing_to_do_when_no_large_files(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result(""))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value


# --- Execute node tests ---

class TestExecuteNode:
    async def test_dry_run_returns_dry_run_ok(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("simulated output"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(large_files=["/var/log/syslog"])
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value

    async def test_live_returns_success(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("done"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(large_files=["/var/log/syslog"], dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value

    async def test_per_file_failure_returns_failed(self) -> None:
        """Per-file SSH failure is recorded as FAILED (no global logrotate fallback)."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("", exit_code=1))

        with patch.object(executor, "execute", side_effect=execute_mock):
            state = _base_state(large_files=["/var/log/syslog"], dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "/var/log/syslog" in result["rotation_output"]

    async def test_does_not_call_global_logrotate(self) -> None:
        """Execute node must never call logrotate --force /etc/logrotate.conf."""
        executor = _make_executor(dry_run=False)
        commands_seen: list[str] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            commands_seen.append(str(kwargs.get("command", "")))
            return _make_result("done")

        with patch.object(executor, "execute", side_effect=capture):
            state = _base_state(large_files=["/var/log/syslog"], dry_run=False)
            await execute_node(state, executor=executor)

        assert not any("logrotate --force /etc/logrotate.conf" in c for c in commands_seen)


# --- Verify node tests ---

class TestVerifyNode:
    async def test_skipped_in_dry_run(self) -> None:
        executor = _make_executor(dry_run=True)
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        result = await verify_node(state, executor=executor)
        assert result == {}

    async def test_live_checks_remaining(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("0"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(status=ActionStatus.SUCCESS.value)
            result = await verify_node(state, executor=executor)

        assert "error" not in result or result.get("error") is None

    async def test_verify_forces_real_read_regardless_of_executor_mode(self) -> None:
        """Verification must read real VM state even when executor is in dry-run mode."""
        executor = _make_executor(dry_run=True)
        captured_kwargs: dict[str, object] = {}

        async def capture_execute(*args: object, **kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            return _make_result("0")

        with patch.object(executor, "execute", side_effect=capture_execute):
            state = _base_state(status=ActionStatus.SUCCESS.value)
            await verify_node(state, executor=executor)

        assert captured_kwargs.get("dry_run") is False, (
            "verify_node must pass dry_run=False so verification always inspects real VM state"
        )


# --- Routing tests ---

class TestRouting:
    def test_route_after_validate_continues(self) -> None:
        state = _base_state(status=ActionStatus.PENDING.value)
        assert route_after_validate(state) == "assess"

    def test_route_after_validate_aborts_on_failure(self) -> None:
        state = _base_state(status=ActionStatus.FAILED.value)
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
        graph = build_log_rotation_subgraph(executor)
        assert graph is not None

    def test_graph_compiles(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_log_rotation_subgraph(executor)
        compiled = graph.compile()
        assert compiled is not None

    async def test_graph_blocks_invalid_path(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_log_rotation_subgraph(executor)
        compiled = graph.compile()

        initial_state: LogRotationGraphState = {
            "vm_id": "dev/web-01",
            "os_family": "ubuntu",
            "dry_run": True,
            "log_paths": ["/home/user/logs"],
        }

        result = await compiled.ainvoke(initial_state)
        assert result["status"] == ActionStatus.FAILED.value

    async def test_graph_skips_when_nothing_to_do(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result(""))

        with patch.object(executor, "execute", execute_mock):
            graph = build_log_rotation_subgraph(executor)
            compiled = graph.compile()

            initial_state: LogRotationGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "log_paths": ["/var/log"],
                "size_threshold_mb": 100,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value


# --- Phase 3 hardening tests (Step 5) ---

class TestAssessNodeCommandFailure:
    """Step 5: assess_node must fail when the find command exits non-zero."""

    async def test_assess_handles_empty_stdout(self) -> None:
        """find command fails with exit_code=1 → FAILED, not silent nothing-to-do."""
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result(stdout="", exit_code=1))

        state = _base_state()

        with patch.object(executor, "execute", execute_mock):
            result = await assess_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "command failed" in result["error"]
