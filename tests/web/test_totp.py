"""Tests for errander.web.totp — TOTP helpers for admin MFA (public mode)."""

from __future__ import annotations

import pyotp

from errander.web.totp import generate_secret, make_qr_uri, verify_code


class TestGenerateSecret:
    def test_returns_base32_string(self) -> None:
        secret = generate_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16

    def test_secrets_are_random(self) -> None:
        assert generate_secret() != generate_secret()


class TestMakeQrUri:
    def test_uri_contains_secret_and_username(self) -> None:
        secret = generate_secret()
        uri = make_qr_uri("alice", secret)
        assert uri.startswith("otpauth://totp/")
        assert secret in uri
        assert "alice" in uri

    def test_uri_contains_issuer(self) -> None:
        secret = generate_secret()
        uri = make_qr_uri("alice", secret)
        assert "Errander-AI" in uri


class TestVerifyCode:
    def test_current_code_is_valid(self) -> None:
        secret = generate_secret()
        code = pyotp.TOTP(secret).now()
        assert verify_code(secret, code) is True

    def test_wrong_code_is_rejected(self) -> None:
        secret = generate_secret()
        totp = pyotp.TOTP(secret)
        wrong = "000000" if totp.now() != "000000" else "111111"
        assert verify_code(secret, wrong) is False

    def test_code_for_different_secret_is_rejected(self) -> None:
        secret_a = generate_secret()
        secret_b = generate_secret()
        code_b = pyotp.TOTP(secret_b).now()
        assert verify_code(secret_a, code_b) is False
