"""Log redaction filter — strips sensitive values from log messages.

Redacts:
- OpenAI / Anthropic / Groq API keys (sk-...)
- Slack bot tokens (xoxb-...)
- Encrypted blobs (enc:v1:...) — already safe, but don't leak them
- Fernet key format (url-safe base64, 44 chars)

Attach to the root logger so all log output is scrubbed:

    import logging
    from errander.observability.redaction import SecretsRedactingFilter
    logging.root.addFilter(SecretsRedactingFilter())
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),              # OpenAI / Anthropic / Groq keys
    re.compile(r"xoxb-[a-zA-Z0-9-]{40,}"),             # Slack bot tokens
    re.compile(r"enc:v1:[A-Za-z0-9_=\-]+"),            # Errander encrypted blobs
    re.compile(r"[A-Za-z0-9_\-]{43}="),                # Fernet key format (44-char base64)
]

_REPLACEMENT = "<redacted>"


class SecretsRedactingFilter(logging.Filter):
    """Logging filter that redacts known secret patterns from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact(str(a)) for a in record.args)
        return True


def _redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(_REPLACEMENT, text)
    return text
