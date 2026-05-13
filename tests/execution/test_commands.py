"""Tests for OS-specific command abstraction."""

from __future__ import annotations

import pytest

from errander.execution.commands import AptManager, DnfManager, get_package_manager
from errander.models.vm import OSFamily


class TestGetPackageManager:
    """Tests for package manager factory."""

    def test_ubuntu_returns_apt(self) -> None:
        assert isinstance(get_package_manager(OSFamily.UBUNTU), AptManager)

    def test_debian_returns_apt(self) -> None:
        assert isinstance(get_package_manager(OSFamily.DEBIAN), AptManager)

    def test_rhel_returns_dnf(self) -> None:
        assert isinstance(get_package_manager(OSFamily.RHEL), DnfManager)


class TestDetectLock:
    """Tests for PackageManager.detect_lock() shell command generation."""

    def test_apt_detect_lock_returns_string(self) -> None:
        cmd = AptManager().detect_lock()
        assert isinstance(cmd, str)
        assert len(cmd) > 0

    def test_apt_detect_lock_references_dpkg_lock(self) -> None:
        cmd = AptManager().detect_lock()
        assert "/var/lib/dpkg/lock" in cmd

    def test_apt_detect_lock_references_apt_lists_lock(self) -> None:
        cmd = AptManager().detect_lock()
        assert "/var/lib/apt/lists/lock" in cmd

    def test_apt_detect_lock_uses_fuser(self) -> None:
        cmd = AptManager().detect_lock()
        assert "fuser" in cmd

    def test_apt_detect_lock_reads_proc_comm(self) -> None:
        cmd = AptManager().detect_lock()
        assert "/proc/" in cmd and "comm" in cmd

    def test_dnf_detect_lock_returns_string(self) -> None:
        cmd = DnfManager().detect_lock()
        assert isinstance(cmd, str)
        assert len(cmd) > 0

    def test_dnf_detect_lock_references_dnf_pid(self) -> None:
        cmd = DnfManager().detect_lock()
        assert "/var/run/dnf.pid" in cmd

    def test_dnf_detect_lock_references_yum_pid(self) -> None:
        cmd = DnfManager().detect_lock()
        assert "/var/run/yum.pid" in cmd

    def test_dnf_detect_lock_uses_kill_zero(self) -> None:
        cmd = DnfManager().detect_lock()
        assert "kill -0" in cmd

    def test_both_output_same_format_hint(self) -> None:
        for pm in (AptManager(), DnfManager()):
            cmd = pm.detect_lock()
            # Both commands should produce "pid=N cmd=X" style output
            assert "pid=" in cmd
            assert "cmd=" in cmd
