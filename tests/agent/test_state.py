"""Tests verifying state TypedDicts exist in their canonical modules."""

from __future__ import annotations


class TestStateDefinitions:
    """Verify that canonical state types are importable from their graph modules."""

    def test_batch_graph_state_importable(self) -> None:
        from errander.agent.graph import BatchGraphState
        assert "batch_id" in BatchGraphState.__annotations__

    def test_vm_graph_state_importable(self) -> None:
        from errander.agent.vm_graph import VMGraphState
        assert "vm_id" in VMGraphState.__annotations__

    def test_disk_cleanup_state_importable(self) -> None:
        from errander.agent.subgraphs.disk_cleanup import DiskCleanupGraphState
        assert "vm_id" in DiskCleanupGraphState.__annotations__
