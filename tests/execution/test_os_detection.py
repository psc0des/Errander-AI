"""Tests for OS detection and verification."""

from __future__ import annotations

from automaint.execution.os_detection import verify_os_match
from automaint.models.vm import OSFamily


class TestOSDetection:
    """Tests for OS detection logic."""

    def test_verify_os_match(self) -> None:
        assert verify_os_match(OSFamily.UBUNTU, OSFamily.UBUNTU)
        assert not verify_os_match(OSFamily.UBUNTU, OSFamily.RHEL)
