"""Tests for settings loader precedence: env > DB > YAML > defaults."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from errander.config.settings import load_settings


def _yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "settings.yaml"
    p.write_text(content)
    return p


class TestDefaultValues:
    def test_defaults_with_no_config(self):
        s = load_settings()
        assert s.llm_temperature == 0.1
        assert s.llm_timeout_seconds == 30
        assert s.dry_run_default is True
        assert s.approval_timeout_seconds == 1800

    def test_source_tracked_as_default(self):
        s = load_settings()
        assert s.sources.get("llm_temperature") == "default"
        assert s.sources.get("llm_timeout_seconds") == "default"


class TestYAMLValues:
    def test_yaml_model_loaded(self, tmp_path):
        yaml = _yaml(tmp_path, """
llm:
  model: qwen3-8b-awq
  temperature: 0.2
""")
        s = load_settings(settings_path=yaml)
        assert s.llm_model == "qwen3-8b-awq"
        assert s.llm_temperature == 0.2

    def test_yaml_agent_block(self, tmp_path):
        yaml = _yaml(tmp_path, """
agent:
  approval_timeout_seconds: 600
  vm_lock_ttl_seconds: 3600
""")
        s = load_settings(settings_path=yaml)
        assert s.approval_timeout_seconds == 600
        assert s.vm_lock_ttl_seconds == 3600

    def test_yaml_source_tracked(self, tmp_path):
        yaml = _yaml(tmp_path, """
llm:
  model: qwen3
  temperature: 0.5
""")
        s = load_settings(settings_path=yaml)
        assert s.sources.get("llm_model") == "yaml"
        assert s.sources.get("llm_temperature") == "yaml"

    def test_nonexistent_yaml_uses_defaults(self, tmp_path):
        s = load_settings(settings_path=tmp_path / "missing.yaml")
        assert s.llm_temperature == 0.1


class TestEnvVarOverrides:
    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("ERRANDER_LLM_TEMPERATURE", "0.7")
        s = load_settings()
        assert s.llm_model == "gpt-4o"
        assert s.llm_temperature == pytest.approx(0.7)

    def test_env_overrides_yaml(self, monkeypatch, tmp_path):
        yaml = _yaml(tmp_path, "llm:\n  model: yaml-model\n")
        monkeypatch.setenv("ERRANDER_LLM_MODEL", "env-model")
        s = load_settings(settings_path=yaml)
        assert s.llm_model == "env-model"

    def test_env_source_tracked(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_LLM_MODEL", "env-model")
        s = load_settings()
        assert s.sources.get("llm_model") == "env"

    def test_invalid_int_raises(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_LLM_TIMEOUT", "notanint")
        with pytest.raises(ValueError, match="must be an integer"):
            load_settings()

    def test_invalid_float_raises(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_LLM_TEMPERATURE", "hot")
        with pytest.raises(ValueError, match="must be a float"):
            load_settings()

    def test_bool_truthy_values(self, monkeypatch):
        for val in ("1", "true", "yes", "True", "YES"):
            monkeypatch.setenv("ERRANDER_DRY_RUN", val)
            s = load_settings()
            assert s.dry_run_default is True

    def test_bool_falsy_values(self, monkeypatch):
        for val in ("0", "false", "no", "False", "NO"):
            monkeypatch.setenv("ERRANDER_DRY_RUN", val)
            s = load_settings()
            assert s.dry_run_default is False

    def test_invalid_bool_raises(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_DRY_RUN", "maybe")
        with pytest.raises(ValueError, match="must be a boolean"):
            load_settings()


class TestDBOverrides:
    def test_db_overrides_yaml(self, tmp_path):
        yaml = _yaml(tmp_path, "llm:\n  model: yaml-model\n")
        db = {"ERRANDER_LLM_MODEL": "db-model"}
        s = load_settings(settings_path=yaml, db_overrides=db)
        assert s.llm_model == "db-model"

    def test_db_source_tracked(self, tmp_path):
        yaml = _yaml(tmp_path, "llm:\n  model: yaml-model\n")
        db = {"ERRANDER_LLM_MODEL": "db-model"}
        s = load_settings(settings_path=yaml, db_overrides=db)
        assert s.sources.get("llm_model") == "db"

    def test_env_beats_db(self, monkeypatch, tmp_path):
        yaml = _yaml(tmp_path, "llm:\n  model: yaml-model\n")
        monkeypatch.setenv("ERRANDER_LLM_MODEL", "env-model")
        db = {"ERRANDER_LLM_MODEL": "db-model"}
        s = load_settings(settings_path=yaml, db_overrides=db)
        assert s.llm_model == "env-model"
        assert s.sources.get("llm_model") == "env"

    def test_db_int_field(self):
        db = {"ERRANDER_LLM_TIMEOUT": "120"}
        s = load_settings(db_overrides=db)
        assert s.llm_timeout_seconds == 120

    def test_db_int_invalid_falls_back_to_default(self):
        db = {"ERRANDER_LLM_TIMEOUT": "notanint"}
        s = load_settings(db_overrides=db)
        assert s.llm_timeout_seconds == 30  # falls back to default

    def test_db_bool_field(self):
        db = {"ERRANDER_DRY_RUN": "false"}
        s = load_settings(db_overrides=db)
        assert s.dry_run_default is False


class TestUICredentials:
    def test_ui_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("ERRANDER_UI_USER", "admin")
        monkeypatch.setenv("ERRANDER_UI_PASSWORD", "secret")
        s = load_settings()
        assert s.ui_user == "admin"
        assert s.ui_password == "secret"

    def test_ui_credentials_default_empty(self):
        s = load_settings()
        assert s.ui_user == ""
        assert s.ui_password == ""
