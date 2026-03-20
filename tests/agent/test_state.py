"""Tests for agent state definitions."""

from __future__ import annotations

from automaint.agent.state import BatchState, VMMaintenanceState


class TestState:
    """Tests for state dataclass construction and reducers."""

    def test_batch_state_defaults(self) -> None:
        """BatchState should have sensible defaults."""
        state = BatchState()
        assert state.dry_run is True
        assert state.vm_results == []
        assert state.approved is None

    def test_vm_state_defaults(self) -> None:
        """VMMaintenanceState should have sensible defaults."""
        state = VMMaintenanceState(vm_id="test-1")
        assert state.vm_id == "test-1"
        assert state.dry_run is True
        assert state.planned_actions == []
        assert state.current_action_index == 0
