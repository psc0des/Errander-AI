"""Rollback capabilities per action type.

Each action type has a defined rollback strategy (see CLAUDE.md Rollback Tiers):
- Full rollback: patching (reinstall previous package versions via apt-get --allow-downgrades)
- Re-pull: Docker prune (re-pull images if needed)
- No rollback needed: log rotation, disk cleanup
- Never touch: kernel, active data dirs

Rollback is triggered from the action subgraph on failure. SSH credentials and
executor are passed in so rollback can run real commands on the target VM.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.execution.command_builder import CommandBuildError, pkg_version_spec

from errander.models.actions import ActionType

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


async def rollback_action(
    action_type: ActionType,
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None" = None,
    hostname: str = "",
    username: str = "",
    key_path: str = "",
) -> tuple[bool, str]:
    """Attempt to rollback a failed action.

    Uses a strategy-per-action-type dispatch. Not all action types
    require or support rollback.

    Args:
        action_type: The type of action to rollback.
        vm_id: Target VM identifier.
        pre_snapshot: State snapshot taken before execution.
            For patching: maps package name → version string.
        executor: SandboxExecutor for SSH execution. Required for patching rollback.
        hostname: SSH hostname. Required for patching rollback.
        username: SSH username. Required for patching rollback.
        key_path: Path to SSH private key. Required for patching rollback.

    Returns:
        Tuple of (success, detail). If failed, detail explains what went wrong.
    """
    strategy = _ROLLBACK_STRATEGIES.get(action_type)
    if strategy is None:
        detail = f"Unknown action type for rollback: {action_type.value}"
        logger.error("Rollback failed for %s on %s: %s", action_type.value, vm_id, detail)
        return False, detail

    return await strategy(vm_id, pre_snapshot, executor, hostname, username, key_path)


async def _rollback_patching(
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None",
    hostname: str,
    username: str,
    key_path: str,
) -> tuple[bool, str]:
    """Rollback patching by reinstalling previous package versions.

    Strategy (Option A — finding #5):
    1. Parse pre_snapshot (package → version mapping from snapshot_node).
    2. Build apt-get install --allow-downgrades with exact pkg=version specs.
    3. Execute via SSH.
    4. Verify post-rollback versions match snapshot; log CRITICAL on mismatch.
    """
    if executor is None:
        return False, "Rollback requires SSH executor — not available"

    if not pre_snapshot:
        return False, "Pre-snapshot is empty — cannot determine versions to restore"

    # Build pkg=version install specs via validated helpers (finding #10)
    install_specs: list[str] = []
    for pkg, version in pre_snapshot.items():
        pkg_str = str(pkg).strip()
        ver_str = str(version).strip()
        if not pkg_str or not ver_str:
            continue
        try:
            install_specs.append(pkg_version_spec(pkg_str, ver_str))
        except CommandBuildError as exc:
            logger.error("Skipping unsafe pkg=ver in rollback snapshot: %s", exc)
            continue

    if not install_specs:
        return False, "No versioned packages in snapshot — cannot rollback"

    rollback_cmd = (
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades "
        + " ".join(install_specs)
    )

    logger.info(
        "Rolling back %d packages on %s via apt-get --allow-downgrades",
        len(install_specs), vm_id,
    )
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=rollback_cmd,
        timeout=300,
        dry_run=False,  # rollback always runs live — we're fixing a live failure
    )

    if not result.success:
        return (
            False,
            f"apt-get rollback failed on {vm_id}: {result.stderr[:500]}",
        )

    # Verification: re-query installed versions and compare to snapshot
    from errander.execution.command_builder import safe_pkg as _safe_pkg
    pkg_names = [str(p) for p in pre_snapshot]
    safe_names = [_safe_pkg(p) for p in pkg_names if p.strip()]
    verify_cmd = (
        "dpkg-query -W -f='${Package}=${Version}\\n' "
        + " ".join(safe_names)
    )
    verify_result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=verify_cmd,
        dry_run=False,
    )

    if not verify_result.success:
        logger.warning(
            "Rollback executed on %s but post-rollback verification SSH failed — "
            "manual inspection required",
            vm_id,
        )
        return (
            True,
            f"Rollback apt-get succeeded on {vm_id} "
            f"({len(install_specs)} packages) but version verification SSH failed",
        )

    # Parse post-rollback versions
    post_versions: dict[str, str] = {}
    for line in verify_result.stdout.strip().splitlines():
        if "=" in line:
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                post_versions[parts[0]] = parts[1]

    mismatches = [
        f"{pkg} expected={ver} got={post_versions.get(str(pkg), 'missing')}"
        for pkg, ver in pre_snapshot.items()
        if post_versions.get(str(pkg)) != str(ver)
    ]

    if mismatches:
        logger.error(
            "CRITICAL: Rollback verification failed on %s — %d packages do not match snapshot: %s",
            vm_id, len(mismatches), mismatches[:5],
        )
        return (
            False,
            f"Rollback verification mismatch on {vm_id}: "
            f"{len(mismatches)} packages wrong after rollback: {mismatches[:3]}",
        )

    logger.info(
        "Rollback verified: %d packages restored to pre-upgrade versions on %s",
        len(install_specs), vm_id,
    )
    return True, f"Rolled back {len(install_specs)} packages on {vm_id} — versions verified"


async def _rollback_docker_prune(
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None",
    hostname: str,
    username: str,
    key_path: str,
) -> tuple[bool, str]:
    """Docker prune has no true rollback — pruned resources are gone."""
    return True, "Docker prune is low-risk — re-pull images from registry if needed"


async def _rollback_disk_cleanup(
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None",
    hostname: str,
    username: str,
    key_path: str,
) -> tuple[bool, str]:
    """Disk cleanup targets only safe paths — no rollback needed."""
    return True, "No rollback needed for disk cleanup — only targets whitelisted paths"


async def _rollback_log_rotation(
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None",
    hostname: str,
    username: str,
    key_path: str,
) -> tuple[bool, str]:
    """Log rotation compresses data — original data still exists."""
    return True, "No rollback needed for log rotation — data is compressed, not deleted"


async def _rollback_backup_verify(
    vm_id: str,
    pre_snapshot: dict[str, object],
    executor: "SandboxExecutor | None",
    hostname: str,
    username: str,
    key_path: str,
) -> tuple[bool, str]:
    """Backup verification is read-only — nothing to rollback."""
    return True, "Backup verify is read-only — no state changes to rollback"


_ROLLBACK_STRATEGIES = {
    ActionType.PATCHING: _rollback_patching,
    ActionType.DOCKER_PRUNE: _rollback_docker_prune,
    ActionType.DISK_CLEANUP: _rollback_disk_cleanup,
    ActionType.LOG_ROTATION: _rollback_log_rotation,
    ActionType.BACKUP_VERIFY: _rollback_backup_verify,
}
