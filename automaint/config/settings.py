"""Global settings and environment variable loading.

Centralizes all configuration that comes from environment variables
or has sensible defaults. Secrets (tokens, keys) come from env vars.
YAML-based settings (agent, slack, llm blocks) come from settings.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from automaint.config.schema import SettingsConfig, validate_settings


@dataclass
class Settings:
    """Global application settings.

    Loaded from environment variables with AUTOMAINT_ prefix,
    merged with optional settings.yaml file.

    Attributes:
        slack_bot_token: Slack bot OAuth token.
        slack_channel_id: Approvals channel ID.
        llm_base_url: vLLM endpoint URL.
        llm_api_key: vLLM API key.
        audit_db_url: SQLite database path.
        llm_timeout_seconds: LLM request timeout.
        llm_max_retries: LLM max retry attempts.
        approval_timeout_seconds: Slack approval timeout.
        approval_poll_interval_seconds: Reaction poll interval.
        ssh_command_timeout_seconds: SSH command timeout.
        ssh_reconnect_attempts: SSH reconnection attempts.
        ssh_reconnect_backoff: Backoff intervals for SSH reconnect.
        fleet_failure_threshold: Abort if this fraction of targets fail.
        vm_lock_ttl_seconds: VM lock time-to-live.
        metrics_port: Prometheus metrics server port.
        dry_run_default: Whether to default to dry-run mode.
    """

    # Secrets from env vars
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    llm_base_url: str = ""
    llm_api_key: str = "not-needed"
    audit_db_url: str = "automaint.sqlite"

    # From settings.yaml (agent block)
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    approval_timeout_seconds: int = 1800
    approval_poll_interval_seconds: int = 30
    ssh_command_timeout_seconds: int = 300
    ssh_reconnect_attempts: int = 3
    ssh_reconnect_backoff: list[int] = field(default_factory=lambda: [5, 15, 45])
    fleet_failure_threshold: float = 0.5
    vm_lock_ttl_seconds: int = 7200
    graceful_shutdown_timeout_seconds: int = 120

    # Operational defaults
    metrics_port: int = 9090
    dry_run_default: bool = True


def _load_env_str(key: str, default: str = "") -> str:
    """Load a string from environment variable."""
    return os.environ.get(key, default)


def _load_env_int(key: str, default: int) -> int:
    """Load an int from environment variable.

    Raises:
        ValueError: If the env var is set but not a valid integer.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        msg = f"Environment variable {key} must be an integer, got '{raw}'"
        raise ValueError(msg) from None


def _load_env_bool(key: str, default: bool) -> bool:
    """Load a bool from environment variable.

    Truthy: '1', 'true', 'yes' (case-insensitive).
    Falsy: '0', 'false', 'no' (case-insensitive).
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    if raw.lower() in ("1", "true", "yes"):
        return True
    if raw.lower() in ("0", "false", "no"):
        return False
    msg = f"Environment variable {key} must be a boolean, got '{raw}'"
    raise ValueError(msg)


def load_settings(settings_path: Path | None = None) -> Settings:
    """Load settings from environment variables + optional settings.yaml.

    Environment variables (AUTOMAINT_ prefix) provide secrets.
    settings.yaml provides operational tuning parameters.
    Env vars take precedence over YAML for overlapping fields.

    Args:
        settings_path: Optional path to settings.yaml file.
            If None or file doesn't exist, uses defaults.

    Returns:
        Populated Settings instance.

    Raises:
        ValueError: If env vars contain invalid values.
    """
    # Load YAML settings if available
    yaml_settings: SettingsConfig | None = None
    if settings_path is not None and settings_path.exists():
        yaml_settings = validate_settings(settings_path)

    # Build settings: env vars override YAML, YAML overrides defaults
    agent = yaml_settings.agent if yaml_settings else None
    slack = yaml_settings.slack if yaml_settings else None
    llm = yaml_settings.llm if yaml_settings else None

    return Settings(
        # Secrets — always from env vars
        slack_bot_token=_load_env_str("AUTOMAINT_SLACK_BOT_TOKEN"),
        slack_channel_id=_load_env_str("AUTOMAINT_SLACK_CHANNEL_ID"),
        llm_base_url=_load_env_str("AUTOMAINT_LLM_BASE_URL"),
        llm_api_key=_load_env_str("AUTOMAINT_LLM_API_KEY", "not-needed"),
        audit_db_url=_load_env_str("AUTOMAINT_AUDIT_DB_URL", "automaint.sqlite"),
        # Agent settings — from YAML with env var overrides
        approval_timeout_seconds=_load_env_int(
            "AUTOMAINT_APPROVAL_TIMEOUT",
            agent.approval_timeout_seconds if agent else 1800,
        ),
        ssh_command_timeout_seconds=(
            agent.ssh_command_timeout_seconds if agent else 300
        ),
        ssh_reconnect_attempts=(
            agent.ssh_reconnect_attempts if agent else 3
        ),
        ssh_reconnect_backoff=(
            agent.ssh_reconnect_backoff if agent else [5, 15, 45]
        ),
        fleet_failure_threshold=(
            agent.fleet_failure_threshold if agent else 0.5
        ),
        vm_lock_ttl_seconds=(
            agent.vm_lock_ttl_seconds if agent else 7200
        ),
        graceful_shutdown_timeout_seconds=(
            agent.graceful_shutdown_timeout_seconds if agent else 120
        ),
        # Slack settings
        approval_poll_interval_seconds=(
            slack.poll_interval_seconds if slack else 30
        ),
        # LLM settings
        llm_timeout_seconds=llm.timeout_seconds if llm else 30,
        llm_max_retries=llm.max_retries if llm else 2,
        # Operational
        metrics_port=_load_env_int("AUTOMAINT_METRICS_PORT", 9090),
        dry_run_default=_load_env_bool("AUTOMAINT_DRY_RUN", True),
    )
