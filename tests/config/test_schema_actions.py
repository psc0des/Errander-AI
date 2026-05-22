"""Tests for per-action opt-in schema (actions: block in inventory.yaml)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
import yaml
from pydantic import ValidationError

from errander.agent.subgraphs import BUILTIN_ACTIONS
from errander.config.schema import (
    ActionConfig,
    ConfigError,
    EnvironmentSchema,
    TargetSchema,
    validate_inventory,
)

_TARGET = {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"}


def _env_with_actions(actions: dict | None = None) -> dict:  # type: ignore[type-arg]
    env: dict[str, object] = {"targets": [_TARGET]}
    if actions is not None:
        env["actions"] = actions
    return env


def _inventory_yaml(envs: dict) -> str:  # type: ignore[type-arg]
    return yaml.dump({"environments": envs})


class TestNewNestedSchemaAccepted:
    def test_full_actions_block_accepted(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={
                "patching": ActionConfig(enabled=True),
                "disk_cleanup": ActionConfig(enabled=True),
                "log_rotation": ActionConfig(enabled=True),
                "docker_hygiene": ActionConfig(enabled=False, command_mode="disabled"),
                "backup_verify": ActionConfig(enabled=False),
            },
        )
        assert env.actions["patching"].enabled is True
        assert env.actions["docker_hygiene"].command_mode == "disabled"

    def test_partial_actions_block_accepted(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={"docker_hygiene": ActionConfig(enabled=True, command_mode="wrapper")},
        )
        assert env.actions["docker_hygiene"].enabled is True

    def test_no_actions_block_accepted(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        assert isinstance(env.actions, dict)


class TestLegacyFieldRejected:
    def test_legacy_docker_command_mode_raises_config_error(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(
            yaml.dump({
                "environments": {
                    "prod": {
                        "docker_command_mode": "wrapper",
                        "targets": [_TARGET],
                    },
                },
            }),
        )
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            validate_inventory(config_file)
        assert "docker_command_mode" in str(exc_info.value)
        assert "--migrate-inventory" in str(exc_info.value)

    def test_legacy_field_error_mentions_migration_command(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(
            yaml.dump({
                "environments": {
                    "dev": {
                        "docker_command_mode": "disabled",
                        "targets": [_TARGET],
                    },
                },
            }),
        )
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            validate_inventory(config_file)
        msg = str(exc_info.value)
        assert "uv run python -m errander --migrate-inventory" in msg


class TestDefaultsApplied:
    def test_missing_actions_block_applies_builtin_defaults(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        for name, manifest in BUILTIN_ACTIONS.items():
            assert name in env.actions
            assert env.actions[name].enabled is manifest.default_enabled

    def test_docker_hygiene_default_disabled_and_command_mode_disabled(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        docker_cfg = env.actions["docker_hygiene"]
        assert docker_cfg.enabled is False
        assert docker_cfg.command_mode == "disabled"

    def test_patching_default_enabled(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        assert env.actions["patching"].enabled is True

    def test_partial_actions_fills_missing_with_defaults(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={"docker_hygiene": ActionConfig(enabled=True, command_mode="wrapper")},
        )
        # Other actions should have defaults applied
        assert env.actions["patching"].enabled is True
        assert env.actions["disk_cleanup"].enabled is True
        assert env.actions["log_rotation"].enabled is True
        assert env.actions["backup_verify"].enabled is False

    def test_all_builtin_actions_present_after_defaults(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        assert set(env.actions.keys()) == set(BUILTIN_ACTIONS.keys())

    def test_defaults_via_yaml_load(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(_inventory_yaml({"dev": {"targets": [_TARGET]}}))
        inv = validate_inventory(config_file)
        env = inv.environments["dev"]
        for name, manifest in BUILTIN_ACTIONS.items():
            assert env.actions[name].enabled is manifest.default_enabled


class TestContradictionRejected:
    def test_docker_hygiene_enabled_true_disabled_mode_raises(self) -> None:
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            EnvironmentSchema(
                targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
                actions={
                    "docker_hygiene": ActionConfig(enabled=True, command_mode="disabled"),
                },
            )
        assert "contradiction" in str(exc_info.value).lower() or "disabled" in str(exc_info.value)

    def test_docker_hygiene_enabled_wrapper_mode_accepted(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={"docker_hygiene": ActionConfig(enabled=True, command_mode="wrapper")},
        )
        assert env.actions["docker_hygiene"].enabled is True
        assert env.actions["docker_hygiene"].command_mode == "wrapper"

    def test_legacy_docker_prune_key_raises_config_error(self) -> None:
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            EnvironmentSchema(
                targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
                actions={"docker_prune": ActionConfig(enabled=False, command_mode="disabled")},
            )
        assert "docker_prune" in str(exc_info.value)
        assert "migrate" in str(exc_info.value).lower()


class TestServiceRestartValidation:
    def test_service_restart_enabled_with_empty_units_raises(self) -> None:
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            EnvironmentSchema(
                targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
                actions={"service_restart": ActionConfig(enabled=True, restartable_units=[])},
            )
        msg = str(exc_info.value)
        assert "restartable_units" in msg
        assert "service_restart" in msg

    def test_service_restart_enabled_with_units_accepted(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={"service_restart": ActionConfig(enabled=True, restartable_units=["nginx"])},
        )
        assert env.actions["service_restart"].enabled is True
        assert "nginx" in env.actions["service_restart"].restartable_units

    def test_service_restart_disabled_with_empty_units_accepted(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(**_TARGET)],  # type: ignore[arg-type]
            actions={"service_restart": ActionConfig(enabled=False, restartable_units=[])},
        )
        assert env.actions["service_restart"].enabled is False

    def test_service_restart_default_disabled_no_units_required(self) -> None:
        env = EnvironmentSchema(targets=[TargetSchema(**_TARGET)])  # type: ignore[arg-type]
        cfg = env.actions["service_restart"]
        assert cfg.enabled is False
        assert cfg.restartable_units == []

    def test_service_restart_enabled_via_yaml_with_units(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(_inventory_yaml({
            "prod": _env_with_actions({
                "service_restart": {"enabled": True, "restartable_units": ["nginx", "gunicorn"]},
            }),
        }))
        inv = validate_inventory(config_file)
        cfg = inv.environments["prod"].actions["service_restart"]
        assert cfg.enabled is True
        assert "nginx" in cfg.restartable_units
        assert "gunicorn" in cfg.restartable_units

    def test_service_restart_enabled_via_yaml_empty_units_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(_inventory_yaml({
            "prod": _env_with_actions({
                "service_restart": {"enabled": True, "restartable_units": []},
            }),
        }))
        with pytest.raises((ConfigError, ValidationError, ValueError)) as exc_info:
            validate_inventory(config_file)
        assert "restartable_units" in str(exc_info.value)


class TestDockerHygieneV15Config:
    """v1.5 volume/build_cache config fields default and validate correctly."""

    def test_volume_deletion_enabled_defaults_to_false(self) -> None:
        cfg = ActionConfig(enabled=True)
        assert cfg.volume_deletion_enabled is False

    def test_volume_last_mount_days_threshold_defaults_to_90(self) -> None:
        cfg = ActionConfig(enabled=True)
        assert cfg.volume_last_mount_days_threshold == 90

    def test_build_cache_deletion_enabled_defaults_to_false(self) -> None:
        cfg = ActionConfig(enabled=True)
        assert cfg.build_cache_deletion_enabled is False

    def test_volume_deletion_enabled_with_action_disabled_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(_inventory_yaml({
            "prod": _env_with_actions({
                "docker_hygiene": {
                    "enabled": False,
                    "volume_deletion_enabled": True,
                },
            }),
        }))
        with pytest.raises((ConfigError, ValueError)) as exc_info:
            validate_inventory(config_file)
        assert "volume_deletion_enabled" in str(exc_info.value) or "enabled=false" in str(exc_info.value)
