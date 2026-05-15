"""Strategy pattern for OS-specific command abstraction.

Each supported OS family (Ubuntu/Debian via apt, RHEL via dnf) implements
the PackageManager interface. The correct implementation is selected at
runtime based on OS detection.

This ensures action sub-graphs use a uniform interface regardless of the
target OS.

All command construction uses safe_path()/safe_pkg()/safe_ver() from
command_builder — never raw f-strings with untrusted input (finding #10).
"""

from __future__ import annotations

import re
import shlex
from abc import ABC, abstractmethod

from errander.execution.command_builder import safe_pkg, safe_ver
from errander.execution.privilege import privileged
from errander.models.vm import OSFamily

# Kernel package patterns for Python-side filtering (finding #11).
# These are applied to dpkg-query / rpm -q output in Python — NOT passed
# to apt-mark as globs (apt-mark does not expand globs).
_KERNEL_PKG_RE = re.compile(
    r"^(linux-(image|headers|modules|generic|aws|azure|gcp|kvm|raspi|oem)|"
    r"kernel(-core|-modules|-devel|-headers)?)"
)


class PackageManager(ABC):
    """Abstract interface for OS package management commands.

    Implementations generate shell commands as strings — they do NOT
    execute them directly. Execution goes through the SSH layer.
    """

    @abstractmethod
    def refresh_package_lists(self) -> str:
        """Return command to refresh the local package index from upstream repos."""

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

    @abstractmethod
    def detect_lock(self) -> str:
        """Return shell command that prints holder info if a pkg manager lock is held.

        stdout empty  → no lock held.
        stdout present → lock held; format: 'pid=<N> cmd=<name>'
        Always exits 0 — never causes the caller's SSH success check to fail.
        """


class AptManager(PackageManager):
    """Package manager for Debian/Ubuntu systems (apt)."""

    def refresh_package_lists(self) -> str:
        return privileged("/usr/bin/apt-get update -qq")

    def list_upgradable(self) -> str:
        return "apt list --upgradable 2>/dev/null"

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        # Finding #11: apt-mark does not expand globs — we hold exact kernel
        # package names queried from dpkg-query and filtered in Python.
        # This two-step shell script queries installed kernel packages, holds
        # them, upgrades everything else, then unholds.
        # dpkg-query is read-only — no sudo needed.
        kernel_query = (
            "dpkg-query -W -f='${Package}\\n' 2>/dev/null"
            " | grep -E "
            + shlex.quote(
                r"^(linux-(image|headers|modules|generic|aws|azure|gcp|kvm|raspi|oem)"
                r"|kernel(-core|-modules|-devel|-headers)?)"
            )
        )
        hold_cmd = (
            f"KERNEL_PKGS=$({kernel_query}); "
            "[ -n \"$KERNEL_PKGS\" ] && echo \"$KERNEL_PKGS\" | "
            "xargs sudo -n /usr/bin/apt-mark hold 2>/dev/null || true; "
            "sudo -n /usr/bin/apt-get upgrade -y; "
            "APT_RC=$?; "
            "[ -n \"$KERNEL_PKGS\" ] && echo \"$KERNEL_PKGS\" | "
            "xargs sudo -n /usr/bin/apt-mark unhold 2>/dev/null || true; "
            "exit $APT_RC"
        )
        return hold_cmd

    def query_kernel_packages(self) -> str:
        """Return command that prints exact installed kernel package names (one per line)."""
        return (
            "dpkg-query -W -f='${Package}\\n' 2>/dev/null"
            " | grep -E "
            + shlex.quote(
                r"^(linux-(image|headers|modules|generic|aws|azure|gcp|kvm|raspi|oem)"
                r"|kernel(-core|-modules|-devel|-headers)?)"
            )
        )

    def install_version(self, package: str, version: str) -> str:
        return privileged(
            "/usr/bin/apt-get install -y "
            "-o Dpkg::Options::=--force-confdef "
            "-o Dpkg::Options::=--force-confold "
            "--allow-downgrades "
            f"{safe_pkg(package)}={safe_ver(version)}"
        )

    def list_installed_versions(self, packages: list[str]) -> str:
        quoted = " ".join(safe_pkg(p) for p in packages)
        return f"dpkg-query -W -f='${{Package}}=${{Version}}\\n' {quoted} 2>/dev/null"

    def clean_cache(self) -> str:
        return privileged("/usr/bin/apt-get clean")

    def autoremove(self) -> str:
        return privileged("/usr/bin/apt-get autoremove -y")

    def simulate_upgrade(self) -> str:
        """Return command to simulate an upgrade (dry-run)."""
        # Dry-run simulation does not modify state; runs unprivileged.
        return "apt-get --simulate upgrade"

    def detect_lock(self) -> str:
        # fuser prints the PID holding each lock file; /proc/<pid>/comm gives the name.
        # fuser may not be installed — || true keeps stdout empty rather than erroring.
        return (
            "for lock in"
            " /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/lib/dpkg/lock; do"
            ' pid=$(fuser "$lock" 2>/dev/null | tr -d \' \') || true;'
            ' if [ -n "$pid" ]; then'
            ' cmd=$(cat "/proc/$pid/comm" 2>/dev/null || echo unknown);'
            ' echo "pid=$pid cmd=$cmd";'
            " break;"
            " fi;"
            " done"
        )

    def cache_size(self) -> str:
        """Return command to check package cache size."""
        return "du -sh /var/cache/apt 2>/dev/null || echo '0\t/var/cache/apt'"


class DnfManager(PackageManager):
    """Package manager for RHEL systems (dnf)."""

    def refresh_package_lists(self) -> str:
        return privileged("/usr/bin/dnf makecache --quiet 2>/dev/null || true")

    def list_upgradable(self) -> str:
        return "dnf check-update --quiet 2>/dev/null || true"

    def upgrade_all(self, exclude_patterns: list[str] | None = None) -> str:
        # Finding #11: use dnf versionlock with exact kernel package names
        # queried from rpm and filtered in Python — not glob patterns.
        # rpm -qa is read-only — no sudo needed.
        kernel_query = (
            "rpm -qa --qf '%{NAME}\\n' 2>/dev/null"
            " | grep -E "
            + shlex.quote(
                r"^(kernel(-core|-modules|-devel|-headers)?)"
            )
        )
        lock_cmd = (
            f"KERNEL_PKGS=$({kernel_query}); "
            "[ -n \"$KERNEL_PKGS\" ] && echo \"$KERNEL_PKGS\" | "
            "xargs sudo -n /usr/bin/dnf versionlock add 2>/dev/null || true; "
            "sudo -n /usr/bin/dnf upgrade -y; "
            "DNF_RC=$?; "
            "[ -n \"$KERNEL_PKGS\" ] && echo \"$KERNEL_PKGS\" | "
            "xargs sudo -n /usr/bin/dnf versionlock delete 2>/dev/null || true; "
            "exit $DNF_RC"
        )
        return lock_cmd

    def query_kernel_packages(self) -> str:
        """Return command that prints exact installed kernel package names (one per line)."""
        return (
            "rpm -qa --qf '%{NAME}\\n' 2>/dev/null"
            " | grep -E "
            + shlex.quote(r"^(kernel(-core|-modules|-devel|-headers)?)")
        )

    def install_version(self, package: str, version: str) -> str:
        return privileged(f"/usr/bin/dnf downgrade -y {safe_pkg(package)}-{safe_ver(version)}")

    def list_installed_versions(self, packages: list[str]) -> str:
        quoted = " ".join(safe_pkg(p) for p in packages)
        return f"rpm -q --qf '%{{NAME}}=%{{VERSION}}-%{{RELEASE}}\\n' {quoted} 2>/dev/null"

    def clean_cache(self) -> str:
        return privileged("/usr/bin/dnf clean all")

    def autoremove(self) -> str:
        return privileged("/usr/bin/dnf autoremove -y")

    def simulate_upgrade(self) -> str:
        """Return command to simulate an upgrade (dry-run)."""
        return "dnf check-update"

    def detect_lock(self) -> str:
        # DNF/YUM write their PID to a file while running; kill -0 checks the process is alive.
        return (
            "for pidfile in /var/run/dnf.pid /var/run/yum.pid; do"
            ' [ -f "$pidfile" ] || continue;'
            ' pid=$(cat "$pidfile" 2>/dev/null) || continue;'
            ' kill -0 "$pid" 2>/dev/null || continue;'
            ' cmd=$(cat "/proc/$pid/comm" 2>/dev/null || echo unknown);'
            ' echo "pid=$pid cmd=$cmd";'
            " break;"
            " done"
        )

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
