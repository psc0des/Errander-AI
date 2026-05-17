"""Tests for BUILTIN_ACTIONS central registry."""

from __future__ import annotations

from errander.agent.subgraphs import BUILTIN_ACTIONS


class TestRegistryContents:
    def test_has_exactly_five_entries(self) -> None:
        assert len(BUILTIN_ACTIONS) == 5

    def test_expected_action_names_present(self) -> None:
        expected = {"patching", "disk_cleanup", "log_rotation", "docker_prune", "backup_verify"}
        assert set(BUILTIN_ACTIONS.keys()) == expected

    def test_docker_prune_default_disabled(self) -> None:
        assert BUILTIN_ACTIONS["docker_prune"].default_enabled is False

    def test_patching_default_enabled(self) -> None:
        assert BUILTIN_ACTIONS["patching"].default_enabled is True

    def test_disk_cleanup_default_enabled(self) -> None:
        assert BUILTIN_ACTIONS["disk_cleanup"].default_enabled is True

    def test_log_rotation_default_enabled(self) -> None:
        assert BUILTIN_ACTIONS["log_rotation"].default_enabled is True

    def test_backup_verify_default_disabled(self) -> None:
        assert BUILTIN_ACTIONS["backup_verify"].default_enabled is False

    def test_manifest_names_match_dict_keys(self) -> None:
        for key, manifest in BUILTIN_ACTIONS.items():
            assert manifest.name == key

    def test_docker_prune_has_command_modes(self) -> None:
        modes = BUILTIN_ACTIONS["docker_prune"].command_modes
        assert modes is not None
        assert "disabled" in modes
        assert "wrapper" in modes
        assert "direct_sudo" in modes

    def test_patching_has_no_command_modes(self) -> None:
        assert BUILTIN_ACTIONS["patching"].command_modes is None

    def test_backup_verify_requires_config_section(self) -> None:
        assert BUILTIN_ACTIONS["backup_verify"].requires_config_section == "backup"

    def test_patching_risk_tier_medium(self) -> None:
        assert BUILTIN_ACTIONS["patching"].risk_tier == "MEDIUM"

    def test_docker_prune_risk_tier_medium(self) -> None:
        assert BUILTIN_ACTIONS["docker_prune"].risk_tier == "MEDIUM"
