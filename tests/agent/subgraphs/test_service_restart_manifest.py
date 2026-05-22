"""Tests for service_restart MANIFEST fields and BUILTIN_ACTIONS registry registration."""

from __future__ import annotations

from errander.agent.subgraphs import BUILTIN_ACTIONS
from errander.agent.subgraphs.service_restart import MANIFEST


class TestServiceRestartManifest:
    def test_name_is_service_restart(self) -> None:
        assert MANIFEST.name == "service_restart"

    def test_default_disabled(self) -> None:
        assert MANIFEST.default_enabled is False

    def test_risk_tier_is_high(self) -> None:
        assert MANIFEST.risk_tier == "HIGH"

    def test_no_command_modes(self) -> None:
        assert MANIFEST.command_modes is None

    def test_required_binaries(self) -> None:
        assert "/bin/systemctl" in MANIFEST.required_binaries
        assert "/bin/journalctl" in MANIFEST.required_binaries

    def test_required_wrapper(self) -> None:
        assert "/usr/local/sbin/errander-systemctl-restart" in MANIFEST.required_wrappers

    def test_setup_doc_anchor(self) -> None:
        assert "SETUP.md" in MANIFEST.setup_doc
        assert "service-restart" in MANIFEST.setup_doc

    def test_no_config_section_required(self) -> None:
        # Unlike backup_verify, service_restart uses restartable_units in the
        # actions block itself — no separate top-level settings section
        assert MANIFEST.requires_config_section is None

    def test_manifest_is_frozen(self) -> None:
        import pytest
        with pytest.raises((AttributeError, TypeError)):
            MANIFEST.name = "other"  # type: ignore[misc]


class TestServiceRestartRegistry:
    def test_registered_in_builtin_actions(self) -> None:
        assert "service_restart" in BUILTIN_ACTIONS

    def test_registry_has_six_entries(self) -> None:
        # docker_prune removed in Session 3; 6 actions remain.
        assert len(BUILTIN_ACTIONS) == 6

    def test_registry_manifest_matches_module_manifest(self) -> None:
        assert BUILTIN_ACTIONS["service_restart"] is MANIFEST

    def test_service_restart_is_high_risk(self) -> None:
        assert BUILTIN_ACTIONS["service_restart"].risk_tier == "HIGH"

    def test_service_restart_default_disabled(self) -> None:
        assert BUILTIN_ACTIONS["service_restart"].default_enabled is False

    def test_all_manifest_names_match_keys(self) -> None:
        for key, manifest in BUILTIN_ACTIONS.items():
            assert manifest.name == key
