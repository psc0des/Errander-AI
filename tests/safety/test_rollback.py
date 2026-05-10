"""Tests for per-action-type rollback logic."""

from __future__ import annotations

import pytest

from errander.models.actions import ActionType
from errander.safety.rollback import rollback_action


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
    async def test_docker_prune_re_pull(self) -> None:
        success, detail = await rollback_action(ActionType.DOCKER_PRUNE, "vm-01", {})
        assert success
        assert "re-pull" in detail.lower()

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
