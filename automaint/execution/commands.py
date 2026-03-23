"""Strategy pattern for OS-specific command abstraction.

Each supported OS family (Ubuntu/Debian via apt, RHEL via dnf) implements
the PackageManager interface. The correct implementation is selected at
runtime based on OS detection.

This ensures action sub-graphs use a uniform interface regardless of the
target OS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from automaint.models.vm import OSFamily


class PackageManager(ABC):
    """Abstract interface for OS package management commands.

    Implementations generate shell commands as strings — they do NOT
    execute them directly. Execution goes through the SSH layer.
    """

    @abstractmethod
    def list_upgradable(self) -> str:
        """Return command to list packages with available updates."""

    @abstractmethod
    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        """Return command to upgrade all packages, excluding patterns.

        Args:
            exclude_patterns: Package name patterns to exclude (e.g., kernel-*).
        """

    @abstractmethod
    def install_version(self, package: str, version: str) -> str:
        """Return command to install a specific package version (for rollback).

        Args:
            package: Package name.
            version: Exact version to install.
        """

    @abstractmethod
    def list_installed_versions(self, packages: list[str]) -> str:
        """Return command to list currently installed versions of packages.

        Args:
            packages: Package names to query.
        """

    @abstractmethod
    def clean_cache(self) -> str:
        """Return command to clean package manager cache."""

    @abstractmethod
    def autoremove(self) -> str:
        """Return command to remove orphaned dependencies."""


class AptManager(PackageManager):
    """Package manager for Debian/Ubuntu systems (apt)."""

    def list_upgradable(self) -> str:
        return "apt list --upgradable 2>/dev/null"

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        if exclude_patterns:
            holds = " && ".join(
                f"apt-mark hold {p}" for p in exclude_patterns
            )
            return f"{holds} && apt-get upgrade -y && " + " && ".join(
                f"apt-mark unhold {p}" for p in exclude_patterns
            )
        return "apt-get upgrade -y"

    def install_version(self, package: str, version: str) -> str:
        return f"apt-get install -y --allow-downgrades {package}={version}"

    def list_installed_versions(self, packages: list[str]) -> str:
        pkg_list = " ".join(packages)
        return f"dpkg-query -W -f='${{Package}}=${{Version}}\\n' {pkg_list} 2>/dev/null"

    def clean_cache(self) -> str:
        return "apt-get clean"

    def autoremove(self) -> str:
        return "apt-get autoremove -y"

    def simulate_upgrade(self) -> str:
        """Return command to simulate an upgrade (dry-run)."""
        return "apt-get --simulate upgrade"

    def cache_size(self) -> str:
        """Return command to check package cache size."""
        return "du -sh /var/cache/apt 2>/dev/null || echo '0\t/var/cache/apt'"


class DnfManager(PackageManager):
    """Package manager for RHEL systems (dnf)."""

    def list_upgradable(self) -> str:
        return "dnf check-update --quiet 2>/dev/null || true"

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        if exclude_patterns:
            excludes = " ".join(f"--exclude={p}" for p in exclude_patterns)
            return f"dnf upgrade -y {excludes}"
        return "dnf upgrade -y"

    def install_version(self, package: str, version: str) -> str:
        return f"dnf downgrade -y {package}-{version}"

    def list_installed_versions(self, packages: list[str]) -> str:
        pkg_list = " ".join(packages)
        return f"rpm -q --qf '%{{NAME}}=%{{VERSION}}-%{{RELEASE}}\\n' {pkg_list} 2>/dev/null"

    def clean_cache(self) -> str:
        return "dnf clean all"

    def autoremove(self) -> str:
        return "dnf autoremove -y"

    def simulate_upgrade(self) -> str:
        """Return command to simulate an upgrade (dry-run)."""
        return "dnf check-update"

    def cache_size(self) -> str:
        """Return command to check package cache size."""
        return "du -sh /var/cache/dnf 2>/dev/null || echo '0\t/var/cache/dnf'"


def get_package_manager(os_family: OSFamily) -> PackageManager:
    """Return the appropriate PackageManager for the OS family.

    Args:
        os_family: Detected OS family.

    Returns:
        PackageManager implementation.

    Raises:
        ValueError: If OS family is not supported.
    """
    managers: dict[OSFamily, type[PackageManager]] = {
        OSFamily.UBUNTU: AptManager,
        OSFamily.DEBIAN: AptManager,
        OSFamily.RHEL: DnfManager,
    }
    manager_cls = managers.get(os_family)
    if manager_cls is None:
        msg = f"Unsupported OS family: {os_family}"
        raise ValueError(msg)
    return manager_cls()
