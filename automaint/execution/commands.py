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
        raise NotImplementedError

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        raise NotImplementedError

    def install_version(self, package: str, version: str) -> str:
        raise NotImplementedError

    def list_installed_versions(self, packages: list[str]) -> str:
        raise NotImplementedError

    def clean_cache(self) -> str:
        raise NotImplementedError

    def autoremove(self) -> str:
        raise NotImplementedError


class DnfManager(PackageManager):
    """Package manager for RHEL systems (dnf)."""

    def list_upgradable(self) -> str:
        raise NotImplementedError

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        raise NotImplementedError

    def install_version(self, package: str, version: str) -> str:
        raise NotImplementedError

    def list_installed_versions(self, packages: list[str]) -> str:
        raise NotImplementedError

    def clean_cache(self) -> str:
        raise NotImplementedError

    def autoremove(self) -> str:
        raise NotImplementedError


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
