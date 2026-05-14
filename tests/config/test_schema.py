"""Tests for YAML schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from errander.config.schema import (
    EnvironmentSchema,
    InventoryConfig,
    PoliciesConfig,
    PolicySchema,
    SettingsConfig,
    TargetSchema,
    validate_inventory,
    validate_policies,
    validate_settings,
)


class TestTargetSchema:
    """Tests for individual target validation."""

    def test_valid_target(self) -> None:
        t = TargetSchema(host="10.0.1.10", name="web-01", os_family="ubuntu")
        assert t.host == "10.0.1.10"
        assert t.os_family == "ubuntu"

    def test_os_family_normalized_to_lowercase(self) -> None:
        t = TargetSchema(host="10.0.1.10", name="web-01", os_family="Ubuntu")
        assert t.os_family == "ubuntu"

    def test_invalid_os_family_rejected(self) -> None:
        with pytest.raises(ValidationError, match="os_family"):
            TargetSchema(host="10.0.1.10", name="web-01", os_family="windows")

    def test_empty_host_rejected(self) -> None:
        with pytest.raises(ValidationError, match="host"):
            TargetSchema(host="  ", name="web-01", os_family="ubuntu")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            TargetSchema(host="10.0.1.10", name="", os_family="ubuntu")

    def test_optional_fields_default_to_none(self) -> None:
        t = TargetSchema(host="10.0.1.10", name="web-01", os_family="rhel")
        assert t.ssh_user is None
        assert t.ssh_key_path is None
        assert t.policy is None

    def test_tags_default_to_empty(self) -> None:
        t = TargetSchema(host="10.0.1.10", name="web-01", os_family="debian")
        assert t.tags == []


class TestEnvironmentSchema:
    """Tests for environment-level validation."""

    def test_valid_environment(self) -> None:
        env = EnvironmentSchema(
            targets=[TargetSchema(host="10.0.1.10", name="web-01", os_family="ubuntu")],
        )
        assert env.ssh_user == "errander-ai"
        assert env.approval_policy == "strict"

    def test_invalid_policy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="approval_policy"):
            EnvironmentSchema(
                approval_policy="extreme",
                targets=[TargetSchema(host="10.0.1.10", name="web-01", os_family="ubuntu")],
            )

    def test_null_maintenance_window(self) -> None:
        env = EnvironmentSchema(
            maintenance_window=None,
            targets=[TargetSchema(host="10.0.1.10", name="web-01", os_family="ubuntu")],
        )
        assert env.maintenance_window is None


class TestInventoryConfig:
    """Tests for full inventory config."""

    def test_valid_inventory(self) -> None:
        config = InventoryConfig(
            environments={
                "dev": EnvironmentSchema(
                    targets=[
                        TargetSchema(host="10.0.1.10", name="web-01", os_family="ubuntu"),
                    ],
                ),
            },
        )
        assert len(config.environments) == 1
        assert len(config.environments["dev"].targets) == 1

    def test_empty_environments_allowed(self) -> None:
        config = InventoryConfig(environments={})
        assert len(config.environments) == 0


class TestValidateInventory:
    """Tests for validate_inventory file loading."""

    def test_valid_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(
            yaml.dump({
                "environments": {
                    "dev": {
                        "targets": [
                            {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                        ],
                    },
                },
            }),
        )
        config = validate_inventory(config_file)
        assert "dev" in config.environments

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            validate_inventory(tmp_path / "nonexistent.yaml")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("not: [valid: yaml: structure")
        with pytest.raises(yaml.YAMLError):
            validate_inventory(config_file)

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "list.yaml"
        config_file.write_text("- item1\n- item2")
        with pytest.raises(ValueError, match="YAML mapping"):
            validate_inventory(config_file)

    def test_missing_required_fields_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "incomplete.yaml"
        config_file.write_text(
            yaml.dump({
                "environments": {
                    "dev": {
                        "targets": [
                            {"host": "10.0.1.10"},  # missing name and os_family
                        ],
                    },
                },
            }),
        )
        with pytest.raises(ValidationError):
            validate_inventory(config_file)

    def test_invalid_os_family_in_file_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / "bad_os.yaml"
        config_file.write_text(
            yaml.dump({
                "environments": {
                    "dev": {
                        "targets": [
                            {"host": "10.0.1.10", "name": "w1", "os_family": "freebsd"},
                        ],
                    },
                },
            }),
        )
        with pytest.raises(ValidationError, match="os_family"):
            validate_inventory(config_file)


class TestValidatePolicies:
    """Tests for policies YAML validation."""

    def test_valid_policies_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "policies.yaml"
        config_file.write_text(
            yaml.dump({
                "policies": {
                    "relaxed": {"auto_approve": ["low", "medium"]},
                    "strict": {"auto_approve": ["low"]},
                },
            }),
        )
        config = validate_policies(config_file)
        assert "relaxed" in config.policies
        assert config.policies["relaxed"].auto_approve == ["low", "medium"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            validate_policies(tmp_path / "nope.yaml")

    def test_defaults_applied(self, tmp_path: Path) -> None:
        config_file = tmp_path / "policies.yaml"
        config_file.write_text(yaml.dump({"policies": {"minimal": {}}}))
        config = validate_policies(config_file)
        p = config.policies["minimal"]
        assert p.disk_cleanup_threshold == 80
        assert p.docker_prune_all is False


class TestValidateSettings:
    """Tests for settings YAML validation."""

    def test_valid_settings_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            yaml.dump({
                "agent": {"approval_timeout_seconds": 900},
                "llm": {"timeout_seconds": 60},
            }),
        )
        config = validate_settings(config_file)
        assert config.agent.approval_timeout_seconds == 900
        assert config.llm.timeout_seconds == 60

    def test_defaults_when_empty(self, tmp_path: Path) -> None:
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(yaml.dump({}))
        config = validate_settings(config_file)
        assert config.agent.approval_timeout_seconds == 1800
        assert config.slack.poll_interval_seconds == 30

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            validate_settings(tmp_path / "nope.yaml")


# --- Settings bounds validation tests (Step 4) ---

class TestAgentSettingsBoundsValidation:
    """Step 4: Phase 3 settings must reject out-of-bounds values at load time."""

    def test_rolling_pct_rejects_zero(self) -> None:
        with pytest.raises(ValidationError, match="rolling_update_percentage"):
            from errander.config.schema import AgentSettingsSchema
            AgentSettingsSchema(rolling_update_percentage=0)

    def test_rolling_pct_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="rolling_update_percentage"):
            from errander.config.schema import AgentSettingsSchema
            AgentSettingsSchema(rolling_update_percentage=-10)

    def test_rolling_pct_rejects_over_100(self) -> None:
        with pytest.raises(ValidationError, match="rolling_update_percentage"):
            from errander.config.schema import AgentSettingsSchema
            AgentSettingsSchema(rolling_update_percentage=150)

    def test_rolling_pct_accepts_boundary_values(self) -> None:
        from errander.config.schema import AgentSettingsSchema

        s1 = AgentSettingsSchema(rolling_update_percentage=1)
        s100 = AgentSettingsSchema(rolling_update_percentage=100)
        assert s1.rolling_update_percentage == 1
        assert s100.rolling_update_percentage == 100

    def test_wave_threshold_rejects_out_of_range(self) -> None:
        from errander.config.schema import AgentSettingsSchema

        with pytest.raises(ValidationError, match="failure threshold"):
            AgentSettingsSchema(wave_failure_threshold=1.5)
        with pytest.raises(ValidationError, match="failure threshold"):
            AgentSettingsSchema(wave_failure_threshold=-0.1)

    def test_timeout_rejects_zero_and_huge(self) -> None:
        from errander.config.schema import AgentSettingsSchema

        with pytest.raises(ValidationError, match="timeout"):
            AgentSettingsSchema(approval_timeout_seconds=0)
        with pytest.raises(ValidationError, match="timeout"):
            AgentSettingsSchema(approval_timeout_seconds=999999)
