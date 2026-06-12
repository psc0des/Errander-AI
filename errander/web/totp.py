"""TOTP (RFC 6238) helpers for admin-group MFA in public mode (nginx Mode 2).

Thin wrapper around :mod:`pyotp`. Secrets are generated server-side, shown to
the operator once as a QR code during setup, and stored in
``users.totp_secret`` (migration #15). Verification allows a one-step clock
drift window (±30s) to tolerate minor clock skew between operator device and
server.
"""

from __future__ import annotations

import pyotp

_ISSUER = "Errander-AI"

#: Number of additional 30s windows (before/after current) accepted as valid.
_VALID_WINDOW = 1


def generate_secret() -> str:
    """Generate a new random base32 TOTP secret."""
    return pyotp.random_base32()


def make_qr_uri(username: str, secret: str) -> str:
    """Build the otpauth:// URI for a QR code (scanned by an authenticator app)."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=_ISSUER)


def verify_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code against secret, tolerating minor clock drift."""
    return pyotp.TOTP(secret).verify(code, valid_window=_VALID_WINDOW)
