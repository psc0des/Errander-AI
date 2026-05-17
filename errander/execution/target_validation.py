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


# Package-manager binaries in patching manifest are listed for all OS families.
# Only the subset applicable to this OS should be checked.
_OS_PKG_MANAGERS: dict[str, frozenset[str]] = {
    "ubuntu": frozenset({"/usr/bin/apt-get", "/usr/bin/apt-mark"}),
    "debian": frozenset({"/usr/bin/apt-get", "/usr/bin/apt-mark"}),
    "rhel":   frozenset({"/usr/bin/dnf"}),
    "rocky":  frozenset({"/usr/bin/dnf"}),
    "alma":   frozenset({"/usr/bin/dnf"}),
    "centos": frozenset({"/usr/bin/dnf"}),
}
# All package-manager binaries across all OS families — used to detect and filter them.
_ALL_PKG_MANAGERS: frozenset[str] = frozenset(
    b for s in _OS_PKG_MANAGERS.values() for b in s
) | frozenset({"/usr/bin/yum"})  # yum alias in some patching manifests

# Docker binary is checked separately via docker_command_mode — skip it here.
_DOCKER_BINARIES: frozenset[str] = frozenset({"/usr/bin/docker"})


def _binaries_for_os(os_family: str) -> list[str]:
    """Return the full per-OS binary list (all actions — backward-compat path).

    Derived from BUILTIN_ACTIONS manifests with OS-appropriate package managers.
    """
    from errander.agent.subgraphs import BUILTIN_ACTIONS
    return _binaries_for_enabled_actions(os_family, list(BUILTIN_ACTIONS.keys()))


def _binaries_for_enabled_actions(os_family: str, enabled_actions: list[str]) -> list[str]:
    """Return binaries required by ``enabled_actions``, derived from action manifests.

    The BUILTIN_ACTIONS registry is the single source of truth for binary requirements.
    Package-manager binaries are filtered to only the OS-appropriate subset.
    Docker binaries are excluded — handled separately by docker_command_mode.
    """
    from errander.agent.subgraphs import BUILTIN_ACTIONS

    enabled_set = set(enabled_actions)
    os_pkg = _OS_PKG_MANAGERS.get(os_family, frozenset())
    seen: set[str] = set()
    result: list[str] = []

    def _add(b: str) -> None:
        if b not in seen:
            seen.add(b)
            result.append(b)

    for action_name, manifest in BUILTIN_ACTIONS.items():
        if action_name not in enabled_set:
            continue
        for binary in manifest.required_binaries:
            if binary in _DOCKER_BINARIES:
                continue  # handled by docker_command_mode wrapper path
            if binary in _ALL_PKG_MANAGERS:
                if binary in os_pkg:
                    _add(binary)
                # else: not the right package manager for this OS
            else:
                _add(binary)
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

    # 3. Non-docker wrapper probes — manifest-driven for all other enabled actions
    from errander.agent.subgraphs import BUILTIN_ACTIONS
    for _action_name, _manifest in BUILTIN_ACTIONS.items():
        if _action_name == "docker_prune":
            continue  # docker_prune handled below (command_mode concept)
        if enabled_actions is not None and _action_name not in enabled_actions:
            continue
        for wrapper in _manifest.required_wrappers:
            cmd = f"sudo -n {wrapper} --check 2>/dev/null"
            result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
            ok = result.success and "ok" in result.stdout.strip()
            readiness.wrappers_ok[wrapper] = ok
            if not ok:
                readiness.issues.append(f"wrapper script not ready: {wrapper}")

    # 4. Docker wrapper probes — command_mode drives which probe runs
    _docker_enabled = enabled_actions is None or "docker_prune" in enabled_actions
    if _docker_enabled and docker_command_mode == "wrapper":
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
