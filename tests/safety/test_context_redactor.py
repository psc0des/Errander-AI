"""Tests for ContextRedactor — Phase 3 prompt redaction."""

from __future__ import annotations

import pytest

from errander.safety.context_redactor import ContextRedactor


class TestContextRedactor:
    def setup_method(self) -> None:
        self.r = ContextRedactor()

    # -----------------------------------------------------------------------
    # Secret patterns that MUST be redacted
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("key", [
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "sk-1234567890abcdefghij1234567890",
    ])
    def test_openai_key_redacted(self, key: str) -> None:
        text, count = self.r.redact(f"api_key={key}")
        assert "[REDACTED:api_key]" in text
        assert key not in text
        assert count == 1

    def test_aws_key_redacted(self) -> None:
        key = "AKIA1234567890ABCDEF"
        text, count = self.r.redact(f"aws_access_key_id={key}")
        assert "[REDACTED:aws_key]" in text
        assert key not in text
        assert count == 1

    @pytest.mark.parametrize("raw", [
        "password=hunter2",
        "password: supersecret",
        "PASSWORD=UPPERCASE",
        "Password=MixedCase",
    ])
    def test_password_redacted(self, raw: str) -> None:
        text, count = self.r.redact(raw)
        assert "[REDACTED:password]" in text
        assert count >= 1

    def test_bearer_token_redacted(self) -> None:
        raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.sometoken"
        text, count = self.r.redact(raw)
        assert "[REDACTED:token]" in text
        assert "eyJhbGciOiJIUzI1NiJ9" not in text
        assert count == 1

    def test_pem_rsa_key_redacted(self) -> None:
        raw = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...\n-----END RSA PRIVATE KEY-----"
        text, count = self.r.redact(raw)
        assert "[REDACTED:pem_key]" in text
        assert "MIIEowIBAAK" not in text
        assert count == 1

    def test_pem_openssh_key_redacted(self) -> None:
        raw = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC...\n-----END OPENSSH PRIVATE KEY-----"
        text, count = self.r.redact(raw)
        assert "[REDACTED:pem_key]" in text
        assert count == 1

    def test_pem_pkcs8_key_redacted(self) -> None:
        raw = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBg...\n-----END PRIVATE KEY-----"
        text, count = self.r.redact(raw)
        assert "[REDACTED:pem_key]" in text
        assert count == 1

    # -----------------------------------------------------------------------
    # Clean text — must NOT be modified
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("clean", [
        "disk usage on / is 75%",
        "action_type: patching",
        "last failure: dpkg lock held by another process",
        "VMs surveyed: 5",
        "Environment: production",
    ])
    def test_clean_text_not_modified(self, clean: str) -> None:
        text, count = self.r.redact(clean)
        assert text == clean
        assert count == 0

    # -----------------------------------------------------------------------
    # IP redaction — opt-in only
    # -----------------------------------------------------------------------

    def test_ip_not_redacted_by_default(self) -> None:
        raw = "Connect to 192.168.1.100 for SSH"
        text, count = self.r.redact(raw)
        assert "192.168.1.100" in text
        assert count == 0

    def test_ip_redacted_when_flag_set(self) -> None:
        r = ContextRedactor(redact_ips=True)
        raw = "Connect to 192.168.1.100 for SSH"
        text, count = r.redact(raw)
        assert "192.168.1.100" not in text
        assert "[REDACTED:ip]" in text
        assert count == 1

    def test_multiple_ips_redacted(self) -> None:
        r = ContextRedactor(redact_ips=True)
        raw = "Hosts: 10.0.0.1, 10.0.0.2, 10.0.0.3"
        text, count = r.redact(raw)
        assert count == 3
        assert "10.0.0" not in text

    # -----------------------------------------------------------------------
    # Multiple secrets in one string
    # -----------------------------------------------------------------------

    def test_multiple_secrets_all_redacted(self) -> None:
        raw = (
            "Using sk-abcdefghijklmnopqrstuvwxyz123456 and "
            "password=mysecret with AKIA1234567890ABCDEF"
        )
        text, count = self.r.redact(raw)
        assert count == 3
        assert "sk-" not in text
        assert "mysecret" not in text
        assert "AKIA" not in text

    # -----------------------------------------------------------------------
    # redact_prompt() wraps redact() and returns RedactionStats
    # -----------------------------------------------------------------------

    def test_redact_prompt_stats_zero_on_clean(self) -> None:
        clean = "No secrets here."
        _, stats = self.r.redact_prompt(clean)
        assert stats.total_redactions == 0

    def test_redact_prompt_stats_nonzero_on_secret(self) -> None:
        raw = "key=sk-abcdefghijklmnopqrstuvwxyz123456"
        _, stats = self.r.redact_prompt(raw)
        assert stats.total_redactions == 1

    # -----------------------------------------------------------------------
    # Short API key strings must NOT be redacted (false positive guard)
    # -----------------------------------------------------------------------

    def test_short_sk_prefix_not_redacted(self) -> None:
        # sk-abc is only 7 chars total — should not match (< 20 char suffix)
        raw = "token: sk-abc"
        text, count = self.r.redact(raw)
        assert text == raw
        assert count == 0
