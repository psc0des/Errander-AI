"""Phase F4 tests: post_cleanup_disk_gate_node."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.vm_graph import post_cleanup_disk_gate_node
from errander.db.core import AsyncDatabase
from errander.models.actions import ActionStatus
from errander.models.events import EventType
from errander.safety.audit import AuditStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh_result(stdout: str = "", exit_code: int = 0) -> object:
    m = MagicMock()
    m.success = exit_code == 0
    m.stdout = stdout
    m.stderr = ""
    m.exit_code = exit_code
    return m


def _make_ssh_manager(disk_pct: int | None = 50) -> object:
    mgr = MagicMock()
    stdout = str(disk_pct) if disk_pct is not None else ""
    mgr.execute = AsyncMock(return_value=_ssh_result(stdout=stdout))
    return mgr


def _make_state(
    last_action_type: str = "disk_cleanup",
    next_action_type: str = "patching",
    *,
    extra_results: list[dict] | None = None,
) -> dict:
    results = extra_results or []
    results = results + [{"action_type": last_action_type, "status": "succeeded", "vm_id": "vm-01"}]
    return {
        "vm_id": "vm-01",
        "batch_id": "batch-gate-test",
        "hostname": "10.0.0.1",
        "ssh_user": "errander-ai",
        "ssh_key_path": "/key",
        "results": results,
        "planned_actions": [{"action_type": next_action_type}],
        "current_action_index": 0,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPostCleanupDiskGateNode:
    @pytest.mark.asyncio
    async def test_no_op_when_last_action_not_cleanup(self) -> None:
        state = _make_state(last_action_type="patching", next_action_type="patching")
        ssh = _make_ssh_manager(disk_pct=96)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_op_when_next_action_not_patching(self) -> None:
        state = _make_state(last_action_type="disk_cleanup", next_action_type="docker_prune")
        ssh = _make_ssh_manager(disk_pct=96)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_op_when_results_empty(self) -> None:
        state = _make_state()
        state["results"] = []
        ssh = _make_ssh_manager(disk_pct=96)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_op_when_disk_below_90(self) -> None:
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=70)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_op_when_disk_at_94(self) -> None:
        """90–94%: warn only, allow patching."""
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=94)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}

    @pytest.mark.asyncio
    async def test_blocks_at_95_pct(self) -> None:
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=95)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert "results" in result
        assert result["current_action_index"] == 1
        skipped = result["results"][-1]
        assert skipped["action_type"] == "patching"
        assert skipped["status"] == ActionStatus.SKIPPED.value
        assert "95%" in skipped["detail"]

    @pytest.mark.asyncio
    async def test_blocks_at_98_pct(self) -> None:
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=98)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result["current_action_index"] == 1

    @pytest.mark.asyncio
    async def test_log_rotation_also_triggers_gate(self) -> None:
        state = _make_state(last_action_type="log_rotation", next_action_type="patching")
        ssh = _make_ssh_manager(disk_pct=96)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert "results" in result

    @pytest.mark.asyncio
    async def test_ssh_failure_passes_silently(self) -> None:
        state = _make_state()
        mgr = MagicMock()
        mgr.execute = AsyncMock(side_effect=ConnectionError("refused"))
        result = await post_cleanup_disk_gate_node(state, ssh_manager=mgr)
        assert result == {}

    @pytest.mark.asyncio
    async def test_blocked_emits_audit_event(self) -> None:
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=96)
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            await post_cleanup_disk_gate_node(state, ssh_manager=ssh, audit_store=store)
            events = await store.get_events(batch_id="batch-gate-test")

        gate_events = [e for e in events if e.event_type == EventType.DISK_GATE_BLOCKED]
        assert len(gate_events) == 1
        assert gate_events[0].metadata["disk_pct"] == 96

    @pytest.mark.asyncio
    async def test_no_audit_event_when_disk_ok(self) -> None:
        state = _make_state()
        ssh = _make_ssh_manager(disk_pct=70)
        async with AuditStore(AsyncDatabase(":memory:")) as store:
            await post_cleanup_disk_gate_node(state, ssh_manager=ssh, audit_store=store)
            events = await store.get_events(batch_id="batch-gate-test")

        gate_events = [e for e in events if e.event_type == EventType.DISK_GATE_BLOCKED]
        assert len(gate_events) == 0

    @pytest.mark.asyncio
    async def test_no_op_when_no_planned_actions_left(self) -> None:
        state = _make_state()
        state["current_action_index"] = 5  # past end of planned_actions
        ssh = _make_ssh_manager(disk_pct=98)
        result = await post_cleanup_disk_gate_node(state, ssh_manager=ssh)
        assert result == {}
