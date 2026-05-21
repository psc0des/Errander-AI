"""Tests for BUILTIN_ACTIONS central registry."""

from __future__ import annotations

from errander.agent.subgraphs import BUILTIN_ACTIONS


class TestRegistryContents:
    def test_has_exactly_seven_entries(self) -> None:
        # docker_hygiene added in v1.1 Session 1; docker_prune removed in Session 3.
        assert len(BUILTIN_ACTIONS) == 7

    def test_expected_action_names_present(self) -> None:
        expected = {
            "patching", "disk_cleanup", "log_rotation",
            "docker_prune", "docker_hygiene", "backup_verify", "service_restart",
        }
        assert set(BUILTIN_ACTIONS.keys()) == expected

    def test_docker_prune_default_disabled(self) -> None:
        assert BUILTIN_ACTIONS["docker_prune"].default_enabled is False

    def test_docker_hygiene_default_disabled(self) -> None:
        assert BUILTIN_ACTIONS["docker_hygiene"].default_enabled is False

    def test_docker_hygiene_has_wrapper_only_modes(self) -> None:
        modes = BUILTIN_ACTIONS["docker_hygiene"].command_modes
        assert modes is not None
        assert "disabled" in modes
        assert "wrapper" in modes
        # direct_sudo intentionally not supported — per-object validation requires wrapper.
        assert "direct_sudo" not in modes

    def test_docker_hygiene_risk_tier_medium(self) -> None:
        assert BUILTIN_ACTIONS["docker_hygiene"].risk_tier == "MEDIUM"

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
