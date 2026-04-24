"""Tests for SecretsManager — encrypt/decrypt, round-trip, error cases."""

from __future__ import annotations

import os

import pytest

from errander.integrations.secrets import (
    DecryptionError,
    MasterKeyMissingError,
    SecretsManager,
    get_secret,
)


@pytest.fixture
def key() -> str:
    return SecretsManager.generate_key()


@pytest.fixture
def sm(key: str) -> SecretsManager:
    return SecretsManager(master_key=key)


class TestGetSecret:
    """Backward-compatible tests for get_secret helper."""

    def test_get_secret_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SECRET", "value123")
        assert get_secret("TEST_SECRET") == "value123"

    def test_get_secret_default(self) -> None:
        assert get_secret("NONEXISTENT_SECRET", default="fallback") == "fallback"

    def test_get_secret_missing_raises(self) -> None:
        with pytest.raises(ValueError, match="Required secret not set"):
            get_secret("DEFINITELY_NOT_SET_12345")


class TestGenerateKey:
    def test_produces_valid_fernet_key(self, key: str) -> None:
        sm = SecretsManager(master_key=key)
        assert sm._fernet is not None

    def test_key_is_string(self, key: str) -> None:
        assert isinstance(key, str)
        assert len(key) == 44  # Fernet keys are 44-char url-safe base64


class TestEncryptDecrypt:
    def test_round_trip(self, sm: SecretsManager) -> None:
        plaintext = "super-secret-value"
        assert sm.decrypt(sm.encrypt(plaintext)) == plaintext

    def test_encrypted_output_has_prefix(self, sm: SecretsManager) -> None:
        result = sm.encrypt("test")
        assert result.startswith("enc:v1:")

    def test_encrypt_produces_different_tokens_each_time(self, sm: SecretsManager) -> None:
        a = sm.encrypt("value")
        b = sm.encrypt("value")
        assert a != b

    def test_decrypt_rejects_non_prefix(self, sm: SecretsManager) -> None:
        with pytest.raises(ValueError):
            sm.decrypt("plaintext-without-prefix")

    def test_corrupted_ciphertext_raises_decryption_error(self, sm: SecretsManager) -> None:
        with pytest.raises(DecryptionError):
            sm.decrypt("enc:v1:notvalidbase64==")


class TestDecryptIfNeeded:
    def test_plaintext_passes_through_unchanged(self) -> None:
        sm = SecretsManager(require_key=False)
        assert sm.decrypt_if_needed("plaintext") == "plaintext"

    def test_encrypted_value_decrypts_correctly(self, sm: SecretsManager, key: str) -> None:
        encrypted = sm.encrypt("secret")
        sm2 = SecretsManager(master_key=key)
        assert sm2.decrypt_if_needed(encrypted) == "secret"

    def test_plaintext_does_not_require_key(self) -> None:
        env_backup = os.environ.pop("ERRANDER_SECRETS_KEY", None)
        try:
            sm = SecretsManager(require_key=False)
            assert sm.decrypt_if_needed("plaintext-value") == "plaintext-value"
        finally:
            if env_backup is not None:
                os.environ["ERRANDER_SECRETS_KEY"] = env_backup


class TestMissingKey:
    def test_encrypt_raises_when_key_missing(self) -> None:
        env_backup = os.environ.pop("ERRANDER_SECRETS_KEY", None)
        try:
            sm = SecretsManager(require_key=True)
            with pytest.raises(MasterKeyMissingError):
                sm.encrypt("value")
        finally:
            if env_backup is not None:
                os.environ["ERRANDER_SECRETS_KEY"] = env_backup

    def test_decrypt_if_needed_plaintext_no_key_required(self) -> None:
        env_backup = os.environ.pop("ERRANDER_SECRETS_KEY", None)
        try:
            sm = SecretsManager(require_key=False)
            result = sm.decrypt_if_needed("plain")
            assert result == "plain"
        finally:
            if env_backup is not None:
                os.environ["ERRANDER_SECRETS_KEY"] = env_backup

    def test_decrypt_encrypted_raises_when_key_missing(self) -> None:
        env_backup = os.environ.pop("ERRANDER_SECRETS_KEY", None)
        try:
            sm = SecretsManager(require_key=False)
            with pytest.raises(MasterKeyMissingError):
                sm.decrypt_if_needed("enc:v1:sometoken")
        finally:
            if env_backup is not None:
                os.environ["ERRANDER_SECRETS_KEY"] = env_backup


class TestIsEncrypted:
    def test_detects_encrypted_prefix(self) -> None:
        sm = SecretsManager(require_key=False)
        assert sm.is_encrypted("enc:v1:abc") is True

    def test_returns_false_for_plaintext(self) -> None:
        sm = SecretsManager(require_key=False)
        assert sm.is_encrypted("plaintext") is False
