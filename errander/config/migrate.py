"""Migration helper for converting legacy inventory YAML to the new nested actions schema."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml


def migrate_inventory(path: Path) -> Path:
    """Read old inventory YAML and write a <file>.migrated with nested actions: block.

    Returns path to the .migrated file.
    Raises FileExistsError if .migrated already exists (operator must delete first).
    Prints unified diff to stdout.

    Translation rules:
    - docker_command_mode: wrapper/direct_sudo  → actions.docker_prune.enabled=True, command_mode=<mode>
    - docker_command_mode: disabled             → actions.docker_prune.enabled=False, command_mode=disabled
    - missing actions entry                     → filled from BUILTIN_ACTIONS defaults
    - existing actions entry                    → preserved verbatim
    """
    from errander.agent.subgraphs import BUILTIN_ACTIONS

    migrated_path = path.with_suffix(path.suffix + ".migrated")

    if migrated_path.exists():
        raise FileExistsError(
            f"{migrated_path} already exists. Delete it first to re-run migration."
        )

    original_text = path.read_text()
    data: dict[str, Any] = yaml.safe_load(original_text)

    if not isinstance(data, dict) or "environments" not in data:
        raise ValueError("inventory YAML must have a top-level 'environments:' key")

    for _env_name, env_data in data["environments"].items():
        if not isinstance(env_data, dict):
            continue

        legacy_mode: str | None = env_data.pop("docker_command_mode", None)
        existing_actions: dict[str, Any] = env_data.get("actions", {}) or {}

        full_actions: dict[str, Any] = {}
        for name, manifest in BUILTIN_ACTIONS.items():
            if name in existing_actions:
                full_actions[name] = existing_actions[name]
            elif name == "docker_prune" and legacy_mode is not None:
                if legacy_mode in ("wrapper", "direct_sudo"):
                    full_actions[name] = {"enabled": True, "command_mode": legacy_mode}
                else:
                    full_actions[name] = {"enabled": False, "command_mode": "disabled"}
            else:
                entry: dict[str, Any] = {"enabled": manifest.default_enabled}
                if manifest.command_modes is not None:
                    entry["command_mode"] = manifest.command_modes[0]
                full_actions[name] = entry

        env_data["actions"] = full_actions

    migrated_text = yaml.dump(data, default_flow_style=False, allow_unicode=True)

    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        migrated_text.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(migrated_path),
    )
    print("".join(diff), end="")

    migrated_path.write_text(migrated_text)
    return migrated_path
