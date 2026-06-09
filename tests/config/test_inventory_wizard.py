"""Tests for errander.config.inventory_wizard — YAML renderer and ruamel round-trip."""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from errander.config.inventory_wizard import (
    EnvData,
    TargetData,
    _count_vms,
    _render_inventory_yaml,
    _summarise_existing,
)
from errander.config.schema import InventoryConfig

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_env(
    name: str = "production",
    enable_docker: bool = False,
    enable_service_restart_on_target: bool = False,
) -> EnvData:
    """Return a minimal EnvData suitable for rendering."""
    target = TargetData(
        host="10.0.1.10",
        name=f"{name}-web-01",
        os_family="ubuntu",
        tags=[name, "web"],
        service_restart_units=["nginx.service"] if enable_service_restart_on_target else [],
    )
    return EnvData(
        name=name,
        ssh_user="errander",
        ssh_key_path=f"~/.ssh/errander_{name}",
        approval_policy="strict",
        maintenance_window="02:00-06:00",
        maintenance_days=["tuesday", "thursday"],
        maintenance_timezone="UTC",
        enable_patching=True,
        enable_disk_cleanup=True,
        enable_log_rotation=True,
        enable_docker_hygiene=enable_docker,
        enable_backup_verify=False,
        targets=[target],
    )


# ── TestRenderInventoryYaml ────────────────────────────────────────────────────


class TestRenderInventoryYaml:
    def test_single_env_single_vm_loads_via_schema(self) -> None:
        """Generated YAML must pass InventoryConfig schema validation."""
        env = _make_env()
        rendered = _render_inventory_yaml([env], "2026-06-09")
        data = yaml.safe_load(rendered)
        # Should not raise
        InventoryConfig.model_validate(data)

    def test_header_and_key_sections_present(self) -> None:
        """Rendered file must contain the doc header and all required sections."""
        rendered = _render_inventory_yaml([_make_env()], "2026-06-09")
        assert "Errander-AI — Inventory" in rendered
        assert "Generated: 2026-06-09" in rendered
        assert "environments:" in rendered
        assert "actions:" in rendered
        assert "patching:" in rendered
        assert "disk_cleanup:" in rendered
        assert "log_rotation:" in rendered
        assert "docker_hygiene:" in rendered
        assert "service_restart:" in rendered
        assert "node_exporter:" in rendered
        assert "critical_services:" in rendered
        assert "maintenance_window:" in rendered
        assert "approval_policy:" in rendered

    def test_docker_hygiene_disabled_generates_disabled_mode(self) -> None:
        env = _make_env(enable_docker=False)
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "command_mode: disabled" in rendered
        assert "enabled: false" in rendered

    def test_docker_hygiene_enabled_generates_wrapper_mode(self) -> None:
        env = _make_env(enable_docker=True)
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "command_mode: wrapper" in rendered
        # enabled: true must appear for docker_hygiene
        assert "enabled: true" in rendered

    def test_docker_hygiene_enabled_still_valid_schema(self) -> None:
        env = _make_env(enable_docker=True)
        rendered = _render_inventory_yaml([env], "2026-06-09")
        data = yaml.safe_load(rendered)
        InventoryConfig.model_validate(data)

    def test_per_target_service_restart_units_rendered(self) -> None:
        env = _make_env(enable_service_restart_on_target=True)
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "nginx.service" in rendered
        assert "enabled: true" in rendered
        assert "restartable_units:" in rendered

    def test_per_target_service_restart_valid_schema(self) -> None:
        env = _make_env(enable_service_restart_on_target=True)
        rendered = _render_inventory_yaml([env], "2026-06-09")
        data = yaml.safe_load(rendered)
        InventoryConfig.model_validate(data)

    def test_multi_env_both_appear_in_output(self) -> None:
        envs = [_make_env("production"), _make_env("staging")]
        rendered = _render_inventory_yaml(envs, "2026-06-09")
        data = yaml.safe_load(rendered)
        assert "production" in data["environments"]
        assert "staging" in data["environments"]

    def test_multi_env_loads_via_schema(self) -> None:
        envs = [_make_env("production"), _make_env("staging")]
        rendered = _render_inventory_yaml(envs, "2026-06-09")
        data = yaml.safe_load(rendered)
        InventoryConfig.model_validate(data)

    def test_per_target_disable_docker_hygiene(self) -> None:
        env = _make_env(enable_docker=True)
        env.targets[0].disable_docker_hygiene = True
        rendered = _render_inventory_yaml([env], "2026-06-09")
        data = yaml.safe_load(rendered)
        InventoryConfig.model_validate(data)
        # Target must have explicit docker_hygiene: false override
        target = data["environments"]["production"]["targets"][0]
        assert target.get("actions", {}).get("docker_hygiene", {}).get("enabled") is False

    def test_env_with_no_targets_renders_placeholder(self) -> None:
        env = _make_env()
        env.targets = []
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "No VMs configured yet" in rendered

    def test_target_tags_rendered_as_flow_list(self) -> None:
        env = _make_env()
        env.targets[0].tags = ["web", "prod", "tier-1"]
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "[web, prod, tier-1]" in rendered

    def test_commented_override_template_present_when_no_overrides(self) -> None:
        """When no per-target overrides are set, commented template must appear."""
        env = _make_env()
        # no overrides on the target
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "Per-target overrides" in rendered

    def test_approval_policy_written_correctly(self) -> None:
        env = _make_env()
        env.approval_policy = "moderate"
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "approval_policy: moderate" in rendered

    def test_maintenance_days_rendered_as_block_list(self) -> None:
        env = _make_env()
        env.maintenance_days = ["monday", "wednesday", "friday"]
        rendered = _render_inventory_yaml([env], "2026-06-09")
        assert "      - monday" in rendered
        assert "      - wednesday" in rendered
        assert "      - friday" in rendered


# ── TestRuamelRoundtrip ────────────────────────────────────────────────────────


class TestRuamelRoundtrip:
    def test_comments_preserved_after_node_exporter_update(self, tmp_path: Path) -> None:
        """Updating node_exporter via configure.py must not strip comments."""
        inventory_path = tmp_path / "inventory.yaml"
        # Write a YAML file with comments (mimics output of inventory wizard)
        inventory_path.write_text(
            textwrap.dedent("""\
                # Errander-AI — Inventory
                # Generated: 2026-06-09
                environments:
                  production:
                    ssh_user: errander           # SSH user for all targets
                    ssh_key_path: ~/.ssh/key
                    approval_policy: strict
                    maintenance_window: "02:00-06:00"
                    maintenance_days:
                      - tuesday
                    maintenance_timezone: UTC
                    node_exporter: false  # set by configure.sh
                    critical_services:
                      - ssh
                    actions:
                      patching:
                        enabled: true       # OS patches
                      disk_cleanup:
                        enabled: true
                      log_rotation:
                        enabled: true
                      docker_hygiene:
                        enabled: false
                        command_mode: disabled
                      backup_verify:
                        enabled: false
                      service_restart:
                        enabled: false
                        restartable_units: []
                    targets:
                      - host: 10.0.1.10
                        name: prod-web-01
                        os_family: ubuntu
                        tags: [web, prod]
                        node_exporter: false  # updated by configure.sh
            """),
            encoding="utf-8",
        )

        # Simulate what configure.py's _update_inventory_yaml does
        from errander.config.configure import _update_inventory_yaml

        results: dict[str, dict[str, bool | None]] = {"production": {"prod-web-01": True}}
        _update_inventory_yaml(inventory_path, results)

        updated = inventory_path.read_text(encoding="utf-8")

        # Comments must survive
        assert "Errander-AI — Inventory" in updated
        assert "SSH user for all targets" in updated
        assert "OS patches" in updated
        assert "updated by configure.sh" in updated

        # Values must be updated
        assert "node_exporter: true" in updated

    def test_none_result_leaves_node_exporter_unchanged(self, tmp_path: Path) -> None:
        """None result (SSH unreachable) must not change node_exporter value."""
        inventory_path = tmp_path / "inventory.yaml"
        inventory_path.write_text(
            textwrap.dedent("""\
                environments:
                  dev:
                    ssh_user: errander
                    ssh_key_path: ~/.ssh/key
                    approval_policy: strict
                    maintenance_window: "08:00-20:00"
                    maintenance_days: [monday]
                    maintenance_timezone: UTC
                    node_exporter: false
                    critical_services: []
                    actions:
                      patching:
                        enabled: true
                      disk_cleanup:
                        enabled: true
                      log_rotation:
                        enabled: true
                      docker_hygiene:
                        enabled: false
                        command_mode: disabled
                      backup_verify:
                        enabled: false
                      service_restart:
                        enabled: false
                        restartable_units: []
                    targets:
                      - host: 10.0.0.10
                        name: dev-vm-01
                        os_family: ubuntu
                        tags: [dev]
                        node_exporter: false
            """),
            encoding="utf-8",
        )

        from errander.config.configure import _update_inventory_yaml

        results: dict[str, dict[str, bool | None]] = {"dev": {"dev-vm-01": None}}
        _update_inventory_yaml(inventory_path, results)

        updated = inventory_path.read_text(encoding="utf-8")
        data = yaml.safe_load(updated)
        target = data["environments"]["dev"]["targets"][0]
        assert target["node_exporter"] is False  # unchanged


# ── TestHelpers ────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_summarise_existing(self, tmp_path: Path) -> None:
        inventory_path = tmp_path / "inventory.yaml"
        inventory_path.write_text(
            textwrap.dedent("""\
                environments:
                  production:
                    targets:
                      - host: 10.0.1.10
                        name: prod-web-01
                        os_family: ubuntu
                      - host: 10.0.1.11
                        name: prod-web-02
                        os_family: ubuntu
                  staging:
                    targets:
                      - host: 10.0.2.10
                        name: staging-web-01
                        os_family: ubuntu
            """),
            encoding="utf-8",
        )
        summary = _summarise_existing(inventory_path)
        assert "production (2 VMs)" in summary
        assert "staging (1 VM)" in summary

    def test_count_vms(self, tmp_path: Path) -> None:
        inventory_path = tmp_path / "inventory.yaml"
        inventory_path.write_text(
            textwrap.dedent("""\
                environments:
                  production:
                    targets:
                      - host: 10.0.1.10
                        name: a
                        os_family: ubuntu
                      - host: 10.0.1.11
                        name: b
                        os_family: ubuntu
                  dev:
                    targets:
                      - host: 10.0.2.10
                        name: c
                        os_family: ubuntu
            """),
            encoding="utf-8",
        )
        assert _count_vms(inventory_path) == 3

    def test_count_vms_empty_file(self, tmp_path: Path) -> None:
        inventory_path = tmp_path / "inventory.yaml"
        inventory_path.write_text("environments: {}\n", encoding="utf-8")
        assert _count_vms(inventory_path) == 0
