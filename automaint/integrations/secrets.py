"""Secrets interface — environment variables (v1), HashiCorp Vault (v2).

v1: All secrets loaded from environment variables.
v2: Migrate to HashiCorp Vault with env var fallback.

Required secrets:
- AUTOMAINT_SLACK_BOT_TOKEN: Slack bot OAuth token
- AUTOMAINT_SLACK_CHANNEL_ID: Approvals channel ID
- AUTOMAINT_LLM_BASE_URL: vLLM endpoint URL
- AUTOMAINT_LLM_API_KEY: vLLM API key (if required)
- AUTOMAINT_AUDIT_DB_URL: SQLite path (v1)
"""

from __future__ import annotations

import os


def get_secret(name: str, default: str | None = None) -> str:
    """Retrieve a secret value.

    v1: Read from environment variables.
    v2: Read from Vault with env var fallback.

    Args:
        name: Secret name (e.g., AUTOMAINT_SLACK_BOT_TOKEN).
        default: Default value if not set.

    Returns:
        Secret value.

    Raises:
        ValueError: If secret is not set and no default provided.
    """
    value = os.environ.get(name, default)
    if value is None:
        msg = f"Required secret not set: {name}"
        raise ValueError(msg)
    return value
