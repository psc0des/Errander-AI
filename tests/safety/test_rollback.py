"""Tests for per-action-type rollback logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHResult
from errander.models.actions import ActionType
from errander.safety.rollback import rollback_action


def _make_executor() -> SandboxExecutor:
    from errander.execution.ssh import SSHConnectionManager
    manager = SSHConnectionManager()
    return SandboxExecutor(ssh_manager=manager, dry_run=False)


def _make_ssh_result(stdout: str, success: bool = True) -> SSHResult:
    return SSHResult(stdout=stdout, stderr="", exit_code=0 if success else 1, command="")


class TestRollback:
    """Tests for rollback_action() strategy dispatch."""

    @pytest.mark.asyncio
    async def test_disk_cleanup_no_rollback_needed(self) -> None:
        success, detail = await rollback_action(ActionType.DISK_CLEANUP, "vm-01", {})
        assert success
        assert "no rollback needed" in detail.lower()

    @pytest.mark.asyncio
    async def test_log_rotation_no_rollback_needed(self) -> None:
        success, detail = await rollback_action(ActionType.LOG_ROTATION, "vm-01", {})
        assert success
        assert "no rollback needed" in detail.lower()

    @pytest.mark.asyncio
    async def test_backup_verify_read_only(self) -> None:
        success, detail = await rollback_action(ActionType.BACKUP_VERIFY, "vm-01", {})
        assert success
        assert "read-only" in detail.lower()

    @pytest.mark.asyncio
    async def test_docker_prune_legacy_type_unknown_strategy(self) -> None:
        # DOCKER_PRUNE is retained in the enum for audit-log read-back only.
        # It has no rollback strategy — rollback_action must fail cleanly.
        success, detail = await rollback_action(ActionType.DOCKER_PRUNE, "vm-01", {})
        assert not success
        assert "docker_prune" in detail.lower() or "unknown" in detail.lower()

    @pytest.mark.asyncio
    async def test_patching_rollback_requires_executor(self) -> None:
        """Patching rollback fails gracefully when no SSH executor is provided."""
        snapshot = {"curl": "7.81.0", "nginx": "1.18.0"}
        success, detail = await rollback_action(ActionType.PATCHING, "vm-01", snapshot)
        assert not success
        assert "executor" in detail.lower()

    @pytest.mark.asyncio
    async def test_patching_rollback_empty_snapshot_fails(self) -> None:
        """Patching rollback with empty snapshot fails gracefully (no versions to restore)."""
        success, detail = await rollback_action(ActionType.PATCHING, "vm-01", {})
        assert not success


class TestDnfRollbackVersionVerification:
    """DNF rollback must compare restored versions against the snapshot, not just check SSH success."""

    @pytest.mark.asyncio
    async def test_dnf_rollback_verified_when_versions_match(self) -> None:
        """Rollback succeeds when rpm output confirms snapshot versions are restored."""
        snapshot = {"bash": "5.1.8-6.el9", "curl": "7.76.1-29.el9"}
        executor = _make_executor()
        # downgrade succeeds + rpm verification shows snapshot versions restored
        rpm_output = "bash=5.1.8-6.el9\ncurl=7.76.1-29.el9\n"
        ssh_responses = [
            _make_ssh_result("Complete!"),    # dnf downgrade
            _make_ssh_result(rpm_output),     # rpm -q verification
        ]

        with patch.object(executor, "execute", AsyncMock(side_effect=ssh_responses)):
            success, detail = await rollback_action(
                ActionType.PATCHING, "vm-01", snapshot, executor=executor,
                hostname="10.0.1.10", username="user", key_path="/key",
                os_family="rhel",
            )

        assert success, f"Rollback should succeed when versions match: {detail}"
        assert "versions verified" in detail.lower()

    @pytest.mark.asyncio
    async def test_dnf_rollback_fails_when_versions_mismatch(self) -> None:
        """Rollback fails when rpm output shows versions do not match snapshot."""
        snapshot = {"bash": "5.1.8-6.el9", "curl": "7.76.1-29.el9"}
        executor = _make_executor()
        # Versions NOT restored — still at newer version
        rpm_output = "bash=5.2.0-1.el9\ncurl=7.76.1-29.el9\n"
        ssh_responses = [
            _make_ssh_result("Complete!"),    # dnf downgrade
            _make_ssh_result(rpm_output),     # rpm -q — bash version wrong
        ]

        with patch.object(executor, "execute", AsyncMock(side_effect=ssh_responses)):
            success, detail = await rollback_action(
                ActionType.PATCHING, "vm-01", snapshot, executor=executor,
                hostname="10.0.1.10", username="user", key_path="/key",
                os_family="rhel",
            )

        assert not success, "Rollback should fail when installed versions don't match snapshot"
        assert "mismatch" in detail.lower()

    @pytest.mark.asyncio
    async def test_dnf_rollback_fails_when_package_missing_from_rpm_output(self) -> None:
        """Rollback fails when a snapshot package is absent from rpm output."""
        snapshot = {"bash": "5.1.8-6.el9", "curl": "7.76.1-29.el9"}
        executor = _make_executor()
        # curl missing from rpm output entirely
        rpm_output = "bash=5.1.8-6.el9\n"
        ssh_responses = [
            _make_ssh_result("Complete!"),
            _make_ssh_result(rpm_output),
        ]

        with patch.object(executor, "execute", AsyncMock(side_effect=ssh_responses)):
            success, detail = await rollback_action(
                ActionType.PATCHING, "vm-01", snapshot, executor=executor,
                hostname="10.0.1.10", username="user", key_path="/key",
                os_family="rhel",
            )

        assert not success
        assert "missing" in detail.lower() or "mismatch" in detail.lower()
