"""Runtime OS detection and configuration verification.

Detects the OS family and version of a target VM via SSH commands
(parsing /etc/os-release). Verifies that the detected OS matches
the expected OS from inventory configuration.
"""

from __future__ import annotations

import logging
import re

from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.vm import OSFamily, VMInfo

logger = logging.getLogger(__name__)

# Mapping from /etc/os-release ID values to our OSFamily enum
_OS_ID_MAP: dict[str, OSFamily] = {
    "ubuntu": OSFamily.UBUNTU,
    "debian": OSFamily.DEBIAN,
    "rhel": OSFamily.RHEL,
    "centos": OSFamily.RHEL,  # CentOS maps to RHEL
    "rocky": OSFamily.RHEL,   # Rocky Linux maps to RHEL
    "almalinux": OSFamily.RHEL,  # AlmaLinux maps to RHEL
}


def parse_os_release(content: str) -> tuple[OSFamily, str]:
    """Parse /etc/os-release content to extract OS family and version.

    Args:
        content: Raw content of /etc/os-release.

    Returns:
        Tuple of (OSFamily, version_string).

    Raises:
        ValueError: If OS cannot be determined or is unsupported.
    """
    # Parse key=value pairs (values may be quoted)
    fields: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        # Strip quotes
        value = value.strip().strip('"').strip("'")
        fields[key.strip()] = value

    os_id = fields.get("ID", "").lower()
    version_id = fields.get("VERSION_ID", "unknown")
    pretty_name = fields.get("PRETTY_NAME", f"{os_id} {version_id}")

    os_family = _OS_ID_MAP.get(os_id)
    if os_family is None:
        msg = f"Unsupported OS: ID={os_id} ({pretty_name})"
        raise ValueError(msg)

    return os_family, pretty_name


def parse_disk_usage(df_output: str) -> dict[str, float]:
    """Parse `df -h` output into mount → usage percentage.

    Args:
        df_output: Raw output from `df -h`.

    Returns:
        Dict mapping mount point to usage percentage.
    """
    usage: dict[str, float] = {}
    for line in df_output.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 6:
            # Format: Filesystem Size Used Avail Use% Mounted
            pct_str = parts[4].rstrip("%")
            try:
                usage[parts[5]] = float(pct_str)
            except ValueError:
                continue
    return usage


def parse_uptime(uptime_output: str) -> float:
    """Parse /proc/uptime to get system uptime in seconds.

    Args:
        uptime_output: Content of /proc/uptime (first field is seconds).

    Returns:
        Uptime in seconds.
    """
    try:
        return float(uptime_output.strip().split()[0])
    except (ValueError, IndexError):
        return 0.0


async def detect_os(
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    ssh_manager: SSHConnectionManager,
) -> VMInfo:
    """Detect the OS family and version of a remote host.

    Runs detection commands via SSH and parses the output to determine
    OS family, version, disk usage, Docker availability, etc.

    Args:
        vm_id: VM identifier.
        hostname: Target host.
        username: SSH username.
        key_path: Path to private key file.
        ssh_manager: SSH connection manager for command execution.

    Returns:
        VMInfo with detected system information.

    Raises:
        ValueError: If OS detection fails or OS is unsupported.
        ConnectionError: If SSH commands fail.
    """
    # Read /etc/os-release
    os_release_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, "cat /etc/os-release",
    )
    if not os_release_result.success:
        msg = f"Failed to read /etc/os-release on {vm_id}: {os_release_result.stderr}"
        raise ValueError(msg)

    os_family, os_version = parse_os_release(os_release_result.stdout)

    # Get disk usage
    df_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, "df -h",
    )
    disk_usage = parse_disk_usage(df_result.stdout) if df_result.success else {}

    # Check Docker
    docker_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, "docker info >/dev/null 2>&1 && echo yes || echo no",
    )
    docker_available = docker_result.success and "yes" in docker_result.stdout

    # Get pending updates count (best-effort)
    if os_family in (OSFamily.UBUNTU, OSFamily.DEBIAN):
        pkg_cmd = "apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0"
    else:
        pkg_cmd = "dnf check-update --quiet 2>/dev/null | wc -l || echo 0"

    pkg_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, pkg_cmd,
    )
    try:
        pending_packages = int(pkg_result.stdout.strip())
    except ValueError:
        pending_packages = 0

    # Get uptime
    uptime_result = await ssh_manager.execute(
        vm_id, hostname, username, key_path, "cat /proc/uptime",
    )
    uptime_seconds = parse_uptime(uptime_result.stdout) if uptime_result.success else 0.0

    return VMInfo(
        os_family=os_family,
        os_version=os_version,
        disk_usage=disk_usage,
        docker_available=docker_available,
        pending_packages=pending_packages,
        uptime_seconds=uptime_seconds,
    )


def verify_os_match(expected: OSFamily, detected: OSFamily) -> bool:
    """Verify that detected OS matches inventory expectation.

    Args:
        expected: OS family from inventory config.
        detected: OS family detected at runtime.

    Returns:
        True if they match.
    """
    return expected == detected
