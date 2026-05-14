"""Assert that all privileged command generators use sudo -n with absolute paths.

Read-only commands (list_upgradable, list_installed_versions, cache_size,
detect_lock, simulate_upgrade for dnf) must NOT have sudo -n.
"""
from __future__ import annotations

import pytest

from errander.execution.commands import AptManager, DnfManager


@pytest.fixture()
def apt() -> AptManager:
    return AptManager()


@pytest.fixture()
def dnf() -> DnfManager:
    return DnfManager()


# --- AptManager privileged commands ---

def test_apt_refresh_uses_sudo(apt: AptManager) -> None:
    cmd = apt.refresh_package_lists()
    assert cmd.startswith("sudo -n /usr/bin/apt-get")


def test_apt_upgrade_all_uses_sudo(apt: AptManager) -> None:
    cmd = apt.upgrade_all()
    assert "sudo -n /usr/bin/apt-get upgrade" in cmd


def test_apt_upgrade_all_apt_mark_uses_sudo(apt: AptManager) -> None:
    cmd = apt.upgrade_all()
    assert "sudo -n /usr/bin/apt-mark hold" in cmd
    assert "sudo -n /usr/bin/apt-mark unhold" in cmd


def test_apt_install_version_uses_sudo(apt: AptManager) -> None:
    cmd = apt.install_version("nginx", "1.18.0-0ubuntu1")
    assert "sudo -n" in cmd
    assert "/usr/bin/apt-get install" in cmd


def test_apt_clean_cache_uses_sudo(apt: AptManager) -> None:
    cmd = apt.clean_cache()
    assert cmd.startswith("sudo -n /usr/bin/apt-get clean")


def test_apt_autoremove_uses_sudo(apt: AptManager) -> None:
    cmd = apt.autoremove()
    assert cmd.startswith("sudo -n /usr/bin/apt-get autoremove")


def test_apt_simulate_upgrade_uses_sudo(apt: AptManager) -> None:
    cmd = apt.simulate_upgrade()
    assert cmd.startswith("sudo -n /usr/bin/apt-get --simulate")


# --- AptManager read-only commands (must NOT have sudo -n) ---

def test_apt_list_upgradable_no_sudo(apt: AptManager) -> None:
    cmd = apt.list_upgradable()
    assert "sudo" not in cmd


def test_apt_list_installed_versions_no_sudo(apt: AptManager) -> None:
    cmd = apt.list_installed_versions(["nginx"])
    assert "sudo" not in cmd


def test_apt_cache_size_no_sudo(apt: AptManager) -> None:
    cmd = apt.cache_size()
    assert "sudo" not in cmd


# --- DnfManager privileged commands ---

def test_dnf_refresh_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.refresh_package_lists()
    assert cmd.startswith("sudo -n /usr/bin/dnf makecache")


def test_dnf_upgrade_all_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.upgrade_all()
    assert "sudo -n /usr/bin/dnf upgrade" in cmd


def test_dnf_upgrade_all_versionlock_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.upgrade_all()
    assert "sudo -n /usr/bin/dnf versionlock add" in cmd
    assert "sudo -n /usr/bin/dnf versionlock delete" in cmd


def test_dnf_install_version_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.install_version("nginx", "1.20.0")
    assert cmd.startswith("sudo -n /usr/bin/dnf downgrade")


def test_dnf_clean_cache_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.clean_cache()
    assert cmd.startswith("sudo -n /usr/bin/dnf clean")


def test_dnf_autoremove_uses_sudo(dnf: DnfManager) -> None:
    cmd = dnf.autoremove()
    assert cmd.startswith("sudo -n /usr/bin/dnf autoremove")


# --- DnfManager read-only commands (must NOT have sudo -n) ---

def test_dnf_list_upgradable_no_sudo(dnf: DnfManager) -> None:
    cmd = dnf.list_upgradable()
    assert "sudo" not in cmd


def test_dnf_list_installed_versions_no_sudo(dnf: DnfManager) -> None:
    cmd = dnf.list_installed_versions(["httpd"])
    assert "sudo" not in cmd


def test_dnf_simulate_upgrade_no_sudo(dnf: DnfManager) -> None:
    cmd = dnf.simulate_upgrade()
    assert "sudo" not in cmd
