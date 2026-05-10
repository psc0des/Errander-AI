"""Tests for the disk cleanup sub-graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.subgraphs.disk_cleanup import (
    ALLOWED_CLEANUP_PATHS,
    DiskCleanupGraphState,
    assess_node,
    build_disk_cleanup_subgraph,
    execute_node,
    get_package_manager_by_name,
    is_whitelisted,
    route_after_execute,
    route_after_validate,
    validate_node,
    validate_whitelist,
    verify_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus


# --- Helpers ---

def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _base_state(**overrides: object) -> DiskCleanupGraphState:
    defaults: DiskCleanupGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "whitelist_paths": list(ALLOWED_CLEANUP_PATHS),
        "tmp_age_days": 7,
        "journal_vacuum_days": 7,
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


# --- Whitelist tests ---

class TestWhitelist:
    """Tests for whitelist enforcement (hardcoded, never LLM-decided)."""

    def test_allowed_paths(self) -> None:
        for path in ALLOWED_CLEANUP_PATHS:
            assert is_whitelisted(path), f"{path} should be whitelisted"

    def test_disallowed_path(self) -> None:
        assert not is_whitelisted("/var/lib/mysql")
        assert not is_whitelisted("/home")
        assert not is_whitelisted("/etc")
        assert not is_whitelisted("/")

    def test_validate_whitelist_all_ok(self) -> None:
        assert validate_whitelist(list(ALLOWED_CLEANUP_PATHS)) == []

    def test_validate_whitelist_rejects_unknown(self) -> None:
        paths = ["/tmp", "/var/lib/mysql", "journal"]
        rejected = validate_whitelist(paths)
        assert rejected == ["/var/lib/mysql"]

    def test_validate_whitelist_empty(self) -> None:
        assert validate_whitelist([]) == []


# --- Validate node tests ---

class TestValidateNode:
    """Tests for the validation step."""

    def test_valid_paths_pass(self) -> None:
        state = _base_state(whitelist_paths=["/tmp", "journal"])
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value
        assert "error" not in result or result.get("error") is None

    def test_invalid_path_blocked(self) -> None:
        state = _base_state(whitelist_paths=["/tmp", "/var/lib/mysql"])
        result = validate_node(state)
        assert result["status"] == ActionStatus.FAILED.value
        assert "/var/lib/mysql" in result["error"]

    def test_default_paths_used_when_not_specified(self) -> None:
        state: DiskCleanupGraphState = {
            "vm_id": "vm-1",
            "os_family": "ubuntu",
            "dry_run": True,
            "status": ActionStatus.PENDING.value,
        }
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value


# --- Routing tests ---

class TestRouting:
    """Tests for conditional edge routing."""

    def test_route_after_validate_continues(self) -> None:
        state = _base_state(status=ActionStatus.PENDING.value)
        assert route_after_validate(state) == "assess"

    def test_route_after_validate_aborts_on_failure(self) -> None:
        state = _base_state(status=ActionStatus.FAILED.value)
        assert route_after_validate(state) == "__end__"

    def test_route_after_execute_finishes_dry_run(self) -> None:
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        assert route_after_execute(state) == "__end__"

    def test_route_after_execute_verifies_live(self) -> None:
        state = _base_state(status=ActionStatus.SUCCESS.value)
        assert route_after_execute(state) == "verify"


# --- Assess node tests ---

class TestAssessNode:
    """Tests for the assessment step (SSH mocked)."""

    async def test_assess_collects_space(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("4.0K\ttotal"))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["/tmp"])
            result = await assess_node(state, executor=executor)

        assert "/tmp" in result["space_by_path"]
        assert "disk_before" in result

    async def test_assess_handles_failure_gracefully(self) -> None:
        executor = _make_executor(dry_run=True)
        # Mock at executor level — return a failed result directly
        failed_result = _make_result("", exit_code=1)
        # Override success property check: the assess node checks result.success
        execute_mock = AsyncMock(return_value=failed_result)

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(whitelist_paths=["/tmp"])
            result = await assess_node(state, executor=executor)

        assert result["space_by_path"]["/tmp"] == "unknown"

    async def test_assess_apt_cache(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("120M\t/var/cache/apt"))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["apt-cache"], os_family="ubuntu")
            result = await assess_node(state, executor=executor)

        assert "apt-cache" in result["space_by_path"]

    async def test_assess_journal(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(
            return_value=_make_result("Archived and active journals take up 256.0M"),
        )

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["journal"])
            result = await assess_node(state, executor=executor)

        assert "journal" in result["space_by_path"]

    async def test_assess_orphaned_deps_ubuntu(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(
            return_value=_make_result("0 upgraded, 0 newly installed, 3 to remove"),
        )

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["orphaned-deps"], os_family="ubuntu")
            result = await assess_node(state, executor=executor)

        assert "orphaned-deps" in result["space_by_path"]


# --- Execute node tests ---

class TestExecuteNode:
    """Tests for the execution step (SSH mocked)."""

    async def test_dry_run_returns_dry_run_ok(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("simulated output"))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["/tmp"])
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert "/tmp" in result["cleanup_output"]

    async def test_live_returns_success(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("done"))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=["/tmp"], dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value

    async def test_all_paths_executed(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("ok"))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(whitelist_paths=list(ALLOWED_CLEANUP_PATHS))
            result = await execute_node(state, executor=executor)

        # Every whitelisted path should have output
        for path in ALLOWED_CLEANUP_PATHS:
            key = path if path not in ("apt-cache", "yum-cache") else path
            assert key in result["cleanup_output"] or path in result["cleanup_output"]

    async def test_rhel_uses_dnf_commands(self) -> None:
        executor = _make_executor(dry_run=False)
        calls: list[str] = []

        async def capture_execute(vm_id: str, hostname: str, username: str,
                                   key_path: str, command: str,
                                   simulate_command: str | None = None,
                                   timeout: int | None = None,
                                   dry_run: bool | None = None) -> SSHResult:
            actual_cmd = command if not executor.dry_run else (simulate_command or command)
            calls.append(actual_cmd)
            return _make_result("ok")

        with patch.object(executor, "execute", side_effect=capture_execute):
            state = _base_state(
                whitelist_paths=["yum-cache", "orphaned-deps"],
                os_family="rhel",
                dry_run=False,
            )
            await execute_node(state, executor=executor)

        # Should use dnf commands
        assert any("dnf clean" in c for c in calls)
        assert any("dnf autoremove" in c for c in calls)


# --- Verify node tests ---

class TestVerifyNode:
    """Tests for the verification step."""

    async def test_skipped_in_dry_run(self) -> None:
        executor = _make_executor(dry_run=True)
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        result = await verify_node(state, executor=executor)
        assert result == {}

    async def test_live_checks_disk_usage(self) -> None:
        executor = _make_executor(dry_run=False)
        df_output = "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 50G 25G 25G 50% /"
        execute_mock = AsyncMock(return_value=_make_result(df_output))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(status=ActionStatus.SUCCESS.value)
            result = await verify_node(state, executor=executor)

        assert result["disk_after"]["/"] == 50.0

    async def test_verify_failure(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("", exit_code=1))

        with patch.object(executor._ssh, "execute", execute_mock):
            state = _base_state(status=ActionStatus.SUCCESS.value)
            result = await verify_node(state, executor=executor)

        assert "error" in result


# --- Package manager helper tests ---

class TestPackageManagerByName:
    """Tests for get_package_manager_by_name."""

    def test_ubuntu_gets_apt(self) -> None:
        from errander.execution.commands import AptManager
        mgr = get_package_manager_by_name("ubuntu")
        assert isinstance(mgr, AptManager)

    def test_debian_gets_apt(self) -> None:
        from errander.execution.commands import AptManager
        mgr = get_package_manager_by_name("debian")
        assert isinstance(mgr, AptManager)

    def test_rhel_gets_dnf(self) -> None:
        from errander.execution.commands import DnfManager
        mgr = get_package_manager_by_name("rhel")
        assert isinstance(mgr, DnfManager)


# --- Sub-graph builder tests ---

class TestBuildSubgraph:
    """Tests for the sub-graph construction."""

    def test_graph_builds_without_error(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_disk_cleanup_subgraph(executor)
        assert graph is not None

    def test_graph_compiles(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_disk_cleanup_subgraph(executor)
        compiled = graph.compile()
        assert compiled is not None

    async def test_graph_runs_dry_run_blocked_path(self) -> None:
        """Graph aborts immediately when non-whitelisted path is given."""
        executor = _make_executor(dry_run=True)
        graph = build_disk_cleanup_subgraph(executor)
        compiled = graph.compile()

        initial_state: DiskCleanupGraphState = {
            "vm_id": "dev/web-01",
            "os_family": "ubuntu",
            "dry_run": True,
            "status": ActionStatus.PENDING.value,
            "whitelist_paths": ["/var/lib/mysql"],
        }

        result = await compiled.ainvoke(initial_state)
        assert result["status"] == ActionStatus.FAILED.value
        assert "/var/lib/mysql" in result["error"]

    async def test_graph_runs_dry_run_valid_paths(self) -> None:
        """Graph completes dry-run successfully with valid paths."""
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("ok"))

        with patch.object(executor._ssh, "execute", execute_mock):
            graph = build_disk_cleanup_subgraph(executor)
            compiled = graph.compile()

            initial_state: DiskCleanupGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "status": ActionStatus.PENDING.value,
                "whitelist_paths": ["/tmp"],
                "tmp_age_days": 7,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert "/tmp" in result["cleanup_output"]


# --- Phase 3 hardening tests (Step 5) ---

class TestAssessNodeEmptyOutput:
    """Step 5: assess_node must fail when df returns empty stdout (not silently pass)."""

    async def test_assess_handles_empty_stdout(self) -> None:
        """df -h returns empty stdout with exit_code=0 → FAILED, not silent nothing-to-do."""
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result(stdout="", exit_code=0))

        state = _base_state()

        with patch.object(executor, "execute", execute_mock):
            result = await assess_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "empty output" in result["error"]
