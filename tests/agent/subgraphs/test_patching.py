"""Tests for the patching sub-graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.agent.subgraphs.patching import (
    MANDATORY_KERNEL_EXCLUDES,
    PatchingGraphState,
    _filter_kernel_packages,
    _is_kernel_package,
    _parse_upgradable,
    _parse_versions,
    assess_node,
    build_patching_subgraph,
    execute_node,
    preflight_lock_node,
    reboot_check_node,
    route_after_assess,
    route_after_execute,
    route_after_preflight_lock,
    route_after_validate,
    service_health_post_node,
    service_health_pre_node,
    snapshot_node,
    validate_node,
    verify_node,
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
        # First call: refresh_package_lists; second call: list_upgradable
        execute_mock = AsyncMock(side_effect=[_make_result(""), _make_result(apt_output)])

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
        execute_mock = AsyncMock(side_effect=[_make_result(""), _make_result(apt_output)])

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert "nginx" in result["pending_updates"]
        assert "linux-image-5.15.0" not in result["pending_updates"]

    async def test_nothing_to_do_when_up_to_date(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(side_effect=[_make_result(""), _make_result("Listing... Done\n")])

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
        execute_mock = AsyncMock(side_effect=[_make_result(""), _make_result(apt_output)])

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True

    async def test_approved_artifact_path_skips_list_upgradable(self) -> None:
        """With approved_packages, assess uses list_installed_versions — not list_upgradable."""
        executor = _make_executor(dry_run=False)
        # Installed versions differ from approved targets → needs installing
        installed_output = "nginx=1.18.0-0ubuntu1\ncurl=7.81.0-1ubuntu1.10\n"
        execute_mock = AsyncMock(return_value=_make_result(installed_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=_APPROVED_PKGS)
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is False
        assert set(result["pending_updates"]) == {"nginx", "curl"}
        # Only one SSH call (list_installed_versions), not two (refresh + list_upgradable)
        assert execute_mock.call_count == 1

    async def test_approved_artifact_already_at_target_nothing_to_do(self) -> None:
        """assess skips execution when all approved packages are already at target version."""
        executor = _make_executor(dry_run=False)
        # Installed == approved targets
        installed_output = "nginx=1.24.0-1ubuntu1\ncurl=7.88.1-10ubuntu1\n"
        execute_mock = AsyncMock(return_value=_make_result(installed_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=_APPROVED_PKGS)
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value
        assert result["pending_updates"] == []

    async def test_approved_artifact_partial_update_needed(self) -> None:
        """assess returns only packages whose installed version differs from approved target."""
        executor = _make_executor(dry_run=False)
        # nginx already at target; curl still old
        installed_output = "nginx=1.24.0-1ubuntu1\ncurl=7.81.0-1ubuntu1.10\n"
        execute_mock = AsyncMock(return_value=_make_result(installed_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=_APPROVED_PKGS)
            result = await assess_node(state, executor=executor)

        assert result["nothing_to_do"] is False
        assert result["pending_updates"] == ["curl"]


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

_APPROVED_PKGS = [
    {"name": "nginx", "target": "1.24.0-1ubuntu1", "current": "1.18.0-0ubuntu1"},
    {"name": "curl", "target": "7.88.1-10ubuntu1", "current": "7.81.0-1ubuntu1.10"},
]


class TestExecuteNode:
    async def test_dry_run_no_preview_returns_dry_run_ok(self) -> None:
        """Dry-run without approved_packages falls back to simulate_upgrade."""
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("simulated upgrade"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state()
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value

    async def test_dry_run_with_approved_packages_simulates_pinned(self) -> None:
        """Dry-run with approved_packages simulates pinned install."""
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("Inst nginx [1.18] (1.24)"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(approved_packages=_APPROVED_PKGS)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        # simulate_install_pinned was used — command must contain --simulate
        call_kwargs = execute_mock.call_args
        assert "--simulate" in str(call_kwargs)

    async def test_live_with_approved_packages_returns_success(self) -> None:
        """Live mode with approved_packages uses install_pinned."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("upgraded"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=_APPROVED_PKGS)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value
        call_kwargs = execute_mock.call_args
        # install_pinned uses apt-get install, not apt-get upgrade
        assert "install" in str(call_kwargs)
        assert "upgrade" not in str(call_kwargs)

    async def test_live_fails_closed_without_approved_packages(self) -> None:
        """Live mode without approved_packages fails closed — never broad upgrade."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("upgraded"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "approved_packages" in result["error"]
        execute_mock.assert_not_called()

    async def test_live_fails_closed_when_versions_missing(self) -> None:
        """Live mode fails if any approved package is missing its target version."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("upgraded"))
        missing_ver_pkgs = [
            {"name": "nginx", "target": "", "current": "1.18.0"},  # target empty
        ]

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=missing_ver_pkgs)
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "nginx" in result["error"]
        execute_mock.assert_not_called()

    async def test_live_failure_returns_failed(self) -> None:
        """SSH failure on pinned install reports FAILED status."""
        executor = _make_executor(dry_run=False)
        execute_mock = AsyncMock(return_value=_make_result("", exit_code=1))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(dry_run=False, approved_packages=_APPROVED_PKGS)
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

    async def test_exact_version_match_passes(self) -> None:
        """With approved_packages, verify passes when installed == approved target."""
        executor = _make_executor(dry_run=False)
        installed = "nginx=1.24.0-1ubuntu1\ncurl=7.88.1-10ubuntu1\n"
        execute_mock = AsyncMock(return_value=_make_result(installed))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                pending_updates=["nginx", "curl"],
                version_snapshot={"nginx": "1.18.0-0ubuntu1", "curl": "7.81.0-1ubuntu1.10"},
                approved_packages=_APPROVED_PKGS,
            )
            result = await verify_node(state, executor=executor)

        assert result.get("status") != ActionStatus.FAILED.value
        assert "updated_versions" in result

    async def test_exact_version_mismatch_fails(self) -> None:
        """With approved_packages, verify fails when installed != approved target."""
        executor = _make_executor(dry_run=False)
        # nginx installed at a different version than approved target
        installed = "nginx=1.22.0-1ubuntu1\ncurl=7.88.1-10ubuntu1\n"
        execute_mock = AsyncMock(return_value=_make_result(installed))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                pending_updates=["nginx", "curl"],
                version_snapshot={"nginx": "1.18.0-0ubuntu1", "curl": "7.81.0-1ubuntu1.10"},
                approved_packages=_APPROVED_PKGS,
            )
            result = await verify_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "nginx" in result["error"]
        assert "1.24.0-1ubuntu1" in result["error"]  # expected version in error message

    async def test_missing_package_in_dpkg_output_fails(self) -> None:
        """With approved_packages, verify fails if an approved package isn't in dpkg output."""
        executor = _make_executor(dry_run=False)
        installed = "nginx=1.24.0-1ubuntu1\n"  # curl missing from output
        execute_mock = AsyncMock(return_value=_make_result(installed))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                pending_updates=["nginx", "curl"],
                version_snapshot={"nginx": "1.18.0-0ubuntu1", "curl": "7.81.0-1ubuntu1.10"},
                approved_packages=_APPROVED_PKGS,
            )
            result = await verify_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "curl" in result["error"]

    async def test_partial_update_already_at_target_passes(self) -> None:
        """Partial-update scenario: nginx already at target (not in pending_updates),
        curl needed install. Both appear at target in dpkg output. Verify must PASS.
        Previously broke because verify only queried pending_updates packages."""
        executor = _make_executor(dry_run=False)
        # dpkg output contains BOTH packages at approved targets
        installed = "nginx=1.24.0-1ubuntu1\ncurl=7.88.1-10ubuntu1\n"
        execute_mock = AsyncMock(return_value=_make_result(installed))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                # Only curl was pending — nginx was already at target version
                pending_updates=["curl"],
                version_snapshot={"nginx": "1.24.0-1ubuntu1", "curl": "7.81.0-1ubuntu1.10"},
                approved_packages=_APPROVED_PKGS,  # nginx + curl both approved
            )
            result = await verify_node(state, executor=executor)

        # Must pass: nginx is already at approved target, curl just got installed
        assert result.get("status") != ActionStatus.FAILED.value
        assert "updated_versions" in result

    async def test_partial_update_query_uses_all_approved_names(self) -> None:
        """Verify that when approved_packages is set, the SSH query uses all approved
        package names (not just pending_updates), so already-at-target packages
        are visible in dpkg output."""
        executor = _make_executor(dry_run=False)
        installed = "nginx=1.24.0-1ubuntu1\ncurl=7.88.1-10ubuntu1\n"
        execute_mock = AsyncMock(return_value=_make_result(installed))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(
                status=ActionStatus.SUCCESS.value,
                pending_updates=["curl"],  # nginx NOT pending
                version_snapshot={},
                approved_packages=_APPROVED_PKGS,
            )
            await verify_node(state, executor=executor)

        # The SSH command must have been called with both approved names
        call_args = execute_mock.call_args
        cmd_arg = call_args.kwargs.get("command") or (call_args.args[4] if len(call_args.args) > 4 else "")
        assert "nginx" in cmd_arg
        assert "curl" in cmd_arg


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

    def test_route_after_execute_routes_failure_to_rollback(self) -> None:
        state = _base_state(status=ActionStatus.FAILED.value)
        assert route_after_execute(state) == "rollback"

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
            graph = build_patching_subgraph(executor, sre_preflight_lock_check=False)
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
                # refresh_package_lists
                return _make_result("")
            if call_count == 2:
                # list_upgradable
                return _make_result(
                    "Listing... Done\n"
                    "nginx/focal 1.18.0 amd64 [upgradable from: 1.17.0]\n"
                )
            if call_count == 3:
                # list_installed_versions (snapshot)
                return _make_result("nginx=1.17.0\n")
            # simulate_upgrade
            return _make_result("simulated upgrade output")

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor, sre_preflight_lock_check=False)
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


# --- Preflight lock node tests (1.1) ---

class TestPreflightLockNode:
    """Tests for preflight_lock_node and its routing."""

    async def test_clear_lock_returns_no_holder(self) -> None:
        executor = _make_executor(dry_run=True)
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(""))):
            result = await preflight_lock_node(_base_state(), executor=executor)
        assert result.get("lock_holder_pid") is None
        assert result.get("lock_holder_cmd") is None
        assert result.get("status") != ActionStatus.BLOCKED.value

    async def test_held_lock_sets_blocked_status(self) -> None:
        executor = _make_executor(dry_run=True)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("pid=1234 cmd=apt-get")),
        ):
            result = await preflight_lock_node(_base_state(), executor=executor)
        assert result["status"] == ActionStatus.BLOCKED.value
        assert result["lock_holder_pid"] == 1234
        assert result["lock_holder_cmd"] == "apt-get"

    async def test_held_lock_populates_error_detail(self) -> None:
        executor = _make_executor(dry_run=True)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("pid=999 cmd=dpkg")),
        ):
            result = await preflight_lock_node(_base_state(), executor=executor)
        assert "999" in result["error"]
        assert "dpkg" in result["error"]

    async def test_emits_audit_event_when_store_provided(self) -> None:
        from errander.models.events import EventType
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=True)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("pid=1234 cmd=apt-get")),
        ):
            await preflight_lock_node(
                _base_state(), executor=executor,
                audit_store=audit_store, batch_id="batch-001",
            )

        audit_store.log_event.assert_called_once()
        call_args = audit_store.log_event.call_args[0][0]
        assert call_args.event_type == EventType.PREFLIGHT_LOCK_DETECTED
        assert call_args.batch_id == "batch-001"

    async def test_emits_clear_audit_event_when_no_lock(self) -> None:
        from errander.models.events import EventType
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=True)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(""))):
            await preflight_lock_node(
                _base_state(), executor=executor,
                audit_store=audit_store, batch_id="batch-001",
            )

        audit_store.log_event.assert_called_once()
        call_args = audit_store.log_event.call_args[0][0]
        assert call_args.event_type == EventType.PREFLIGHT_LOCK_CLEAR

    async def test_no_audit_event_when_store_absent(self) -> None:
        executor = _make_executor(dry_run=True)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("pid=1 cmd=apt-get")),
        ):
            # Should not raise even without audit_store
            result = await preflight_lock_node(_base_state(), executor=executor)
        assert result["status"] == ActionStatus.BLOCKED.value


class TestRouteAfterPreflightLock:
    def test_blocked_routes_to_end(self) -> None:
        state = _base_state(status=ActionStatus.BLOCKED.value)
        assert route_after_preflight_lock(state) == "__end__"

    def test_pending_routes_to_validate(self) -> None:
        state = _base_state(status=ActionStatus.PENDING.value)
        assert route_after_preflight_lock(state) == "validate"

    def test_no_status_routes_to_validate(self) -> None:
        state = _base_state()
        del state["status"]  # type: ignore[misc]
        assert route_after_preflight_lock(state) == "validate"


class TestBuildSubgraphWithLockCheck:
    """Integration tests for the patching subgraph with preflight lock enabled."""

    async def test_lock_held_graph_ends_blocked(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # preflight_lock_node → lock detected
                return _make_result("pid=1234 cmd=apt-get")
            # Should not be reached
            return _make_result("")

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor, sre_preflight_lock_check=True)
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

        assert result["status"] == ActionStatus.BLOCKED.value
        assert result["lock_holder_pid"] == 1234
        # Only 1 SSH call — upgrade was never invoked
        assert call_count == 1

    async def test_no_lock_graph_proceeds_to_assess(self) -> None:
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("")  # no lock
            if call_count == 2:
                return _make_result("")  # refresh_package_lists
            # list_upgradable → nothing to do
            return _make_result("Listing... Done\n")

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor, sre_preflight_lock_check=True)
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

        assert result["status"] == ActionStatus.SKIPPED.value
        assert result.get("lock_holder_pid") is None
        assert call_count >= 3  # lock check + refresh + list_upgradable


# --- Reboot check node tests (1.2) ---

class TestRebootCheckNode:
    """Tests for reboot_check_node and its integration into the patching subgraph."""

    async def test_reboot_needed_sets_flag(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=1\nlibc6\n")),
        ):
            result = await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
            )
        assert result["reboot_status_detected"] is True

    async def test_no_reboot_clears_flag(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=0\n")),
        ):
            result = await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
            )
        assert result["reboot_status_detected"] is False

    async def test_calls_vm_state_store_when_reboot_needed(self) -> None:
        from errander.safety.vm_state import VMStateStore

        executor = _make_executor(dry_run=False)
        vm_state_store = AsyncMock(spec=VMStateStore)
        vm_state_store.set_needs_reboot = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=1\nlibc6\n")),
        ):
            await reboot_check_node(
                _base_state(vm_id="dev/web-01", status=ActionStatus.SUCCESS.value),
                executor=executor,
                vm_state_store=vm_state_store,
            )

        vm_state_store.set_needs_reboot.assert_called_once()
        call_args = vm_state_store.set_needs_reboot.call_args
        assert call_args[0][0] == "dev/web-01"

    async def test_skips_vm_state_store_when_not_needed(self) -> None:
        from errander.safety.vm_state import VMStateStore

        executor = _make_executor(dry_run=False)
        vm_state_store = AsyncMock(spec=VMStateStore)
        vm_state_store.set_needs_reboot = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=0\n")),
        ):
            await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
                vm_state_store=vm_state_store,
            )

        vm_state_store.set_needs_reboot.assert_not_called()

    async def test_no_vm_state_store_no_error(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=1\nlibc6\n")),
        ):
            # Should not raise even without vm_state_store
            result = await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
                vm_state_store=None,
            )
        assert result["reboot_status_detected"] is True

    async def test_emits_audit_event_when_reboot_detected(self) -> None:
        from errander.models.events import EventType
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=False)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=1\nlibc6\n")),
        ):
            await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
                audit_store=audit_store,
                batch_id="batch-42",
            )

        audit_store.log_event.assert_called_once()
        call_args = audit_store.log_event.call_args[0][0]
        assert call_args.event_type == EventType.REBOOT_REQUIRED_DETECTED
        assert call_args.batch_id == "batch-42"

    async def test_no_audit_event_when_no_reboot(self) -> None:
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=False)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("REBOOT=0\n")),
        ):
            await reboot_check_node(
                _base_state(status=ActionStatus.SUCCESS.value),
                executor=executor,
                audit_store=audit_store,
            )

        audit_store.log_event.assert_not_called()

    async def test_rhel_reboot_stores_state(self) -> None:
        from errander.safety.vm_state import VMStateStore

        executor = _make_executor(dry_run=False)
        vm_state_store = AsyncMock(spec=VMStateStore)
        vm_state_store.set_needs_reboot = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("EXIT=1\n")),
        ):
            result = await reboot_check_node(
                _base_state(os_family="rhel", status=ActionStatus.SUCCESS.value),
                executor=executor,
                vm_state_store=vm_state_store,
            )

        assert result["reboot_status_detected"] is True
        vm_state_store.set_needs_reboot.assert_called_once()


class TestBuildSubgraphWithRebootCheck:
    """Integration tests: sre_reboot_check wiring in build_patching_subgraph."""

    def test_graph_builds_with_reboot_check_enabled(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor, sre_reboot_check=True)
        compiled = graph.compile()
        assert compiled is not None

    def test_graph_builds_with_reboot_check_disabled(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor, sre_reboot_check=False)
        compiled = graph.compile()
        assert compiled is not None

    async def test_dry_run_skips_reboot_check(self) -> None:
        """DRY_RUN_OK exits before verify/reboot_check — reboot_status_detected absent."""
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("")   # no lock
            if call_count == 2:
                return _make_result("")   # refresh_package_lists
            if call_count == 3:
                return _make_result(
                    "Listing... Done\n"
                    "nginx/focal 1.18.0 amd64 [upgradable from: 1.17.0]\n"
                )
            if call_count == 4:
                return _make_result("nginx=1.17.0\n")  # snapshot
            return _make_result("simulated upgrade output")  # dry-run execute

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor, sre_reboot_check=True)
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

        # DRY_RUN_OK exits before reboot check
        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert result.get("reboot_status_detected") is None


# --- Service health node tests (1.3) ---

class TestServiceHealthPreNode:
    async def test_no_services_returns_empty_snapshot(self) -> None:
        executor = _make_executor(dry_run=False)
        result = await service_health_pre_node(
            _base_state(critical_services=[]),
            executor=executor,
        )
        assert result["service_pre_snapshot"] == {}

    async def test_captures_service_states(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=active\npostgresql=inactive\n")),
        ):
            result = await service_health_pre_node(
                _base_state(critical_services=["nginx", "postgresql"]),
                executor=executor,
            )
        assert result["service_pre_snapshot"]["nginx"] == "active"
        assert result["service_pre_snapshot"]["postgresql"] == "inactive"

    async def test_ssh_failure_returns_empty_snapshot(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("", exit_code=1)),
        ):
            result = await service_health_pre_node(
                _base_state(critical_services=["nginx"]),
                executor=executor,
            )
        assert result["service_pre_snapshot"] == {}


class TestServiceHealthPostNode:
    async def test_no_services_returns_empty_regressions(self) -> None:
        executor = _make_executor(dry_run=False)
        result = await service_health_post_node(
            _base_state(critical_services=[], service_pre_snapshot={}),
            executor=executor,
        )
        assert result["service_regressions"] == []

    async def test_detects_regression(self) -> None:
        executor = _make_executor(dry_run=False)
        # nginx was active before, now inactive
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=inactive\n")),
        ):
            result = await service_health_post_node(
                _base_state(
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "active"},
                ),
                executor=executor,
            )
        assert "nginx" in result["service_regressions"]

    async def test_no_regression_when_all_active(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=active\n")),
        ):
            result = await service_health_post_node(
                _base_state(
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "active"},
                ),
                executor=executor,
            )
        assert result["service_regressions"] == []

    async def test_pre_inactive_service_not_regression(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=inactive\n")),
        ):
            result = await service_health_post_node(
                _base_state(
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "inactive"},
                ),
                executor=executor,
            )
        assert result["service_regressions"] == []

    async def test_emits_audit_event_on_regression(self) -> None:
        from errander.models.events import EventType
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=False)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=inactive\n")),
        ):
            await service_health_post_node(
                _base_state(
                    vm_id="dev/web-01",
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "active"},
                ),
                executor=executor,
                audit_store=audit_store,
                batch_id="batch-77",
            )

        audit_store.log_event.assert_called_once()
        call_args = audit_store.log_event.call_args[0][0]
        assert call_args.event_type == EventType.SERVICE_HEALTH_REGRESSION
        assert call_args.batch_id == "batch-77"
        assert "nginx" in call_args.detail

    async def test_no_audit_event_when_no_regression(self) -> None:
        from errander.safety.audit import AuditStore

        executor = _make_executor(dry_run=False)
        audit_store = AsyncMock(spec=AuditStore)
        audit_store.log_event = AsyncMock()

        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=active\n")),
        ):
            await service_health_post_node(
                _base_state(
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "active"},
                ),
                executor=executor,
                audit_store=audit_store,
            )

        audit_store.log_event.assert_not_called()

    async def test_no_audit_store_no_error(self) -> None:
        executor = _make_executor(dry_run=False)
        with patch.object(
            executor, "execute",
            AsyncMock(return_value=_make_result("nginx=inactive\n")),
        ):
            result = await service_health_post_node(
                _base_state(
                    critical_services=["nginx"],
                    service_pre_snapshot={"nginx": "active"},
                ),
                executor=executor,
                audit_store=None,
            )
        assert "nginx" in result["service_regressions"]

    async def test_missing_pre_snapshot_returns_empty(self) -> None:
        executor = _make_executor(dry_run=False)
        result = await service_health_post_node(
            _base_state(critical_services=["nginx"]),
            executor=executor,
        )
        assert result["service_regressions"] == []


class TestBuildSubgraphWithServiceCheck:
    """Integration tests: sre_service_check wiring in build_patching_subgraph."""

    def test_graph_builds_with_service_check_enabled(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor, sre_service_check=True)
        compiled = graph.compile()
        assert compiled is not None

    def test_graph_builds_with_service_check_disabled(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(executor, sre_service_check=False)
        compiled = graph.compile()
        assert compiled is not None

    def test_graph_builds_all_sre_disabled(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_patching_subgraph(
            executor,
            sre_preflight_lock_check=False,
            sre_reboot_check=False,
            sre_service_check=False,
        )
        compiled = graph.compile()
        assert compiled is not None

    async def test_nothing_to_do_skips_service_pre(self) -> None:
        """Nothing-to-do path exits before service_pre is reached."""
        executor = _make_executor(dry_run=True)
        call_count = 0

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_result("")   # no lock
            if call_count == 2:
                return _make_result("")   # refresh_package_lists
            return _make_result("Listing... Done\n")  # list_upgradable → nothing to do

        with patch.object(executor, "execute", side_effect=mock_execute):
            graph = build_patching_subgraph(executor, sre_service_check=True)
            compiled = graph.compile()
            initial_state: PatchingGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "critical_services": ["nginx"],  # type: ignore[typeddict-item]
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }
            result = await compiled.ainvoke(initial_state)

        assert result["status"] == ActionStatus.SKIPPED.value
        # service_pre was never reached (nothing_to_do exits at assess)
        assert result.get("service_pre_snapshot") is None
