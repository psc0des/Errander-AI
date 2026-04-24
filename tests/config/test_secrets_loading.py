"""Tests for secrets decryption in YAML and env var loading."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from errander.integrations.secrets import MasterKeyMissingError, SecretsManager


@pytest.fixture
def key() -> str:
    return SecretsManager.generate_key()


@pytest.fixture
def sm(key: str) -> SecretsManager:
    return SecretsManager(master_key=key)


class TestYamlDecryption:
    def test_encrypted_yaml_value_decrypts(self, tmp_path: Path, sm: SecretsManager, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SECRETS_KEY", key)
        encrypted_token = sm.encrypt("xoxb-real-token")
        yaml_content = textwrap.dedent(f"""
            environments:
              dev:
                ssh_user: errander
                ssh_key_path: ~/.ssh/errander
                approval_policy: relaxed
                targets:
                  - host: 10.0.1.10
                    name: dev-web-01
                    os_family: ubuntu
        """)
        inv_file = tmp_path / "inventory.yaml"
        inv_file.write_text(yaml_content)

        from errander.config.schema import validate_inventory
        inv = validate_inventory(inv_file)
        assert inv.environments["dev"].targets[0].host == "10.0.1.10"

    def test_encrypted_settings_yaml_decrypts(self, tmp_path: Path, sm: SecretsManager, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SECRETS_KEY", key)
        encrypted_model = sm.encrypt("gpt-4o-mini")
        yaml_content = textwrap.dedent(f"""\
            llm:
              model: "{encrypted_model}"
              temperature: 0.1
              timeout_seconds: 30
              max_retries: 2
        """)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(yaml_content)

        from errander.config.schema import validate_settings
        cfg = validate_settings(settings_file)
        assert cfg.llm.model == "gpt-4o-mini"

    def test_encrypted_value_without_key_raises(self, tmp_path: Path, sm: SecretsManager, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ERRANDER_SECRETS_KEY", raising=False)
        encrypted = sm.encrypt("secret")
        yaml_content = textwrap.dedent(f"""\
            llm:
              model: "{encrypted}"
              temperature: 0.1
              timeout_seconds: 30
              max_retries: 2
        """)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(yaml_content)

        from errander.config.schema import validate_settings
        with pytest.raises(MasterKeyMissingError):
            validate_settings(settings_file)

    def test_plaintext_yaml_passes_through_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ERRANDER_SECRETS_KEY", raising=False)
        yaml_content = textwrap.dedent("""\
            llm:
              model: "Qwen/Qwen3-8B-AWQ"
              temperature: 0.1
              timeout_seconds: 30
              max_retries: 2
        """)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(yaml_content)

        from errander.config.schema import validate_settings
        cfg = validate_settings(settings_file)
        assert cfg.llm.model == "Qwen/Qwen3-8B-AWQ"


class TestEnvVarDecryption:
    def test_encrypted_env_var_decrypts(self, sm: SecretsManager, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SECRETS_KEY", key)
        encrypted = sm.encrypt("sk-realkey12345678901234567890")
        monkeypatch.setenv("ERRANDER_LLM_API_KEY", encrypted)

        from errander.config.settings import load_settings
        settings = load_settings()
        assert settings.llm_api_key == "sk-realkey12345678901234567890"

    def test_plaintext_env_var_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_LLM_API_KEY", "sk-plaintext-key-1234567890")
        monkeypatch.delenv("ERRANDER_SECRETS_KEY", raising=False)

        from errander.config.settings import load_settings
        settings = load_settings()
        assert settings.llm_api_key == "sk-plaintext-key-1234567890"
