"""VM and target host models.

Defines VMTarget (inventory entry with connection details and maintenance policy)
and VMInfo (runtime-discovered system information like OS, disk, packages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OSFamily(StrEnum):
    """Supported Linux OS families."""

    UBUNTU = "ubuntu"
    DEBIAN = "debian"
    RHEL = "rhel"


@dataclass(frozen=True)
class VMTarget:
    """A target VM from inventory configuration.

    Attributes:
        vm_id: Unique identifier for this VM.
        hostname: DNS name or IP address.
        ssh_user: Username for SSH connections.
        ssh_key_path: Path to SSH private key file.
        os_family: Expected OS family (verified at runtime).
        policy: Named maintenance policy (relaxed/moderate/strict).
        tags: Arbitrary tags for grouping/filtering.
    """

    vm_id: str
    hostname: str
    ssh_user: str
    ssh_key_path: str
    os_family: OSFamily
    policy: str = "moderate"
    tags: dict[str, str] = field(default_factory=dict)
    # Services monitored pre/post maintenance for health regressions (Phase 1.3).
    # tuple because VMTarget is frozen=True.
    critical_services: tuple[str, ...] = ()
    # Whether Errander manages Node Exporter on this VM.
    # true  → scrape :9100 for metrics (installed by configure.sh or pre-existing).
    # false → SSH probe fallback (vmstat + /proc/meminfo + df).
    node_exporter: bool = False


@dataclass
class VMInfo:
    """Runtime-discovered system information for a VM.

    Populated during the discovery phase via SSH commands.

    Attributes:
        os_family: Detected OS family.
        os_version: Full OS version string.
        disk_usage: Disk usage per mount point (mount -> usage percentage).
        docker_available: Whether Docker is installed and running.
        pending_packages: Number of packages with available updates.
        uptime_seconds: System uptime in seconds.
    """

    os_family: OSFamily
    os_version: str
    disk_usage: dict[str, float]
    docker_available: bool
    pending_packages: int
    uptime_seconds: float
