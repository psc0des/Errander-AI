"""Privilege escalation helper — sudo -n prefix for privileged commands.

All commands that require root on target VMs must use privileged() rather
than bare command names or plain sudo.

Why sudo -n:
- Fails immediately (exit 1) if passwordless sudo is not configured —
  no hanging waiting for a password prompt.
- Absolute binary paths match sudoers entries predictably across distros.
- Produces clean audit trails: command, user, host, timestamp in
  /var/log/auth.log on the target VM.

Read-only commands that do NOT need privilege escalation:
  df, du, dpkg-query, rpm -q, apt list, dnf check-update, stat,
  systemctl is-active, find (listing only), journalctl --disk-usage
"""

from __future__ import annotations

# Canonical absolute paths for privileged binaries.
# On modern Linux (Ubuntu 20.04+, RHEL 8+) /bin is a symlink to /usr/bin —
# /usr/bin paths work everywhere and match the sudoers entries in SETUP.md.
PRIVILEGED_PATHS: dict[str, str] = {
    "apt-get": "/usr/bin/apt-get",
    "apt-mark": "/usr/bin/apt-mark",
    "dnf": "/usr/bin/dnf",
    "yum": "/usr/bin/yum",
    "journalctl": "/usr/bin/journalctl",
    "logrotate": "/usr/sbin/logrotate",
    "gzip": "/usr/bin/gzip",
    "truncate": "/usr/bin/truncate",
    "cp": "/usr/bin/cp",
    "docker": "/usr/bin/docker",
    "needs-restarting": "/usr/bin/needs-restarting",
}

# Binaries required per action type — used by sudo_capability_check.
REQUIRED_BINARIES_BY_ACTION: dict[str, list[str]] = {
    "patching_apt": ["/usr/bin/apt-get", "/usr/bin/apt-mark"],
    "patching_dnf": ["/usr/bin/dnf"],
    "disk_cleanup": ["/usr/bin/journalctl"],
    "log_rotation": ["/usr/sbin/logrotate", "/usr/bin/gzip", "/usr/bin/truncate", "/usr/bin/cp"],
    "docker_prune": ["/usr/bin/docker"],
}


def privileged(cmd: str) -> str:
    """Prefix a command with 'sudo -n' for privilege escalation.

    Args:
        cmd: Shell command string to elevate. The binary should be an
            absolute path matching a sudoers entry (see PRIVILEGED_PATHS).

    Returns:
        Command prefixed with "sudo -n ".
    """
    return f"sudo -n {cmd}"


def sudo_capability_check(binaries: list[str]) -> str:
    """Return a shell command that checks sudo -n access for each binary.

    Each binary is checked with a harmless flag (--version or version).
    Output per binary: "SUDO_OK /path/to/bin" or "SUDO_FAIL /path/to/bin".
    The overall command always exits 0 so callers can parse the output.

    Args:
        binaries: Absolute paths to check (e.g. ["/usr/bin/apt-get"]).

    Returns:
        Shell command string.
    """
    checks: list[str] = []
    for binary in binaries:
        flag = "version" if "docker" in binary else "--version"
        probe = f"sudo -n {binary} {flag} >/dev/null 2>&1"
        checks.append(
            f'if {probe}; then echo "SUDO_OK {binary}"; '
            f'else echo "SUDO_FAIL {binary}"; fi'
        )
    return "; ".join(checks) if checks else "true"


def parse_capability_check(output: str) -> tuple[list[str], list[str]]:
    """Parse output of sudo_capability_check() into ok/fail lists.

    Args:
        output: stdout from sudo_capability_check() command.

    Returns:
        Tuple of (ok_binaries, failed_binaries).
    """
    ok: list[str] = []
    failed: list[str] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if line.startswith("SUDO_OK "):
            ok.append(line[8:])
        elif line.startswith("SUDO_FAIL "):
            failed.append(line[10:])
    return ok, failed
