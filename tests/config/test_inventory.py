"""Tests for inventory loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from errander.config.inventory import (
    load_inventory,
    validate_ssh_keys,
    validate_target,
)
from errander.models.vm import OSFamily, VMTarget


class TestLoadInventory:
    """Tests for inventory YAML loading with environment inheritance."""

    def _write_inventory(self, tmp_path: Path, data: dict) -> Path:  # noqa: ANN401
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(yaml.dump(data))
        return config_file

    def test_basic_load(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "dev": {
                    "approval_policy": "relaxed",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert len(targets) == 1
        assert targets[0].vm_id == "dev/web-01"
        assert targets[0].hostname == "10.0.1.10"
        assert targets[0].os_family == OSFamily.UBUNTU

    def test_environment_inheritance(self, tmp_path: Path) -> None:
        """ssh_user and ssh_key_path inherit from environment."""
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "prod-user",
                    "ssh_key_path": "~/.ssh/prod_key",
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                        {"host": "10.0.1.20", "name": "db-01", "os_family": "rhel"},
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert len(targets) == 2
        # Both inherit env-level settings
        assert targets[0].ssh_user == "prod-user"
        assert targets[0].ssh_key_path == "~/.ssh/prod_key"
        assert targets[0].policy == "strict"
        assert targets[1].ssh_user == "prod-user"
        assert targets[1].policy == "strict"

    def test_host_override(self, tmp_path: Path) -> None:
        """Host-level ssh_user overrides environment-level."""
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                        {
                            "host": "10.0.1.20",
                            "name": "db-01",
                            "os_family": "rhel",
                            "ssh_user": "db-admin",
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert targets[0].ssh_user == "errander-ai"
        assert targets[1].ssh_user == "db-admin"

    def test_host_policy_override(self, tmp_path: Path) -> None:
        """Host-level policy overrides environment approval_policy."""
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {
                            "host": "10.0.1.10",
                            "name": "web-01",
                            "os_family": "ubuntu",
                            "policy": "relaxed",
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert targets[0].policy == "relaxed"

    def test_multiple_environments(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/prod",
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-prod", "os_family": "ubuntu"},
                    ],
                },
                "staging": {
                    "approval_policy": "moderate",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/stg",
                    "targets": [
                        {"host": "10.0.2.10", "name": "web-stg", "os_family": "debian"},
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert len(targets) == 2
        vm_ids = {t.vm_id for t in targets}
        assert "production/web-prod" in vm_ids
        assert "staging/web-stg" in vm_ids

    def test_tags_include_env(self, tmp_path: Path) -> None:
        """Tags include the environment name."""
        path = self._write_inventory(tmp_path, {
            "environments": {
                "dev": {
                    "approval_policy": "relaxed",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {
                            "host": "10.0.1.10",
                            "name": "web-01",
                            "os_family": "ubuntu",
                            "tags": ["web", "frontend"],
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert targets[0].tags["env"] == "dev"
        assert "web" in targets[0].tags
        assert "frontend" in targets[0].tags

    def test_empty_environments(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {"environments": {}})
        targets = load_inventory(path)
        assert targets == []

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_inventory(tmp_path / "nope.yaml")


class TestValidateTarget:
    """Tests for single-target validation."""

    def test_valid_target(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="~/.ssh/key",
            os_family=OSFamily.UBUNTU,
            policy="moderate",
        )
        errors = validate_target(target)
        assert errors == []

    def test_empty_hostname(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="  ",
            ssh_user="errander-ai",
            ssh_key_path="~/.ssh/key",
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_target(target)
        assert any("hostname" in e for e in errors)

    def test_empty_ssh_user(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="  ",
            ssh_key_path="~/.ssh/key",
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_target(target)
        assert any("ssh_user" in e for e in errors)

    def test_empty_ssh_key_path(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="  ",
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_target(target)
        assert any("ssh_key_path" in e for e in errors)

    def test_unknown_policy(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="~/.ssh/key",
            os_family=OSFamily.UBUNTU,
            policy="unknown-policy",
        )
        errors = validate_target(target)
        assert any("unknown policy" in e for e in errors)

    def test_multiple_errors(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="  ",
            ssh_user="  ",
            ssh_key_path="  ",
            os_family=OSFamily.UBUNTU,
            policy="nope",
        )
        errors = validate_target(target)
        assert len(errors) == 4


class TestValidateSSHKeys:
    """Tests for SSH key file existence validation."""

    def test_existing_keys_pass(self, tmp_path: Path) -> None:
        key_file = tmp_path / "test_key"
        key_file.write_text("fake-key")
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path=str(key_file),
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_ssh_keys([target])
        assert errors == []

    def test_missing_key_reported(self) -> None:
        target = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="/nonexistent/path/key",
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_ssh_keys([target])
        assert len(errors) == 1
        assert "SSH key not found" in errors[0]

    def test_deduplicates_key_paths(self) -> None:
        """Same key path used by multiple targets → reported once."""
        target1 = VMTarget(
            vm_id="dev/web-01",
            hostname="10.0.1.10",
            ssh_user="errander-ai",
            ssh_key_path="/nonexistent/key",
            os_family=OSFamily.UBUNTU,
        )
        target2 = VMTarget(
            vm_id="dev/web-02",
            hostname="10.0.1.11",
            ssh_user="errander-ai",
            ssh_key_path="/nonexistent/key",
            os_family=OSFamily.UBUNTU,
        )
        errors = validate_ssh_keys([target1, target2])
        assert len(errors) == 1


class TestCriticalServicesInheritance:
    """Tests for critical_services field in inventory loading."""

    def _write_inventory(self, tmp_path: Path, data: dict) -> Path:  # noqa: ANN401
        import yaml
        config_file = tmp_path / "inventory.yaml"
        config_file.write_text(yaml.dump(data))
        return config_file

    def test_critical_services_defaults_to_empty(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "dev": {
                    "approval_policy": "relaxed",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert targets[0].critical_services == ()

    def test_host_critical_services_loaded(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "dev": {
                    "approval_policy": "relaxed",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {
                            "host": "10.0.1.10",
                            "name": "web-01",
                            "os_family": "ubuntu",
                            "critical_services": ["nginx", "prometheus-node-exporter"],
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert "nginx" in targets[0].critical_services
        assert "prometheus-node-exporter" in targets[0].critical_services

    def test_env_critical_services_inherited_when_host_empty(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "critical_services": ["ssh", "prometheus-node-exporter"],
                    "targets": [
                        {"host": "10.0.1.10", "name": "web-01", "os_family": "ubuntu"},
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert "ssh" in targets[0].critical_services
        assert "prometheus-node-exporter" in targets[0].critical_services

    def test_host_critical_services_override_env(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "production": {
                    "approval_policy": "strict",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "critical_services": ["ssh"],
                    "targets": [
                        {
                            "host": "10.0.1.10",
                            "name": "web-01",
                            "os_family": "ubuntu",
                            "critical_services": ["nginx", "ssh"],
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        # Host override is used (contains nginx)
        assert "nginx" in targets[0].critical_services

    def test_critical_services_is_tuple(self, tmp_path: Path) -> None:
        path = self._write_inventory(tmp_path, {
            "environments": {
                "dev": {
                    "approval_policy": "relaxed",
                    "ssh_user": "errander-ai",
                    "ssh_key_path": "~/.ssh/key",
                    "targets": [
                        {
                            "host": "10.0.1.10",
                            "name": "web-01",
                            "os_family": "ubuntu",
                            "critical_services": ["nginx"],
                        },
                    ],
                },
            },
        })
        targets = load_inventory(path)
        assert isinstance(targets[0].critical_services, tuple)
