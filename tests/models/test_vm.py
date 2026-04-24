"""Tests for VM models."""

from __future__ import annotations

from errander.models.vm import OSFamily, VMTarget


class TestVMModels:
    """Tests for VM model construction."""

    def test_vm_target_frozen(self) -> None:
        target = VMTarget(
            vm_id="vm-1",
            hostname="10.0.1.1",
            ssh_user="admin",
            ssh_key_path="/keys/id_rsa",
            os_family=OSFamily.UBUNTU,
        )
        assert target.vm_id == "vm-1"
        assert target.policy == "moderate"  # default
