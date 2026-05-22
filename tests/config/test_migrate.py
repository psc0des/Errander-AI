"""Tests for the inventory migration helper (commit 1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from errander.agent.subgraphs import BUILTIN_ACTIONS
from errander.config.migrate import migrate_inventory

_TARGET = {"host": "10.0.0.1", "name": "web-01", "os_family": "ubuntu"}


def _inv(envs: dict) -> str:  # type: ignore[type-arg]
    return yaml.dump({"environments": envs}, default_flow_style=False)


class TestDockerCommandModeMigration:
    def test_wrapper_mode_maps_to_enabled_true(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"prod": {"docker_command_mode": "wrapper", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        docker = data["environments"]["prod"]["actions"]["docker_hygiene"]
        assert docker["enabled"] is True
        assert docker["command_mode"] == "wrapper"

    def test_direct_sudo_mode_maps_to_enabled_true_wrapper_mode(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "direct_sudo", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        # direct_sudo is unsupported by docker_hygiene — migrated to wrapper mode with a warning
        docker = data["environments"]["dev"]["actions"]["docker_hygiene"]
        assert docker["enabled"] is True
        assert docker["command_mode"] == "wrapper"

    def test_disabled_mode_maps_to_enabled_false(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        docker = data["environments"]["dev"]["actions"]["docker_hygiene"]
        assert docker["enabled"] is False
        assert docker["command_mode"] == "wrapper"

    def test_legacy_field_removed_from_env(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        assert "docker_command_mode" not in data["environments"]["dev"]


class TestFullActionsSynthesis:
    def test_all_builtin_actions_present_in_migrated_output(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "wrapper", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        actions = data["environments"]["dev"]["actions"]
        assert set(actions.keys()) == set(BUILTIN_ACTIONS.keys())

    def test_non_docker_actions_get_builtin_defaults(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        actions = data["environments"]["dev"]["actions"]
        for name, manifest in BUILTIN_ACTIONS.items():
            if name != "docker_hygiene":
                assert actions[name]["enabled"] is manifest.default_enabled

    def test_no_docker_command_mode_applies_docker_hygiene_default(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        docker = data["environments"]["dev"]["actions"]["docker_hygiene"]
        assert docker["enabled"] is False
        assert docker["command_mode"] == "disabled"


class TestFieldPreservation:
    def test_preserves_ssh_user_and_other_env_fields(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({
            "dev": {
                "ssh_user": "myuser",
                "approval_policy": "relaxed",
                "docker_command_mode": "disabled",
                "targets": [_TARGET],
            },
        }))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        env = data["environments"]["dev"]
        assert env["ssh_user"] == "myuser"
        assert env["approval_policy"] == "relaxed"
        assert "docker_command_mode" not in env

    def test_preserves_targets_verbatim(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}}))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        assert data["environments"]["dev"]["targets"] == [_TARGET]

    def test_multiple_environments_each_migrated(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({
            "prod": {"docker_command_mode": "wrapper", "targets": [_TARGET]},
            "dev": {"docker_command_mode": "disabled", "targets": [_TARGET]},
        }))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        assert data["environments"]["prod"]["actions"]["docker_hygiene"]["enabled"] is True
        assert data["environments"]["dev"]["actions"]["docker_hygiene"]["enabled"] is False


class TestFileHandling:
    def test_writes_migrated_file_not_original(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        original = _inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}})
        inv.write_text(original)
        migrated_path = migrate_inventory(inv)
        assert migrated_path == inv.with_suffix(".yaml.migrated")
        assert inv.read_text() == original

    def test_migrated_path_has_migrated_suffix(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"targets": [_TARGET]}}))
        result = migrate_inventory(inv)
        assert result.name == "inventory.yaml.migrated"

    def test_refuses_if_migrated_already_exists(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"targets": [_TARGET]}}))
        (tmp_path / "inventory.yaml.migrated").write_text("existing content")
        with pytest.raises(FileExistsError, match="already exists"):
            migrate_inventory(inv)

    def test_missing_environments_key_raises(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text("foo: bar\n")
        with pytest.raises(ValueError, match="environments"):
            migrate_inventory(inv)


class TestIdempotency:
    def test_idempotent_on_new_schema_file(self, tmp_path: Path) -> None:
        actions: dict = {}  # type: ignore[type-arg]
        for name, manifest in BUILTIN_ACTIONS.items():
            entry: dict = {"enabled": manifest.default_enabled}  # type: ignore[type-arg]
            if manifest.command_modes is not None:
                entry["command_mode"] = manifest.command_modes[0]
            actions[name] = entry

        data = {
            "environments": {
                "dev": {
                    "targets": [_TARGET],
                    "actions": actions,
                },
            },
        }
        canonical = yaml.dump(data, default_flow_style=False, allow_unicode=True)
        inv = tmp_path / "inventory.yaml"
        inv.write_text(canonical)
        migrated = migrate_inventory(inv)
        assert migrated.read_text() == canonical

    def test_existing_actions_preserved_verbatim(self, tmp_path: Path) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({
            "dev": {
                "targets": [_TARGET],
                "actions": {
                    "docker_prune": {"enabled": True, "command_mode": "wrapper"},
                },
            },
        }))
        migrated = migrate_inventory(inv)
        data = yaml.safe_load(migrated.read_text())
        # docker_prune is renamed to docker_hygiene by the migrator
        docker = data["environments"]["dev"]["actions"]["docker_hygiene"]
        assert docker["enabled"] is True
        assert docker["command_mode"] == "wrapper"


class TestDiffOutput:
    def test_diff_printed_when_changes_made(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "wrapper", "targets": [_TARGET]}}))
        migrate_inventory(inv)
        captured = capsys.readouterr()
        assert "docker_command_mode" in captured.out or "-" in captured.out

    def test_diff_contains_removal_and_addition_markers(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(_inv({"dev": {"docker_command_mode": "disabled", "targets": [_TARGET]}}))
        migrate_inventory(inv)
        out = capsys.readouterr().out
        assert "-" in out
        assert "+" in out
