"""Shared pytest fixtures for Errander-AI tests.

Provides common test fixtures:
- Sample VMTarget and VMInfo objects
- Mock SSH connections
- In-memory audit database
- Fake Slack client
- Fake LLM client (returns deterministic responses)
"""

from __future__ import annotations

import os

import pytest

from errander.models.vm import OSFamily, VMTarget


@pytest.fixture(autouse=True)
def clean_errander_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all ERRANDER_* env vars before each test.

    Prevents real .env values (e.g. ERRANDER_LLM_MODEL, ERRANDER_UI_PASSWORD)
    exported to the shell from leaking into tests that expect a clean slate.
    Tests that need specific values set them explicitly via monkeypatch.setenv.
    """
    for key in list(os.environ.keys()):
        if key.startswith("ERRANDER_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def sample_vm_target() -> VMTarget:
    """A sample Ubuntu VM target for testing."""
    return VMTarget(
        vm_id="test-vm-1",
        hostname="10.0.1.10",
        ssh_user="errander-ai",
        ssh_key_path="/tmp/test_key",
        os_family=OSFamily.UBUNTU,
        policy="moderate",
        tags={"env": "test"},
    )
