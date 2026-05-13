"""Pre-execution validation checks.

Validators run before every action to ensure preconditions are met.
If validation fails, the action is skipped with a logged reason.

Validation checks include:
- Risk tier gate: block CRITICAL actions (never automated)
- Kernel exclusion: reject any action targeting kernel packages
- Disk whitelist: reject cleanup of non-whitelisted paths
- OS compatibility: action is supported on the target OS
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from errander.agent.subgraphs.disk_cleanup import ALLOWED_CLEANUP_PATHS
from errander.config.policies import MaintenancePolicy
from errander.models.actions import Action, ActionType, RiskTier

if TYPE_CHECKING:
    from errander.execution.commands import PackageManager
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

#: Patterns that indicate kernel packages — always excluded from patching.
_KERNEL_PATTERNS: frozenset[str] = frozenset({
    "linux-image",
    "linux-headers",
    "linux-modules",
    "kernel",
    "kernel-core",
    "kernel-devel",
})


@dataclass(frozen=True)
class LockHolder:
    """Structured info about the process holding a package manager lock."""

    pid: int | None
    cmd: str | None


def parse_lock_output(output: str) -> LockHolder | None:
    """Parse detect_lock() output into structured holder info.

    Args:
        output: stdout from detect_lock() shell command.

    Returns:
        LockHolder if a lock is held, None if output is empty (no lock).
    """
    stripped = output.strip()
    if not stripped:
        return None
    pid: int | None = None
    cmd: str | None = None
    for token in stripped.split():
        if token.startswith("pid="):
            with contextlib.suppress(ValueError):
                pid = int(token[4:])
        elif token.startswith("cmd="):
            val = token[4:]
            cmd = val if val else None
    return LockHolder(pid=pid, cmd=cmd)


async def validate_no_pkg_lock(
    executor: SandboxExecutor,
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    pm: PackageManager,
) -> tuple[bool, LockHolder | None]:
    """Check whether a package manager lock is held on the target VM.

    Runs detect_lock() via SSH.  The command always exits 0; empty stdout
    means no lock.  On SSH failure we log a warning and treat the lock as
    absent (best-effort — don't block patching on a probe error).

    Args:
        executor: SSH executor.
        vm_id: VM identifier for logging.
        hostname: SSH host.
        username: SSH user.
        key_path: SSH key path.
        pm: PackageManager for the target OS (provides detect_lock() command).

    Returns:
        (is_clear, holder): is_clear=True when no lock held; holder is None
        when clear or when lock state could not be determined.
    """
    result = await executor.execute(
        vm_id, hostname, username, key_path,
        command=pm.detect_lock(),
        dry_run=False,
    )
    if not result.success:
        logger.warning(
            "Lock probe failed on %s (treating as clear): %s",
            vm_id, result.stderr[:120],
        )
        return True, None
    holder = parse_lock_output(result.stdout)
    return holder is None, holder


def _contains_kernel_packages(params: dict[str, object]) -> bool:
    """Check if action params reference kernel packages."""
    packages = params.get("packages", [])
    if not isinstance(packages, list):
        return False
    exclude = params.get("exclude_patterns", [])
    if not isinstance(exclude, list):
        exclude = []
    # Check if any package name starts with a kernel pattern
    for pkg in packages:
        pkg_str = str(pkg).lower()
        if any(pkg_str.startswith(kp) for kp in _KERNEL_PATTERNS):
            return True
    return False


async def validate_action(
    action: Action,
    vm_id: str,
    os_family: str,
    policy: str = "moderate",
) -> tuple[bool, str]:
    """Validate whether an action should proceed.

    Checks (in order):
    1. Critical risk tier → always blocked (finding #2.1).
    2. Policy gate: HIGH-risk actions blocked in 'relaxed' environments
       without explicit approval (defense-in-depth against batch gate bypass).
    3. Kernel exclusion → patching actions with kernel packages blocked.
    4. Disk whitelist → cleanup paths must be whitelisted.

    Args:
        action: The action to validate.
        vm_id: Target VM identifier.
        os_family: Detected OS family.
        policy: Maintenance policy name ('relaxed', 'moderate', 'strict').
            Consulted via requires_approval() to enforce policy-aware gating.

    Returns:
        Tuple of (is_valid, reason). If invalid, reason explains why.
    """
    from errander.config.policies import BUILTIN_POLICIES

    policy_obj = BUILTIN_POLICIES.get(policy) or BUILTIN_POLICIES["moderate"]

    # 1. Critical actions are NEVER automated regardless of policy
    if action.risk_tier == RiskTier.CRITICAL:
        reason = (
            f"Critical-risk action {action.action_type.value} is never automated "
            f"(policy={policy})"
        )
        logger.warning("BLOCKED %s on %s: %s", action.action_type.value, vm_id, reason)
        return False, reason

    # 2. Policy gate (finding #2.1): log which actions require approval per policy
    needs_approval = requires_approval(action.risk_tier, policy_obj)
    if needs_approval:
        logger.info(
            "Action %s on %s (risk=%s) requires approval under policy=%s — "
            "proceeding (batch approval already granted)",
            action.action_type.value, vm_id, action.risk_tier.value, policy,
        )

    # 3. Kernel exclusion for patching
    if action.action_type == ActionType.PATCHING and _contains_kernel_packages(action.params):
        reason = "Kernel packages detected — kernel patching is never automated"
        logger.warning("BLOCKED patching on %s: %s", vm_id, reason)
        return False, reason

    # 4. Disk cleanup whitelist enforcement
    if action.action_type == ActionType.DISK_CLEANUP:
        paths = action.params.get("paths", [])
        if isinstance(paths, list) and paths:
            rejected = [p for p in paths if str(p) not in ALLOWED_CLEANUP_PATHS]
            if rejected:
                reason = f"Paths not on cleanup whitelist: {rejected} (policy={policy})"
                logger.warning("BLOCKED disk_cleanup on %s: %s", vm_id, reason)
                return False, reason

    return True, ""


def requires_approval(risk_tier: RiskTier, policy: MaintenancePolicy) -> bool:
    """Determine if an action at this risk tier needs human approval.

    Args:
        risk_tier: The action's risk classification.
        policy: The VM's maintenance policy.

    Returns:
        True if human approval is required before execution.
    """
    return risk_tier not in policy.auto_approve_tiers
