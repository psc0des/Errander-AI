"""Tests for settings loading from env vars and YAML."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from automaint.config.settings import Settings, load_settings


class TestLoadSettingsEnvVars:
    """Tests for environment variable loading."""

    def test_defaults_without_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All env vars cleared → defaults used."""
        monkeypatch.delenv("AUTOMAINT_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("AUTOMAINT_SLACK_CHANNEL_ID", raising=False)
        monkeypatch.delenv("AUTOMAINT_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("AUTOMAINT_LLM_API_KEY", raising=False)
        monkeypatch.delenv("AUTOMAINT_AUDIT_DB_URL", raising=False)
        monkeypatch.delenv("AUTOMAINT_METRICS_PORT", raising=False)
        monkeypatch.delenv("AUTOMAINT_DRY_RUN", raising=False)
        monkeypatch.delenv("AUTOMAINT_APPROVAL_TIMEOUT", raising=False)

        settings = load_settings()
        assert settings.slack_bot_token == ""
        assert settings.llm_api_key == "not-needed"
        assert settings.audit_db_url == "automaint.sqlite"
        assert settings.metrics_port == 9090
        assert settings.dry_run_default is True
        assert settings.approval_timeout_seconds == 1800

    def test_env_vars_override_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOMAINT_SLACK_BOT_TOKEN", "xoxb-test-token")
        monkeypatch.setenv("AUTOMAINT_SLACK_CHANNEL_ID", "C123456")
        monkeypatch.setenv("AUTOMAINT_LLM_BASE_URL", "http://10.0.0.5:8000/v1")
        monkeypatch.setenv("AUTOMAINT_AUDIT_DB_URL", "/var/lib/automaint/audit.sqlite")
        monkeypatch.setenv("AUTOMAINT_METRICS_PORT", "8080")
        monkeypatch.setenv("AUTOMAINT_DRY_RUN", "false")

        settings = load_settings()
        assert settings.slack_bot_token == "xoxb-test-token"
        assert settings.slack_channel_id == "C123456"
        assert settings.llm_base_url == "http://10.0.0.5:8000/v1"
        assert settings.audit_db_url == "/var/lib/automaint/audit.sqlite"
        assert settings.metrics_port == 8080
        assert settings.dry_run_default is False

    def test_invalid_int_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOMAINT_METRICS_PORT", "not-a-number")
        with pytest.raises(ValueError, match="must be an integer"):
            load_settings()

    def test_invalid_bool_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOMAINT_DRY_RUN", "maybe")
        with pytest.raises(ValueError, match="must be a boolean"):
            load_settings()

    def test_bool_truthy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "YES"):
            monkeypatch.setenv("AUTOMAINT_DRY_RUN", val)
            settings = load_settings()
            assert settings.dry_run_default is True

    def test_bool_falsy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("0", "false", "False", "FALSE", "no", "NO"):
            monkeypatch.setenv("AUTOMAINT_DRY_RUN", val)
            settings = load_settings()
            assert settings.dry_run_default is False


class TestLoadSettingsWithYAML:
    """Tests for YAML settings file integration."""

    def test_yaml_values_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOMAINT_APPROVAL_TIMEOUT", raising=False)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump({
                "agent": {"approval_timeout_seconds": 900, "vm_lock_ttl_seconds": 3600},
                "llm": {"timeout_seconds": 60, "max_retries": 5},
                "slack": {"poll_interval_seconds": 15},
            }),
        )

        settings = load_settings(settings_path=settings_file)
        assert settings.approval_timeout_seconds == 900
        assert settings.vm_lock_ttl_seconds == 3600
        assert settings.llm_timeout_seconds == 60
        assert settings.llm_max_retries == 5
        assert settings.approval_poll_interval_seconds == 15

    def test_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var takes precedence over YAML for approval_timeout."""
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump({"agent": {"approval_timeout_seconds": 900}}),
        )
        monkeypatch.setenv("AUTOMAINT_APPROVAL_TIMEOUT", "600")

        settings = load_settings(settings_path=settings_file)
        assert settings.approval_timeout_seconds == 600

    def test_nonexistent_yaml_uses_defaults(self, tmp_path: Path) -> None:
        settings = load_settings(settings_path=tmp_path / "nope.yaml")
        assert settings.approval_timeout_seconds == 1800

    def test_none_path_uses_defaults(self) -> None:
        settings = load_settings(settings_path=None)
        assert settings.approval_timeout_seconds == 1800


class TestSettingsDataclass:
    """Tests for Settings dataclass itself."""

    def test_default_construction(self) -> None:
        s = Settings()
        assert s.dry_run_default is True
        assert s.ssh_reconnect_backoff == [5, 15, 45]

    def test_custom_construction(self) -> None:
        s = Settings(
            slack_bot_token="xoxb-test",
            metrics_port=8080,
            dry_run_default=False,
        )
        assert s.slack_bot_token == "xoxb-test"
        assert s.metrics_port == 8080
        assert s.dry_run_default is False
