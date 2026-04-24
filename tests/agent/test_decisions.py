"""Tests for LLM decision logic and hardcoded fallbacks."""

from __future__ import annotations

import pytest

from errander.agent.decisions import (
    DEFAULT_PRIORITY,
    analyze_failure,
    filter_applicable_actions,
    generate_report,
    prioritize_actions,
)
from errander.models.actions import (
    ActionResult,
    ActionStatus,
    ActionType,
    RiskTier,
)
from errander.models.vm import OSFamily, VMInfo


def _make_vm_info(**overrides: object) -> VMInfo:
    """Build a VMInfo with sensible defaults."""
    defaults: dict[str, object] = {
        "os_family": OSFamily.UBUNTU,
        "os_version": "Ubuntu 22.04.3 LTS",
        "disk_usage": {"/": 45.0},
        "docker_available": True,
        "pending_packages": 5,
        "uptime_seconds": 86400.0,
    }
    defaults.update(overrides)
    return VMInfo(**defaults)  # type: ignore[arg-type]


def _make_result(
    action_type: ActionType = ActionType.DISK_CLEANUP,
    status: ActionStatus = ActionStatus.SUCCESS,
    vm_id: str = "dev/web-01",
    **overrides: object,
) -> ActionResult:
    """Build an ActionResult with sensible defaults."""
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    defaults: dict[str, object] = {
        "action_type": action_type,
        "status": status,
        "vm_id": vm_id,
        "started_at": now,
        "completed_at": now,
        "detail": "",
        "error": None,
        "rollback_detail": None,
    }
    defaults.update(overrides)
    return ActionResult(**defaults)  # type: ignore[arg-type]


class TestFilterApplicableActions:
    """Tests for filtering actions by VM state."""

    def test_all_applicable_when_everything_available(self) -> None:
        vm_info = _make_vm_info(docker_available=True, pending_packages=5)
        result = filter_applicable_actions(list(ActionType), vm_info)
        assert set(result) == set(ActionType)

    def test_docker_prune_excluded_when_no_docker(self) -> None:
        vm_info = _make_vm_info(docker_available=False)
        result = filter_applicable_actions(list(ActionType), vm_info)
        assert ActionType.DOCKER_PRUNE not in result

    def test_patching_excluded_when_no_pending(self) -> None:
        vm_info = _make_vm_info(pending_packages=0)
        result = filter_applicable_actions(list(ActionType), vm_info)
        assert ActionType.PATCHING not in result

    def test_disk_cleanup_always_applicable(self) -> None:
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)
        result = filter_applicable_actions([ActionType.DISK_CLEANUP], vm_info)
        assert ActionType.DISK_CLEANUP in result

    def test_log_rotation_always_applicable(self) -> None:
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)
        result = filter_applicable_actions([ActionType.LOG_ROTATION], vm_info)
        assert ActionType.LOG_ROTATION in result

    def test_backup_verify_always_applicable(self) -> None:
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)
        result = filter_applicable_actions([ActionType.BACKUP_VERIFY], vm_info)
        assert ActionType.BACKUP_VERIFY in result


class TestPrioritizeActions:
    """Tests for hardcoded action prioritization."""

    @pytest.mark.asyncio
    async def test_default_priority_order(self) -> None:
        vm_info = _make_vm_info()
        actions = await prioritize_actions(vm_info)
        action_types = [a.action_type for a in actions]
        assert action_types == list(DEFAULT_PRIORITY)

    @pytest.mark.asyncio
    async def test_filters_inapplicable(self) -> None:
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)
        actions = await prioritize_actions(vm_info)
        action_types = [a.action_type for a in actions]
        assert ActionType.DOCKER_PRUNE not in action_types
        assert ActionType.PATCHING not in action_types

    @pytest.mark.asyncio
    async def test_risk_tiers_assigned(self) -> None:
        vm_info = _make_vm_info()
        actions = await prioritize_actions(vm_info)
        disk = next(a for a in actions if a.action_type == ActionType.DISK_CLEANUP)
        assert disk.risk_tier == RiskTier.LOW
        patch = next(a for a in actions if a.action_type == ActionType.PATCHING)
        assert patch.risk_tier == RiskTier.MEDIUM

    @pytest.mark.asyncio
    async def test_custom_action_list(self) -> None:
        vm_info = _make_vm_info()
        actions = await prioritize_actions(
            vm_info,
            available_actions=[ActionType.PATCHING, ActionType.DISK_CLEANUP],
        )
        # Disk cleanup should come before patching (lower risk)
        action_types = [a.action_type for a in actions]
        assert action_types == [ActionType.DISK_CLEANUP, ActionType.PATCHING]

    @pytest.mark.asyncio
    async def test_empty_when_nothing_applicable(self) -> None:
        vm_info = _make_vm_info(docker_available=False, pending_packages=0)
        actions = await prioritize_actions(
            vm_info,
            available_actions=[ActionType.DOCKER_PRUNE, ActionType.PATCHING],
        )
        assert actions == []


class TestAnalyzeFailure:
    """Tests for hardcoded failure analysis heuristics."""

    @pytest.mark.asyncio
    async def test_timeout_suggests_retry(self) -> None:
        result = await analyze_failure("patching", "Connection timeout after 30s", {})
        assert result == "retry"

    @pytest.mark.asyncio
    async def test_connection_error_suggests_retry(self) -> None:
        result = await analyze_failure("disk_cleanup", "SSH connection refused", {})
        assert result == "retry"

    @pytest.mark.asyncio
    async def test_temporary_error_suggests_retry(self) -> None:
        result = await analyze_failure("patching", "Temporary failure resolving host", {})
        assert result == "retry"

    @pytest.mark.asyncio
    async def test_dpkg_error_on_patching_suggests_rollback(self) -> None:
        result = await analyze_failure("patching", "dpkg: error processing package", {})
        assert result == "rollback"

    @pytest.mark.asyncio
    async def test_broken_deps_on_patching_suggests_rollback(self) -> None:
        result = await analyze_failure("patching", "broken dependencies detected", {})
        assert result == "rollback"

    @pytest.mark.asyncio
    async def test_unknown_error_suggests_escalate(self) -> None:
        result = await analyze_failure("disk_cleanup", "Permission denied", {})
        assert result == "escalate"

    @pytest.mark.asyncio
    async def test_dpkg_on_non_patching_escalates(self) -> None:
        """dpkg errors only trigger rollback for patching actions."""
        result = await analyze_failure("disk_cleanup", "dpkg lock held", {})
        assert result == "escalate"


class TestGenerateReport:
    """Tests for template-based report generation."""

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        report = await generate_report([], batch_id="batch-001")
        assert "batch-001" in report
        assert "Total actions: 0" in report

    @pytest.mark.asyncio
    async def test_single_success(self) -> None:
        results = [_make_result(status=ActionStatus.SUCCESS)]
        report = await generate_report(results, batch_id="batch-002")
        assert "Succeeded: 1" in report
        assert "dev/web-01" in report

    @pytest.mark.asyncio
    async def test_mixed_statuses(self) -> None:
        results = [
            _make_result(status=ActionStatus.SUCCESS, vm_id="dev/web-01"),
            _make_result(
                action_type=ActionType.PATCHING,
                status=ActionStatus.FAILED,
                vm_id="dev/web-01",
                error="dpkg error",
            ),
            _make_result(
                status=ActionStatus.DRY_RUN_OK,
                vm_id="dev/db-01",
            ),
        ]
        report = await generate_report(results, batch_id="batch-003")
        assert "Total actions: 3" in report
        assert "Succeeded: 1" in report
        assert "Failed: 1" in report
        assert "Dry-run OK: 1" in report
        assert "VMs processed: 2" in report

    @pytest.mark.asyncio
    async def test_error_detail_included(self) -> None:
        results = [
            _make_result(
                status=ActionStatus.FAILED,
                error="Permission denied",
            ),
        ]
        report = await generate_report(results)
        assert "Permission denied" in report
        assert "[FAIL]" in report

    @pytest.mark.asyncio
    async def test_dry_run_icon(self) -> None:
        results = [_make_result(status=ActionStatus.DRY_RUN_OK)]
        report = await generate_report(results)
        assert "[DRY]" in report
