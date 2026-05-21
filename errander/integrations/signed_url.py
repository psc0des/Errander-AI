"""Signed URL infrastructure — HMAC-signed, time-limited tokens.

Used by docker_hygiene's web approval surface (Session 2b-ii) to embed
tamper-resistant, expiring approval references in Slack messages. The web
route accepts only tokens that verify against ``ERRANDER_SIGNING_SECRET``
and have not yet expired.

Token format::

    base64url(payload_json) + "." + base64url(hmac_sha256(payload_json, secret))

The payload is a JSON object with at minimum::

    {"batch_id": "...", "vm_id": "...", "snapshot_hash": "...", "exp": <unix_ts>}

``exp`` is checked at verify time. Missing-secret raises a loud error
(never auto-generate or skip-on-missing — that would let unsigned URLs flow).
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
from hashlib import sha256

logger = logging.getLogger(__name__)


class SigningSecretMissingError(RuntimeError):
    """Raised when ERRANDER_SIGNING_SECRET is unset.

    Fail loud rather than silently disabling signing. An attacker who could
    cause this env var to be unset must not be able to bypass signature
    verification.
    """


class InvalidSignedTokenError(ValueError):
    """Raised when a token fails verification (bad signature, expired, malformed)."""


def _get_secret(secret: bytes | None) -> bytes:
    """Resolve signing secret: explicit param wins, else env var, else fail."""
    if secret is not None:
        return secret
    env_val = os.environ.get("ERRANDER_SIGNING_SECRET")
    if not env_val:
        raise SigningSecretMissingError(
            "ERRANDER_SIGNING_SECRET is not set. Set it to a 32+ byte random "
            "value (e.g. `head -c 32 /dev/urandom | base64`). Signed URLs "
            "cannot be issued or verified without it."
        )
    return env_val.encode("utf-8")


def _b64url(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """URL-safe base64 decode tolerating missing padding."""
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_signed_token(
    payload: dict[str, object],
    *,
    ttl_seconds: int,
    secret: bytes | None = None,
    now: float | None = None,
) -> str:
    """Sign a payload dict and return a token string.

    The token embeds an ``exp`` claim equal to ``now + ttl_seconds``. A
    ``now`` argument is accepted for deterministic testing.

    Raises:
        SigningSecretMissingError: When secret is None and env var is unset.
        ValueError: When ttl_seconds is non-positive.
    """
    if ttl_seconds <= 0:
        msg = f"ttl_seconds must be positive, got {ttl_seconds}"
        raise ValueError(msg)
    sec = _get_secret(secret)
    now_ts = now if now is not None else time.time()
    full_payload = dict(payload)
    full_payload["exp"] = int(now_ts + ttl_seconds)
    body = json.dumps(full_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(sec, body, sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}"


def verify_signed_token(
    token: str,
    *,
    secret: bytes | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Verify a token and return its payload.

    Performs (in order): structural check, HMAC compare (constant-time),
    expiry check. Raises InvalidSignedToken on any failure — caller MUST
    NOT log or echo the token contents on failure (the malformed body
    could contain operator-supplied junk).

    Raises:
        SigningSecretMissingError: When secret is None and env var is unset.
        InvalidSignedToken: Bad structure, signature, or expired.
    """
    sec = _get_secret(secret)
    if not isinstance(token, str) or "." not in token:
        raise InvalidSignedTokenError("malformed token")
    body_b64, _, sig_b64 = token.partition(".")
    if not body_b64 or not sig_b64:
        raise InvalidSignedTokenError("malformed token")
    try:
        body = _b64url_decode(body_b64)
        sig_received = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        raise InvalidSignedTokenError("malformed token") from None

    sig_expected = hmac.new(sec, body, sha256).digest()
    # Constant-time compare to resist timing attacks.
    if not hmac.compare_digest(sig_expected, sig_received):
        raise InvalidSignedTokenError("signature mismatch")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidSignedTokenError("payload is not valid JSON") from None
    if not isinstance(payload, dict):
        raise InvalidSignedTokenError("payload must be a JSON object")

    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise InvalidSignedTokenError("payload missing or invalid exp claim")
    now_ts = now if now is not None else time.time()
    if now_ts >= exp:
        raise InvalidSignedTokenError("token expired")

    return payload
