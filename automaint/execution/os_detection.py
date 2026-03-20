"""Runtime OS detection and configuration verification.

Detects the OS family and version of a target VM via SSH commands
(e.g., parsing /etc/os-release). Verifies that the detected OS matches
the expected OS from inventory configuration.
"""

from __future__ import annotations

from automaint.models.vm import OSFamily, VMInfo


async def detect_os(hostname: str, username: str, key_path: str) -> VMInfo:
    """Detect the OS family and version of a remote host.

    Runs detection commands via SSH and parses the output to determine
    OS family, version, disk usage, Docker availability, etc.

    Args:
        hostname: Target host.
        username: SSH username.
        key_path: Path to private key file.

    Returns:
        VMInfo with detected system information.
    """
    raise NotImplementedError("OS detection not yet implemented")


def verify_os_match(expected: OSFamily, detected: OSFamily) -> bool:
    """Verify that detected OS matches inventory expectation.

    Args:
        expected: OS family from inventory config.
        detected: OS family detected at runtime.

    Returns:
        True if they match.
    """
    return expected == detected
