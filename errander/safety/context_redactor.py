"""LLM prompt redaction — strips known secret patterns before prompt assembly (Phase 3).

Runs on the fully-rendered prompt string immediately before it is sent to the LLM.
Never modifies the original context objects — operates on the final string.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# (compiled_pattern, replacement) — ordered from most to least specific
_SECRET_RULES: list[tuple[re.Pattern[str], str]] = [
    # OpenAI / Anthropic / generic sk- API keys (20+ chars to avoid false positives)
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}", re.ASCII), "[REDACTED:api_key]"),
    # AWS access key IDs (always start AKIA, 16 uppercase-alphanumeric after)
    (re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII), "[REDACTED:aws_key]"),
    # password= or password: assignments (case-insensitive, no whitespace in value)
    (re.compile(r"(?i)password\s*[:=]\s*\S+"), "[REDACTED:password]"),
    # Authorization: Bearer token header (case-insensitive header name)
    (re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"), "Authorization: Bearer [REDACTED:token]"),
    # PEM private key blocks — any key type (RSA, EC, DSA, OPENSSH, or bare PKCS#8)
    (
        re.compile(
            r"-----BEGIN (?:\w+ )*PRIVATE KEY-----[\s\S]*?-----END (?:\w+ )*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED:pem_key]",
    ),
]

# IPv4 address pattern — used only when redact_ips=True
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass
class RedactionStats:
    """Summary of substitutions made during a single prompt redaction pass."""

    total_redactions: int = 0


class ContextRedactor:
    """Strips known secret patterns from strings before they reach the LLM.

    Usage::

        redactor = ContextRedactor()
        clean_prompt, stats = redactor.redact_prompt(raw_prompt)
        if stats.total_redactions:
            logger.warning("Redacted %d secret(s) from prompt", stats.total_redactions)

    IP redaction is opt-in because operator infrastructure data (hostnames, metrics
    endpoints) legitimately contains private IPs that should appear in the prompt.
    Enable via ``redact_ips=True`` only when the deployment context warrants it.
    """

    def __init__(self, *, redact_ips: bool = False) -> None:
        self._redact_ips = redact_ips

    def redact(self, text: str) -> tuple[str, int]:
        """Redact secret patterns from a string. Returns (redacted_text, count)."""
        count = 0
        for pattern, replacement in _SECRET_RULES:
            text, n = pattern.subn(replacement, text)
            count += n
        if self._redact_ips:
            text, n = _IP_RE.subn("[REDACTED:ip]", text)
            count += n
        return text, count

    def redact_prompt(self, prompt: str) -> tuple[str, RedactionStats]:
        """Redact the full rendered prompt string. Returns (clean_prompt, stats)."""
        cleaned, count = self.redact(prompt)
        return cleaned, RedactionStats(total_redactions=count)
