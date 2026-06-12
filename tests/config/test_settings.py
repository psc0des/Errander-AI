"""Tests for settings loading from env vars and YAML."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from errander.config.settings import Settings, load_settings


class TestLoadSettingsEnvVars:
    """Tests for environment variable loading."""

    def test_defaults_without_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All env vars cleared → defaults used."""
        monkeypatch.delenv("ERRANDER_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("ERRANDER_SLACK_CHANNEL_ID", raising=False)
        monkeypatch.delenv("ERRANDER_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("ERRANDER_LLM_API_KEY", raising=False)
        monkeypatch.delenv("ERRANDER_AUDIT_DB_URL", raising=False)
        monkeypatch.delenv("ERRANDER_METRICS_PORT", raising=False)
        monkeypatch.delenv("ERRANDER_DRY_RUN", raising=False)
        monkeypatch.delenv("ERRANDER_APPROVAL_TIMEOUT", raising=False)

        settings = load_settings()
        assert settings.slack_bot_token == ""
        assert settings.llm_api_key == "not-needed"
        assert settings.audit_db_url == "postgresql://errander:errander@localhost:5432/errander"
        assert settings.metrics_port == 9090
        assert settings.dry_run_default is True
        assert settings.approval_timeout_seconds == 1800

    def test_env_vars_override_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SLACK_BOT_TOKEN", "xoxb-test-token")
        monkeypatch.setenv("ERRANDER_SLACK_CHANNEL_ID", "C123456")
        monkeypatch.setenv("ERRANDER_LLM_BASE_URL", "http://10.0.0.5:8000/v1")
        monkeypatch.setenv("ERRANDER_AUDIT_DB_URL", "postgresql://u:p@db.internal:5432/errander")
        monkeypatch.setenv("ERRANDER_METRICS_PORT", "8080")
        monkeypatch.setenv("ERRANDER_DRY_RUN", "false")

        settings = load_settings()
        assert settings.slack_bot_token == "xoxb-test-token"
        assert settings.slack_channel_id == "C123456"
        assert settings.llm_base_url == "http://10.0.0.5:8000/v1"
        assert settings.audit_db_url == "postgresql://u:p@db.internal:5432/errander"
        assert settings.metrics_port == 8080
        assert settings.dry_run_default is False

    def test_invalid_int_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_METRICS_PORT", "not-a-number")
        with pytest.raises(ValueError, match="must be an integer"):
            load_settings()

    def test_invalid_bool_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_DRY_RUN", "maybe")
        with pytest.raises(ValueError, match="must be a boolean"):
            load_settings()

    def test_bool_truthy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "YES"):
            monkeypatch.setenv("ERRANDER_DRY_RUN", val)
            settings = load_settings()
            assert settings.dry_run_default is True

    def test_bool_falsy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("0", "false", "False", "FALSE", "no", "NO"):
            monkeypatch.setenv("ERRANDER_DRY_RUN", val)
            settings = load_settings()
            assert settings.dry_run_default is False


class TestLoadSettingsWithYAML:
    """Tests for YAML settings file integration."""

    def test_yaml_values_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ERRANDER_APPROVAL_TIMEOUT", raising=False)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump({
                "agent": {"approval_timeout_seconds": 900, "vm_lock_ttl_seconds": 3600},
                "llm": {"timeout_seconds": 60, "max_retries": 5},
                # accepted-but-ignored since R2 (reaction channel removed)
                "slack": {"poll_interval_seconds": 15},
            }),
        )

        settings = load_settings(settings_path=settings_file)
        assert settings.approval_timeout_seconds == 900
        assert settings.vm_lock_ttl_seconds == 3600
        assert settings.llm_timeout_seconds == 60
        assert settings.llm_max_retries == 5

    def test_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var takes precedence over YAML for approval_timeout."""
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump({"agent": {"approval_timeout_seconds": 900}}),
        )
        monkeypatch.setenv("ERRANDER_APPROVAL_TIMEOUT", "600")

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

    def test_rolling_update_percentage_default(self) -> None:
        assert Settings().rolling_update_percentage == 100

    def test_canary_enabled_default(self) -> None:
        assert Settings().canary_enabled is False

    def test_drift_detection_enabled_default(self) -> None:
        assert Settings().drift_detection_enabled is False


class TestRollingAndDriftSettings:
    """Tests for rolling update, canary, and drift detection settings."""

    def test_load_settings_with_rolling_config(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            yaml.dump({
                "agent": {
                    "rolling_update_percentage": 25,
                    "wave_failure_threshold": 0.3,
                    "health_check_command": "uptime",
                    "canary_enabled": True,
                    "canary_health_check_command": "systemctl is-system-running",
                    "drift_detection_enabled": True,
                    "drift_abort_on_detection": True,
                },
            }),
        )
        settings = load_settings(settings_path=settings_file)
        assert settings.rolling_update_percentage == 25
        assert settings.wave_failure_threshold == 0.3
        assert settings.health_check_command == "uptime"
        assert settings.canary_enabled is True
        assert settings.canary_health_check_command == "systemctl is-system-running"
        assert settings.drift_detection_enabled is True
        assert settings.drift_abort_on_detection is True

    def test_load_settings_env_override_canary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ERRANDER_CANARY_ENABLED", "true")
        monkeypatch.setenv("ERRANDER_DRIFT_DETECTION", "true")
        monkeypatch.setenv("ERRANDER_ROLLING_UPDATE_PCT", "50")
        settings = load_settings()
        assert settings.canary_enabled is True
        assert settings.drift_detection_enabled is True
        assert settings.rolling_update_percentage == 50
