"""Tests for OS-specific command abstraction."""

from __future__ import annotations

import pytest

from automaint.execution.commands import AptManager, DnfManager, get_package_manager
from automaint.models.vm import OSFamily


class TestGetPackageManager:
    """Tests for package manager factory."""

    def test_ubuntu_returns_apt(self) -> None:
        assert isinstance(get_package_manager(OSFamily.UBUNTU), AptManager)

    def test_debian_returns_apt(self) -> None:
        assert isinstance(get_package_manager(OSFamily.DEBIAN), AptManager)

    def test_rhel_returns_dnf(self) -> None:
        assert isinstance(get_package_manager(OSFamily.RHEL), DnfManager)
