"""Secrets interface — encrypt/decrypt sensitive values at rest.

SecretsManager uses Fernet (AES-128-CBC + HMAC-SHA256) to encrypt secrets.
Encrypted values are stored as "enc:v1:<base64-fernet-token>". The v1 prefix
supports future algorithm rotation without breaking existing ciphertexts.

The master key comes from ERRANDER_SECRETS_KEY (32-byte url-safe base64).
Generate with: uv run python -m errander --generate-secrets-key

v1: Env vars + optional Fernet encryption.
v2: HashiCorp Vault with env var fallback.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class MasterKeyMissingError(ValueError):
    """Raised when encryption/decryption requires a key that is not set."""


class DecryptionError(ValueError):
    """Raised when a ciphertext cannot be decrypted (corrupted or wrong key)."""


class SecretsManager:
    """Encrypt/decrypt sensitive values using Fernet (AES-128-CBC + HMAC-SHA256).

    Values are stored as "enc:v1:<base64-fernet-token>". The v1 prefix allows
    future algorithm rotation without breaking existing ciphertexts.

    The master key comes from ERRANDER_SECRETS_KEY (32-byte url-safe base64).
    Missing key raises MasterKeyMissingError on first encrypt/decrypt — callers
    that don't need secrets can instantiate with require_key=False.
    """

    PREFIX = "enc:v1:"

    def __init__(
        self,
        master_key: str | None = None,
        require_key: bool = True,
    ) -> None:
        """Initialise SecretsManager.

        Args:
            master_key: Fernet key (url-safe base64). If None, reads from
                ERRANDER_SECRETS_KEY env var.
            require_key: If True, raise MasterKeyMissingError when key is absent
                and encrypt/decrypt is called. If False, silently skip (useful
                for callers that only handle plaintext values).
        """
        raw_key = master_key or os.environ.get("ERRANDER_SECRETS_KEY")
        self._fernet: Fernet | None = None
        self._require_key = require_key

        if raw_key:
            try:
                self._fernet = Fernet(raw_key.encode())
            except (ValueError, Exception) as exc:
                msg = f"ERRANDER_SECRETS_KEY is not a valid Fernet key: {exc}"
                raise MasterKeyMissingError(msg) from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext and return an enc:v1: prefixed ciphertext.

        Args:
            plaintext: The value to encrypt.

        Returns:
            String of the form "enc:v1:<base64-fernet-token>".

        Raises:
            MasterKeyMissingError: If ERRANDER_SECRETS_KEY is not set.
        """
        self._require_fernet()
        assert self._fernet is not None  # satisfies type checker
        token = self._fernet.encrypt(plaintext.encode()).decode()
        return f"{self.PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt an enc:v1: prefixed ciphertext.

        Args:
            ciphertext: String of the form "enc:v1:<base64-fernet-token>".

        Returns:
            Decrypted plaintext string.

        Raises:
            MasterKeyMissingError: If ERRANDER_SECRETS_KEY is not set.
            DecryptionError: If the ciphertext is corrupted or the key is wrong.
            ValueError: If the value does not start with "enc:v1:".
        """
        if not ciphertext.startswith(self.PREFIX):
            msg = f"Value does not start with '{self.PREFIX}'"
            raise ValueError(msg)
        self._require_fernet()
        assert self._fernet is not None
        token = ciphertext[len(self.PREFIX):]
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            msg = (
                "Decryption failed — ERRANDER_SECRETS_KEY does not match the key "
                "used to encrypt this value. If you re-ran configure.sh and it "
                "generated a new key, re-run configure.sh again and re-enter the "
                "affected secret (e.g. UI password) so it is re-encrypted with the "
                "current key."
            )
            raise DecryptionError(msg) from exc

    def is_encrypted(self, value: str) -> bool:
        """Return True if value looks like an enc:v1: ciphertext."""
        return value.startswith(self.PREFIX)

    def decrypt_if_needed(self, value: str) -> str:
        """Decrypt value if it starts with enc:v1:, otherwise pass through.

        Callers that don't know whether a value is encrypted can use this safely.
        Plaintext values are returned unchanged — ERRANDER_SECRETS_KEY is not
        required for them.

        Args:
            value: Plaintext or "enc:v1:<ciphertext>".

        Returns:
            Decrypted string, or original value if not encrypted.

        Raises:
            MasterKeyMissingError: If value is encrypted but no key is set.
            DecryptionError: If the ciphertext is corrupted or key is wrong.
        """
        if not self.is_encrypted(value):
            return value
        return self.decrypt(value)

    @staticmethod
    def generate_key() -> str:
        """Generate a new random Fernet key.

        Returns:
            URL-safe base64 string suitable for ERRANDER_SECRETS_KEY.
        """
        return Fernet.generate_key().decode()

    def _require_fernet(self) -> None:
        if self._fernet is None:
            msg = (
                "ERRANDER_SECRETS_KEY is not set. "
                "Generate one with: uv run python -m errander --generate-secrets-key"
            )
            raise MasterKeyMissingError(msg)


def get_secret(name: str, default: str | None = None) -> str:
    """Retrieve and optionally decrypt a secret from an environment variable.

    Supports enc:v1: encrypted values transparently.

    Args:
        name: Environment variable name (e.g., ERRANDER_SLACK_BOT_TOKEN).
        default: Default value if not set.

    Returns:
        Secret value (decrypted if needed).

    Raises:
        ValueError: If secret is not set and no default provided.
        MasterKeyMissingError: If value is encrypted but ERRANDER_SECRETS_KEY is missing.
        DecryptionError: If the ciphertext is corrupted.
    """
    value = os.environ.get(name, default)
    if value is None:
        msg = f"Required secret not set: {name}"
        raise ValueError(msg)
    return SecretsManager(require_key=False).decrypt_if_needed(value)
