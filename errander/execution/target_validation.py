"""Per-VM readiness validation for --check-targets CLI.

Runs SSH probes to confirm each target VM has the binaries Errander needs and
that sudo -n is configured for them. In wrapper mode, also probes the docker
wrapper scripts via their --check flag.

Read-only: no mutation of any kind.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from errander.execution.ssh import SSHConnectionManager

logger = logging.getLogger(__name__)

Verdict = Literal["ready", "warnings", "blocked"]


@dataclass
class TargetReadiness:
    vm_id: str
    hostname: str
    binaries_present: dict[str, bool] = field(default_factory=dict)
    sudo_ok: dict[str, bool] = field(default_factory=dict)
    wrappers_ok: dict[str, bool] = field(default_factory=dict)
    verdict: Verdict = "ready"
    issues: list[str] = field(default_factory=list)


# Binaries required per action. Docker and service_restart use separate checks.
_ACTION_BINARIES: dict[str, list[str]] = {
    "log_rotation": ["/usr/sbin/logrotate", "/usr/bin/gzip"],
    "disk_cleanup": ["/usr/bin/truncate", "/usr/bin/cp"],
    "backup_verify": ["/usr/bin/cp"],
}

# Package-manager binaries are OS-specific and only needed when patching is enabled.
_PATCHING_BINARIES: dict[str, list[str]] = {
    "ubuntu": ["/usr/bin/apt-get", "/usr/bin/apt-mark"],
    "debian": ["/usr/bin/apt-get", "/usr/bin/apt-mark"],
    "rhel": ["/usr/bin/dnf"],
    "rocky": ["/usr/bin/dnf"],
    "alma": ["/usr/bin/dnf"],
    "centos": ["/usr/bin/dnf"],
}


def _binaries_for_os(os_family: str) -> list[str]:
    """Return the full per-OS binary list (all actions — backward-compat path)."""
    base = [
        "/usr/bin/journalctl",
        "/usr/sbin/logrotate",
        "/usr/bin/gzip",
        "/usr/bin/truncate",
        "/usr/bin/cp",
    ]
    if os_family in ("ubuntu", "debian"):
        base.extend(["/usr/bin/apt-get", "/usr/bin/apt-mark"])
    elif os_family in ("rhel", "rocky", "alma", "centos"):
        base.extend(["/usr/bin/dnf"])
    return base


def _binaries_for_enabled_actions(os_family: str, enabled_actions: list[str]) -> list[str]:
    """Return only the binaries required by the given enabled actions.

    journalctl is always included — used by the SRE probe regardless of action config.
    """
    enabled_set = set(enabled_actions)
    seen: set[str] = set()
    result: list[str] = []

    def _add(b: str) -> None:
        if b not in seen:
            seen.add(b)
            result.append(b)

    _add("/usr/bin/journalctl")
    for action, bins in _ACTION_BINARIES.items():
        if action in enabled_set:
            for b in bins:
                _add(b)
    if "patching" in enabled_set:
        for b in _PATCHING_BINARIES.get(os_family, []):
            _add(b)
    return result


async def check_target(
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    os_family: str,
    docker_command_mode: str,
    ssh_manager: SSHConnectionManager,
    *,
    enabled_actions: list[str] | None = None,
) -> TargetReadiness:
    """Run all readiness checks against a single target VM. Read-only.

    When ``enabled_actions`` is provided, only binaries required by those actions
    are checked. When omitted, all binaries are checked (backward-compat path).
    """
    readiness = TargetReadiness(vm_id=vm_id, hostname=hostname)
    binaries = (
        _binaries_for_enabled_actions(os_family, enabled_actions)
        if enabled_actions is not None
        else _binaries_for_os(os_family)
    )

    # 1. Binary presence via `command -v`
    for binary in binaries:
        cmd = f"command -v {binary} >/dev/null 2>&1 && echo present || echo missing"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        present = result.success and "present" in result.stdout
        readiness.binaries_present[binary] = present
        if not present:
            readiness.issues.append(f"missing binary: {binary}")

    # 2. Sudo -n capability per binary
    for binary in binaries:
        if not readiness.binaries_present.get(binary):
            readiness.sudo_ok[binary] = False
            continue
        cmd = f"sudo -n {binary} --version >/dev/null 2>&1 && echo ok || echo fail"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        ok = result.success and "ok" in result.stdout
        readiness.sudo_ok[binary] = ok
        if not ok:
            readiness.issues.append(f"sudo -n denied for: {binary}")

    # 3. Docker wrapper probes — skip entirely when docker_prune is not enabled
    _docker_enabled = enabled_actions is None or "docker_prune" in enabled_actions
    if _docker_enabled and docker_command_mode == "wrapper":
        from errander.agent.subgraphs import BUILTIN_ACTIONS
        docker_manifest = BUILTIN_ACTIONS.get("docker_prune")
        wrapper_paths = list(docker_manifest.required_wrappers) if docker_manifest else []
        for wrapper in wrapper_paths:
            cmd = f"sudo -n {wrapper} --check 2>/dev/null"
            result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
            ok = result.success and "ok" in result.stdout.strip()
            readiness.wrappers_ok[wrapper] = ok
            if not ok:
                readiness.issues.append(f"wrapper script not ready: {wrapper}")
    elif _docker_enabled and docker_command_mode == "direct_sudo":
        cmd = "sudo -n /usr/bin/docker version >/dev/null 2>&1 && echo ok || echo fail"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        ok = result.success and "ok" in result.stdout
        readiness.wrappers_ok["/usr/bin/docker"] = ok
        if not ok:
            readiness.issues.append("sudo -n denied for: /usr/bin/docker")
    # disabled mode: no docker check needed

    readiness.verdict = "blocked" if readiness.issues else "ready"
    return readiness


def render_readiness_report(results: list[TargetReadiness]) -> str:
    """Render a per-VM readiness table for terminal output."""
    lines = []
    lines.append(f"{'VM':<30} {'Host':<20} {'Verdict':<10} {'Issues':<60}")
    lines.append("-" * 120)
    for r in results:
        issues_str = "; ".join(r.issues) if r.issues else "—"
        if len(issues_str) > 58:
            issues_str = issues_str[:55] + "..."
        lines.append(f"{r.vm_id:<30} {r.hostname:<20} {r.verdict:<10} {issues_str:<60}")
    lines.append("")
    blocked = sum(1 for r in results if r.verdict == "blocked")
    lines.append(f"Summary: {len(results) - blocked} ready, {blocked} blocked")
    return "\n".join(lines)
