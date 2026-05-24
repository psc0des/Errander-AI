"""Action manifest model — describes each built-in sub-graph's capabilities.

Each sub-graph in agent/subgraphs/ exports a MANIFEST of this type.
The central registry (BUILTIN_ACTIONS in agent/subgraphs/__init__.py)
aggregates them for use by config validation and preflight checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ActionManifest:
    """Immutable descriptor for one built-in maintenance action.

    Attributes:
        name: Action identifier (matches ActionType value and inventory key).
        default_enabled: Whether the action is on by default when absent from
            the inventory's ``actions:`` block.
        risk_tier: CLAUDE.md risk classification.
        command_modes: Ordered tuple of accepted ``command_mode`` strings, or
            None when the action has no mode concept.
        required_binaries: Absolute paths to binaries that must exist on target
            VMs (checked with ``command -v`` during ``--check-targets``).
        required_wrappers: Absolute paths to root-owned wrapper scripts that
            must be installed on target VMs (empty when not applicable).
        setup_doc: Anchor in SETUP.md pointing to installation instructions.
        requires_config_section: Name of a settings.yaml block that must be
            present for this action to be useful (e.g. ``"backup"``).
    """

    name: str
    default_enabled: bool
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    command_modes: tuple[str, ...] | None
    required_binaries: tuple[str, ...]
    required_wrappers: tuple[str, ...]
    setup_doc: str
    requires_config_section: str | None = None
    # True = never included in automated batch plans; only runs via explicit CLI
    # (e.g. --restart-service). enabled: true in inventory just means "configured".
    operator_triggered: bool = False
