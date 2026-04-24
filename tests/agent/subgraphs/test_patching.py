"""Tests for the patching sub-graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.agent.subgraphs.patching import (
    MANDATORY_KERNEL_EXCLUDES,
    PatchingGraphState,
    assess_node,
    build_patching_subgraph,
    execute_node,
    route_after_assess,
    route_after_execute,
    route_after_validate,
    snapshot_node,
    validate_node,
    verify_node,
    _filter_kernel_packages,
    _is_kernel_package,
    _parse_upgradable,
    _parse_versions,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus


# --- Helpers ---

def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _base_state(**overrides: object) -> PatchingGraphState:
    defaults: PatchingGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "exclude_patterns": list(MANDATORY_KERNEL_EXCLUDES),
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


# --- Kernel exclusion tests ---

class TestKernelExclusion:
    def test_linux_image_is_kernel(self) -> None:
        assert _is_kernel_package("linux-image-5.15.0")

    def test_linux_headers_is_kernel(self) -> None:
        assert _is_kernel_package("linux-headers-5.15.0")

    def test_kernel_core_is_kernel(self) -> None:
        assert _is_kernel_package("kernel-core-5.14")

    def test_regular_package_not_kernel(self) -> None:
        assert not _is_kernel_package("nginx")
        assert not _is_kernel_package("curl")
        assert not _is_kernel_package("python3")

    def test_filter_removes_kernel_packages(self) -> None:
        packages = ["nginx", "linux-image-5.15", "curl", "kernel-core-5.14"]
        filtered = _filter_kernel_packages(packages)
        assert filtered == ["nginx", "curl"]


# --- Parse helpers ---

class TestParseHelpers:
    def test_parse_upgradable_apt(self) -> None:
        output = (
            "Listing... Done\n"
            "nginx/focal-updates 1.18.0-0ubuntu1.3 amd64 [upgradable from: 1.18.0-0ubuntu1]\n"
            "curl/focal-updates 7.68.0-1ubuntu2.14 amd64 [upgradable from: 7.68.0-1ubuntu2.7]\n"
        )
        result = _parse_upgradable(output, "ubuntu")
        assert result == ["nginx", "curl"]

    def test_parse_upgradable_dnf(self) -> None:
        output = (
            "Last metadata check: 1 hour ago.\n"
            "nginx.x86_64  1:1.20.1-10.el8  appstream\n"
            "curl.x86_64   7.61.1-25.el8    baseos\n"
        )
        result = _parse_upgradable(output, "rhel")
        assert result == ["nginx", "curl"]

    def test_parse_versions(self) -> None:
        output = "nginx=1.18.0-0ubuntu1\ncurl=7.68.0-1ubuntu2.7\n"
        result = _parse_versions(output)
        assert result == {"nginx": "1.18.0-0ubuntu1", "curl": "7.68.0-1ubuntu2.7"}

    def test_parse_versions_empty(self) -> None:
        assert _parse_versions("") == {}


# --- Validate node tests ---

class TestValidateNode:
    def test_always_includes_kernel_excludes(self) -> None:
        state = _base_state(exclude_patterns=[])
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value
        # Kernel excludes must be present even if user provides empty list
        for pattern in MANDATORY_KERNEL_EXCLUDES:
            assert pattern in result["exclude_patterns"]

    def test_merges_user_excludes(self) -> None:
        state = _base_state(exclude_patterns=["custom-pkg-*"])
        result = validate_node(state)
        assert "custom-pkg-*" in result["exclude_patterns"]
        for pattern in MANDATORY_KERNEL_EXCLUDES:
            assert pattern in result["exclude_patterns"]


# --- Assess node tests ---

class TestAssessNode:
    async def test_finds_upgradable_packages(self) -> None:
        executor = _make_executor(dry_run=True)
        apt_output = (
            "Listing... Done\n"
            "nginx/focal-updates 1.18.0 amd64 [upgradable from: 1.17.0]\n"
            "curl/focal-updates 7.68.0 amd64 [upgradable from: 7.67.0]\n"
        )
        execute_mock = AsyncMock(return_value=_make_result(apt_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is False
        assert "nginx" in result["pending_updates"]
        assert "curl" in result["pending_updates"]

    async def test_filters_kernel_packages(self) -> None:
        executor = _make_executor(dry_run=True)
        apt_output = (
            "Listing... Done\n"
            "nginx/focal 1.18.0 amd64 [upgradable from: 1.17.0]\n"
            "linux-image-5.15.0/focal 5.15.0 amd64 [upgradable from: 5.14.0]\n"
        )
        execute_mock = AsyncMock(return_value=_make_result(apt_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert "nginx" in result["pending_updates"]
        assert "linux-image-5.15.0" not in result["pending_updates"]

    async def test_nothing_to_do_when_up_to_date(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("Listing... Done\n"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value

    async def test_nothing_to_do_when_only_kernel_updates(self) -> None:
        executor = _make_executor(dry_run=True)
        apt_output = (
            "Listing... Done\n"
            "linux-image-5.15.0/focal 5.15.0 amd64 [upgradable from: 5.14.0]\n"
        )
        execute_mock = AsyncMock(return_value=_make_result(apt_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True


# --- Snapshot node tests ---

class TestSnapshotNode:
    async def test_captures_version_snapshot(self) -> None:
        executor = _make_executor(dry_run=True)
        version_output = "nginx=1.18.0-0ubuntu1\ncurl=7.68.0-1ubuntu2\n"
        execute_mock = AsyncMock(return_value=_make_result(version_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(pending_updates=["nginx", "curl"])
            result = await snapshot_node(state, executor=executor)

        assert result["version_snapshot"]["nginx"] == "1.18.0-0ubuntu1"
        assert result["version_snapshot"]["curl"] == "7.68.0-1ubuntu2"

    async def test_empty_snapshot_when_no_pending(self) -> None:
        executor = _make_executor(dry_run=True)
        state = _base_state(pending_updates=[])
        result = await snapshot_node(state, executor=executor)
        assert result["version_snapshot"] == {}


# --- Execute node tests ---

class TestExecuteNode:
    async def test_dry_run_returns_dry_run_ok(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("simulated upgrade"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value

    async def test_live_returns_success(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("upgraded"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value

    async def test_live_failure_returns_failed(self) -> None:
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("", exit_code=1))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value


# --- Verify node tests ---

class TestVerifyNode:
    async def test_skipped_in_dry_run(self) -> None:
        executor = _make_executor(dry_run=True)
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        result = await verify_node(state, executor=executor)
        assert result == {}

    async def test_detects_version_changes(self) -> None:
        executor = _make_executor(dry_run=False)
        new_versions = "nginx=1.19.0-0ubuntu1\ncurl=7.69.0-1ubuntu2\n"
        execute_mock = AsyncMock(return_value=_make_result(new_versions))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                pending_updates=["nginx", "curl"],
                version_snapshot={"nginx": "1.18.0-0ubuntu1", "curl": "7.68.0-1ubuntu2"},
            )
            result = await verify_node(state, executor=executor)

        assert "updated_versions" in result
        assert result["updated_versions"]["nginx"] == "1.19.0-0ubuntu1"


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

    def test_route_after_assess_continues_to_snapshot(self) -> None:
        state = _base_state(nothing_to_do=False)
        assert route_after_assess(state) == "snapshot"

    def test_route_after_execute_finishes_dry_run(self) -> None:
        state = _base_state(status=ActionStatus.DRY_RUN_OK.value)
        assert route_after_execute(state) == "__end__"

    def test_route_after_execute_finishes_on_failure(self) -> None:
        state = _base_state(status=ActionStatus.FAILED.value)
        assert route_after_execute(state) == "__end__"

    def test_route_after_execute_verifies_live(self) -> None:
        state = _base_state(status=ActionStatus.SUCCESS.value)
        assert route_after_execute(state) == "verify"


# --- Graph builder tests ---

class TestBuildSubgraph:
    def test_graph_builds_without_error(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor)
        assert graph is not None

    def test_graph_compiles(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor)
        compiled = graph.compile()
        assert compiled is not None

    async def test_graph_nothing_to_do(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("Listing... Done\n"))

        with patch.object(executor, "execute", execute_mock):
            graph = build_patching_subgraph(executor)
            compiled = graph.compile()

            initial_state: PatchingGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value

    async def test_graph_dry_run_with_updates(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # list_upgradable
                return _make_result(
                    "Listing... Done\n"
                    "nginx/focal 1.18.0 amd64 [upgradable from: 1.17.0]\n"
                )
            if call_count == 2:
                # list_installed_versions (snapshot)
                return _make_result("nginx=1.17.0\n")
            # simulate_upgrade
            return _make_result("simulated upgrade output")

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor)
            compiled = graph.compile()

            initial_state: PatchingGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert "nginx" in result["pending_updates"]


# --- Phase 3 hardening tests (Step 5) ---

class TestSnapshotNodeEmptyOutput:
    """Step 5: snapshot_node must fail when SSH returns empty stdout (no rollback safety)."""

    async def test_snapshot_node_fails_on_empty_output(self) -> None:
        """SSH returns empty stdout with success=True → FAILED with rollback-safety error."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result(stdout="", exit_code=0))

        state = _base_state(
            pending_updates=["nginx"],
            version_snapshot={},
        )

        with patch.object(executor, "execute", execute_mock):
            result = await snapshot_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "empty package snapshot" in result["error"]
        assert "rollback" in result["error"]
