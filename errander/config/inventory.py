"""VM inventory loader and validator.

Loads target VM definitions from YAML configuration. Validates that
each target has required fields and that SSH key files exist on disk.
Resolves environment→host inheritance per the spec:
  Global defaults → Environment settings → Host-specific overrides
"""

from __future__ import annotations

from pathlib import Path

from errander.config.policies import BUILTIN_POLICIES
from errander.config.schema import (
    EnvironmentSchema,
    InventoryConfig,
    TargetSchema,
    validate_inventory,
)
from errander.models.vm import OSFamily, VMTarget


def load_inventory(config_path: Path) -> list[VMTarget]:
    """Load and validate VM inventory from YAML file.

    Resolves environment-level settings inherited by each target,
    with host-level overrides taking precedence.

    Args:
        config_path: Path to inventory YAML file.

    Returns:
        List of validated VMTarget objects.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config is invalid.
        pydantic.ValidationError: If schema validation fails.
    """
    config = validate_inventory(config_path)
    return _resolve_targets(config)


def _resolve_targets(config: InventoryConfig) -> list[VMTarget]:
    """Resolve environment inheritance and build VMTarget list."""
    targets: list[VMTarget] = []

    for env_name, env in config.environments.items():
        for target in env.targets:
            vm_target = _resolve_single_target(env_name, env, target)
            errors = validate_target(vm_target)
            if errors:
                msg = (
                    f"Invalid target '{target.name}' in environment "
                    f"'{env_name}': {'; '.join(errors)}"
                )
                raise ValueError(msg)
            targets.append(vm_target)

    return targets


def _resolve_single_target(
    env_name: str,
    env: EnvironmentSchema,
    target: TargetSchema,
) -> VMTarget:
    """Resolve a single target with environment inheritance.

    Host-level fields override environment-level fields.
    """
    # Host overrides environment
    ssh_user = target.ssh_user if target.ssh_user is not None else env.ssh_user
    ssh_key_path = target.ssh_key_path if target.ssh_key_path is not None else env.ssh_key_path
    policy = target.policy if target.policy is not None else env.approval_policy
    # Host-level critical_services overrides env-level when non-empty
    critical_services = tuple(
        target.critical_services if target.critical_services else env.critical_services
    )

    return VMTarget(
        vm_id=f"{env_name}/{target.name}",
        hostname=target.host,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        os_family=OSFamily(target.os_family),
        policy=policy,
        tags={"env": env_name, **{tag: "" for tag in target.tags}},
        critical_services=critical_services,
    )


def validate_target(target: VMTarget) -> list[str]:
    """Validate a single VMTarget configuration.

    Checks:
    - hostname is non-empty
    - ssh_user is non-empty
    - ssh_key_path is non-empty
    - OS family is supported (enforced by OSFamily enum)
    - Policy name is valid

    Args:
        target: VMTarget to validate.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if not target.hostname.strip():
        errors.append("hostname must be non-empty")

    if not target.ssh_user.strip():
        errors.append("ssh_user must be non-empty")

    if not target.ssh_key_path.strip():
        errors.append("ssh_key_path must be non-empty")

    if target.policy not in BUILTIN_POLICIES:
        errors.append(
            f"unknown policy '{target.policy}', "
            f"available: {list(BUILTIN_POLICIES)}"
        )

    return errors


def validate_ssh_keys(targets: list[VMTarget]) -> list[str]:
    """Validate that SSH key files exist for all targets.

    Called at startup to catch config errors early.

    Args:
        targets: List of VMTarget objects.

    Returns:
        List of error messages for missing SSH keys.
    """
    errors: list[str] = []
    checked: set[str] = set()

    for target in targets:
        key_path = str(Path(target.ssh_key_path).expanduser())
        if key_path in checked:
            continue
        checked.add(key_path)

        if not Path(key_path).exists():
            errors.append(
                f"SSH key not found: {target.ssh_key_path} "
                f"(used by {target.vm_id})"
            )

    return errors
