"""Shared pytest fixtures for AutoMaint tests.

Provides common test fixtures:
- Sample VMTarget and VMInfo objects
- Mock SSH connections
- In-memory audit database
- Fake Slack client
- Fake LLM client (returns deterministic responses)
"""

from __future__ import annotations

import pytest

from automaint.models.vm import OSFamily, VMTarget


@pytest.fixture
def sample_vm_target() -> VMTarget:
    """A sample Ubuntu VM target for testing."""
    return VMTarget(
        vm_id="test-vm-1",
        hostname="10.0.1.10",
        ssh_user="automaint",
        ssh_key_path="/tmp/test_key",
        os_family=OSFamily.UBUNTU,
        policy="moderate",
        tags={"env": "test"},
    )
