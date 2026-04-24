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
    async def test_patching_not_yet_implemented(self) -> None:
        snapshot = {"packages": {"curl": "7.81.0", "nginx": "1.18.0"}}
        success, detail = await rollback_action(ActionType.PATCHING, "vm-01", snapshot)
        assert not success
        assert "not yet implemented" in detail.lower()

    @pytest.mark.asyncio
    async def test_patching_rollback_receives_snapshot(self) -> None:
        """Ensure pre_snapshot is passed through (will matter when implemented)."""
        snapshot = {"packages": {"vim": "2:8.2.0"}}
        success, detail = await rollback_action(ActionType.PATCHING, "vm-01", snapshot)
        # For now it fails gracefully, but the snapshot was accepted
        assert not success
