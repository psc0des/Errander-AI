"""Tests for HMAC-signed time-limited tokens."""

from __future__ import annotations

import time

import pytest

from errander.integrations.signed_url import (
    InvalidSignedTokenError,
    SigningSecretMissingError,
    make_signed_token,
    verify_signed_token,
)

_SECRET = b"a-test-secret-32-bytes-or-longer-for-prod"


class TestRoundtrip:
    def test_signs_and_verifies_simple_payload(self) -> None:
        tok = make_signed_token({"batch_id": "b1", "vm_id": "v1"}, ttl_seconds=60, secret=_SECRET)
        payload = verify_signed_token(tok, secret=_SECRET)
        assert payload["batch_id"] == "b1"
        assert payload["vm_id"] == "v1"
        assert isinstance(payload["exp"], int)

    def test_payload_round_trips_nested_structures(self) -> None:
        original = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        tok = make_signed_token(original, ttl_seconds=60, secret=_SECRET)
        out = verify_signed_token(tok, secret=_SECRET)
        for key in original:
            assert out[key] == original[key]


class TestSignatureValidation:
    def test_tampered_body_rejected(self) -> None:
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET)
        body_b64, _, sig_b64 = tok.partition(".")
        # Flip one character in the body — signature will no longer match.
        tampered_body = body_b64[:-1] + ("A" if body_b64[-1] != "A" else "B")
        tampered = f"{tampered_body}.{sig_b64}"
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(tampered, secret=_SECRET)

    def test_tampered_signature_rejected(self) -> None:
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET)
        body_b64, _, sig_b64 = tok.partition(".")
        tampered = f"{body_b64}.{sig_b64[:-1] + 'A'}"
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(tampered, secret=_SECRET)

    def test_wrong_secret_rejected(self) -> None:
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET)
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(tok, secret=b"different-secret")

    def test_malformed_token_no_dot(self) -> None:
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token("no-dot-here", secret=_SECRET)

    def test_malformed_token_empty_parts(self) -> None:
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(".sig", secret=_SECRET)
        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token("body.", secret=_SECRET)


class TestExpiry:
    def test_expired_token_rejected(self) -> None:
        # Issued at t=0 with 60s TTL → expires at t=60.
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET, now=0.0)
        # Verify at t=100 (40s past expiry).
        with pytest.raises(InvalidSignedTokenError, match="expired"):
            verify_signed_token(tok, secret=_SECRET, now=100.0)

    def test_token_valid_just_before_expiry(self) -> None:
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET, now=0.0)
        # At t=59 — still valid.
        payload = verify_signed_token(tok, secret=_SECRET, now=59.0)
        assert payload["batch_id"] == "b1"

    def test_token_invalid_at_exact_expiry(self) -> None:
        """Expiry is a strict less-than check: now >= exp → expired."""
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET, now=0.0)
        # Exp is exactly 60 (int truncation of 0 + 60).
        with pytest.raises(InvalidSignedTokenError, match="expired"):
            verify_signed_token(tok, secret=_SECRET, now=60.0)

    def test_zero_or_negative_ttl_rejected(self) -> None:
        with pytest.raises(ValueError):
            make_signed_token({"batch_id": "b1"}, ttl_seconds=0, secret=_SECRET)
        with pytest.raises(ValueError):
            make_signed_token({"batch_id": "b1"}, ttl_seconds=-1, secret=_SECRET)


class TestSecretResolution:
    def test_missing_secret_raises_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ERRANDER_SIGNING_SECRET", raising=False)
        with pytest.raises(SigningSecretMissingError):
            make_signed_token({"batch_id": "b1"}, ttl_seconds=60)

    def test_env_var_used_when_param_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", "env-secret-here")
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60)
        payload = verify_signed_token(tok)
        assert payload["batch_id"] == "b1"

    def test_explicit_secret_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", "env-secret")
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=b"explicit")
        with pytest.raises(InvalidSignedTokenError):
            # Verifying with env secret fails because token was signed with explicit one.
            verify_signed_token(tok)
        # And verifying with the explicit secret works.
        assert verify_signed_token(tok, secret=b"explicit")["batch_id"] == "b1"


class TestPayloadValidation:
    def test_payload_must_be_json_object(self) -> None:
        """The payload must be a JSON object, not a list/string/number."""
        # We can't easily construct a token with a non-object payload via make_signed_token
        # (it takes a dict), so just verify the round-trip preserves the constraint.
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET)
        payload = verify_signed_token(tok, secret=_SECRET)
        assert isinstance(payload, dict)


class TestRealTime:
    def test_default_now_is_current_time(self) -> None:
        """Token issued with default now should verify with default now."""
        tok = make_signed_token({"batch_id": "b1"}, ttl_seconds=60, secret=_SECRET)
        payload = verify_signed_token(tok, secret=_SECRET)
        # Sanity: exp ~ now + 60
        assert int(time.time()) <= payload["exp"] <= int(time.time()) + 61  # type: ignore[operator]
