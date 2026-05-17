"""Tests for ActionManifest model."""

from __future__ import annotations

import pytest

from errander.models.manifest import ActionManifest


def _make_manifest(**overrides: object) -> ActionManifest:
    defaults: dict[str, object] = {
        "name": "test_action",
        "default_enabled": True,
        "risk_tier": "LOW",
        "command_modes": None,
        "required_binaries": ("/usr/bin/foo",),
        "required_wrappers": (),
        "setup_doc": "SETUP.md#section",
    }
    defaults.update(overrides)
    return ActionManifest(**defaults)  # type: ignore[arg-type]


class TestActionManifestImmutability:
    def test_frozen_rejects_field_mutation(self) -> None:
        m = _make_manifest()
        with pytest.raises((AttributeError, TypeError)):
            m.name = "changed"  # type: ignore[misc]

    def test_frozen_rejects_requires_config_mutation(self) -> None:
        m = _make_manifest(requires_config_section="backup")
        with pytest.raises((AttributeError, TypeError)):
            m.requires_config_section = None  # type: ignore[misc]


class TestActionManifestTypes:
    def test_required_binaries_is_tuple(self) -> None:
        m = _make_manifest(required_binaries=("/usr/bin/a", "/usr/bin/b"))
        assert isinstance(m.required_binaries, tuple)

    def test_required_wrappers_is_tuple(self) -> None:
        m = _make_manifest(required_wrappers=("/usr/local/sbin/w",))
        assert isinstance(m.required_wrappers, tuple)

    def test_command_modes_none_when_no_mode_concept(self) -> None:
        m = _make_manifest(command_modes=None)
        assert m.command_modes is None

    def test_command_modes_tuple_when_action_has_modes(self) -> None:
        m = _make_manifest(command_modes=("disabled", "wrapper", "direct_sudo"))
        assert isinstance(m.command_modes, tuple)
        assert len(m.command_modes) == 3

    def test_requires_config_section_defaults_to_none(self) -> None:
        m = _make_manifest()
        assert m.requires_config_section is None

    def test_risk_tier_valid_values(self) -> None:
        for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            m = _make_manifest(risk_tier=tier)
            assert m.risk_tier == tier
