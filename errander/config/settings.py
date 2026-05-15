"""Global settings and environment variable loading.

Centralizes all configuration that comes from environment variables
or has sensible defaults. Secrets (tokens, keys) come from env vars.
YAML-based settings (agent, slack, llm blocks) come from settings.yaml.

Precedence (highest to lowest):
  env vars > DB overrides > YAML > defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from errander.config.schema import SettingsConfig, validate_settings
from errander.integrations.secrets import SecretsManager

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class DiskGrowthSettings:
    """Runtime disk growth trend settings."""

    enabled: bool = True
    threshold_pct: int = 10
    window_days: int = 7
    retention_days: int = 90


@dataclass
class DriftSettings:
    """Runtime per-kind drift detection settings."""

    sudoers: bool = True
    authorized_keys: bool = True
    listening_ports: bool = True
    scheduled_jobs: bool = True
    diff_max_lines: int = 50
    retention_captures: int = 30


@dataclass
class FailedSSHLoginsSettings:
    """Runtime failed SSH login snapshot settings."""

    enabled: bool = True
    window_hours: int = 24


@dataclass
class SRESignalSettings:
    """Runtime SRE signal feature flags and tuning."""

    preflight_lock_check: bool = True
    reboot_required_check: bool = True
    service_health_check: bool = True
    disk_growth_trend: DiskGrowthSettings = field(default_factory=DiskGrowthSettings)
    drift: DriftSettings = field(default_factory=DriftSettings)
    failed_ssh_logins: FailedSSHLoginsSettings = field(default_factory=FailedSSHLoginsSettings)


@dataclass
class Settings:
    """Global application settings.

    Loaded from environment variables with ERRANDER_ prefix,
    merged with optional settings.yaml file and optional DB overrides.

    Attributes:
        slack_bot_token: Slack bot OAuth token.
        slack_channel_id: Approvals channel ID.
        llm_base_url: LLM endpoint URL.
        llm_model: Model ID for the provider.
        llm_api_key: LLM API key.
        llm_temperature: Sampling temperature.
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
        ui_user: HTTP Basic Auth username for /ui/* (env only).
        ui_password: HTTP Basic Auth password for /ui/* (env only).
        sources: Maps each field name to its origin (env/db/yaml/default).
    """

    # Secrets from env vars
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    llm_base_url: str = ""
    llm_api_key: str = "not-needed"
    audit_db_url: str = "errander.sqlite"

    # LLM provider
    llm_model: str = ""
    llm_temperature: float = 0.1

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

    # Rolling updates
    rolling_update_percentage: int = 100
    wave_failure_threshold: float = 0.5
    health_check_command: str = "echo ok"

    # Canary
    canary_enabled: bool = False
    canary_health_check_command: str = "systemctl is-system-running"

    # Drift detection
    drift_detection_enabled: bool = False
    drift_abort_on_detection: bool = False

    # Operational defaults
    metrics_port: int = 9090
    dry_run_default: bool = True

    # HITL guardrails — must remain enabled until immutable plan artifact
    # (P0-1) and exact deferred-artifact replay (P0-2) are implemented.
    #
    # require_live_approval: hardcoded True — NOT configurable via settings.yaml
    # or environment variable until P0-1/P0-2 are implemented.  The approval_gate
    # enforces this: if autonomous_live_apply_enabled=False, any attempt to pass
    # require_live_approval=False is silently overridden back to True.
    require_live_approval: bool = True
    #
    # autonomous_live_apply_enabled: product-level gate.  False (default) = agent
    # is a HITL automation assistant — require_live_approval cannot be disabled.
    # True = immutable artifact replay is implemented and audited; agent may apply
    # live changes after human approval of exact pinned commands.
    # DO NOT set True until P0-1 and P0-2 are fully implemented and tested.
    autonomous_live_apply_enabled: bool = False

    # Audit mode: "strict" (fail-closed on write error for live actions) or
    # "best_effort" (log and continue). Default strict so production batches
    # are never silently under-audited (finding #13).
    audit_mode: str = "strict"

    # SSH host key verification (finding #9).
    # known_hosts_path: path to known_hosts file.  Empty = TOFU mode (log warning per connect).
    # ssh_strict_host_keys: when True (default), reject hosts not in known_hosts.
    #   Set False only for dev environments where TOFU is acceptable.
    ssh_known_hosts_path: str = ""
    ssh_strict_host_keys: bool = True

    # UI bind address (finding #14).
    # Default 127.0.0.1 — set to 0.0.0.0 only behind a trusted reverse proxy.
    # Auth is mandatory when bind != 127.0.0.1.
    ui_bind_address: str = "127.0.0.1"

    # UI auth (env-only — bootstrap credentials must not live in DB)
    ui_user: str = ""
    ui_password: str = ""

    # SRE signal feature flags (Phase 1 + Phase 2)
    sre_signals: SRESignalSettings = field(default_factory=SRESignalSettings)

    # Source tracking: maps field name → "env" | "db" | "yaml" | "default"
    sources: dict[str, str] = field(default_factory=dict)


def _load_env_str(key: str, default: str = "") -> str:
    """Load a string from environment variable, decrypting enc:v1: values."""
    value = os.environ.get(key, default)
    return SecretsManager(require_key=False).decrypt_if_needed(value)


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


def _load_env_float(key: str, default: float) -> float:
    """Load a float from environment variable.

    Raises:
        ValueError: If the env var is set but not a valid float.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        msg = f"Environment variable {key} must be a float, got '{raw}'"
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


def _source(env_key: str, db_overrides: dict[str, str], yaml_value: object | None) -> str:
    """Return the source label for a field."""
    if os.environ.get(env_key) is not None:
        return "env"
    if env_key in db_overrides or any(
        k.lower() == env_key.lower() for k in db_overrides
    ):
        return "db"
    if yaml_value is not None:
        return "yaml"
    return "default"


def _build_sre_settings(sre_yaml: object | None) -> SRESignalSettings:
    """Build SRESignalSettings from a parsed YAML block or fall back to defaults."""
    from errander.config.schema import SRESignalsSchema  # avoid circular at module level

    if sre_yaml is None or not isinstance(sre_yaml, SRESignalsSchema):
        return SRESignalSettings()

    dgt = sre_yaml.disk_growth_trend
    dr = sre_yaml.drift
    fsl = sre_yaml.failed_ssh_logins

    return SRESignalSettings(
        preflight_lock_check=sre_yaml.preflight_lock_check,
        reboot_required_check=sre_yaml.reboot_required_check,
        service_health_check=sre_yaml.service_health_check,
        disk_growth_trend=DiskGrowthSettings(
            enabled=dgt.enabled,
            threshold_pct=dgt.threshold_pct,
            window_days=dgt.window_days,
            retention_days=dgt.retention_days,
        ),
        drift=DriftSettings(
            sudoers=dr.sudoers,
            authorized_keys=dr.authorized_keys,
            listening_ports=dr.listening_ports,
            scheduled_jobs=dr.scheduled_jobs,
            diff_max_lines=dr.diff_max_lines,
            retention_captures=dr.retention_captures,
        ),
        failed_ssh_logins=FailedSSHLoginsSettings(
            enabled=fsl.enabled,
            window_hours=fsl.window_hours,
        ),
    )


def load_settings(
    settings_path: Path | None = None,
    db_overrides: dict[str, str] | None = None,
) -> Settings:
    """Load settings from environment variables + optional settings.yaml + DB overrides.

    Precedence (highest to lowest): env vars > DB overrides > YAML > defaults.

    Args:
        settings_path: Optional path to settings.yaml file.
            If None or file doesn't exist, uses defaults.
        db_overrides: Pre-fetched DB override dict (from OverridesStore.get_settings_overrides()).
            Keys match environment variable names (e.g., "ERRANDER_LLM_MODEL").

    Returns:
        Populated Settings instance with sources dict set.

    Raises:
        ValueError: If env vars contain invalid values.
    """
    _db = db_overrides or {}

    # Load YAML settings if available
    yaml_settings: SettingsConfig | None = None
    if settings_path is not None and settings_path.exists():
        yaml_settings = validate_settings(settings_path)

    agent = yaml_settings.agent if yaml_settings else None
    slack = yaml_settings.slack if yaml_settings else None
    llm = yaml_settings.llm if yaml_settings else None
    sre_yaml = yaml_settings.sre_signals if yaml_settings else None

    def _str(env_key: str, yaml_val: str | None, default: str = "") -> str:
        if os.environ.get(env_key) is not None:
            return _load_env_str(env_key, default)
        if env_key in _db:
            return _db[env_key]
        return yaml_val if yaml_val is not None else default

    def _int_field(env_key: str, yaml_val: int | None, default: int) -> int:
        if os.environ.get(env_key) is not None:
            return _load_env_int(env_key, default)
        if env_key in _db:
            try:
                return int(_db[env_key])
            except ValueError:
                return default
        return yaml_val if yaml_val is not None else default

    def _float_field(env_key: str, yaml_val: float | None, default: float) -> float:
        if os.environ.get(env_key) is not None:
            return _load_env_float(env_key, default)
        if env_key in _db:
            try:
                return float(_db[env_key])
            except ValueError:
                return default
        return yaml_val if yaml_val is not None else default

    def _bool_field(env_key: str, yaml_val: bool | None, default: bool) -> bool:
        if os.environ.get(env_key) is not None:
            return _load_env_bool(env_key, default)
        if env_key in _db:
            return _db[env_key].lower() in ("1", "true", "yes")
        return yaml_val if yaml_val is not None else default

    sources: dict[str, str] = {
        "llm_base_url": _source("ERRANDER_LLM_BASE_URL", _db, None),
        "llm_model": _source("ERRANDER_LLM_MODEL", _db, llm.model if llm else None),
        "llm_api_key": _source("ERRANDER_LLM_API_KEY", _db, None),
        "llm_temperature": _source("ERRANDER_LLM_TEMPERATURE", _db, llm.temperature if llm else None),
        "llm_timeout_seconds": _source("ERRANDER_LLM_TIMEOUT", _db, llm.timeout_seconds if llm else None),
        "approval_timeout_seconds": _source("ERRANDER_APPROVAL_TIMEOUT", _db, agent.approval_timeout_seconds if agent else None),
    }

    return Settings(
        # Secrets — env vars first, then DB overrides
        slack_bot_token=_str("ERRANDER_SLACK_BOT_TOKEN", None),
        slack_channel_id=_str("ERRANDER_SLACK_CHANNEL_ID", None),
        llm_base_url=_str("ERRANDER_LLM_BASE_URL", None),
        llm_api_key=_str("ERRANDER_LLM_API_KEY", None, "not-needed"),
        audit_db_url=_str("ERRANDER_AUDIT_DB_URL", None, "errander.sqlite"),
        # LLM settings — support DB overrides for runtime switching
        llm_model=_str("ERRANDER_LLM_MODEL", llm.model if llm else None),
        llm_temperature=_float_field(
            "ERRANDER_LLM_TEMPERATURE",
            llm.temperature if llm else None,
            0.1,
        ),
        llm_timeout_seconds=_int_field(
            "ERRANDER_LLM_TIMEOUT",
            llm.timeout_seconds if llm else None,
            30,
        ),
        llm_max_retries=_int_field(
            "ERRANDER_LLM_MAX_RETRIES",
            llm.max_retries if llm else None,
            2,
        ),
        # Agent settings
        approval_timeout_seconds=_int_field(
            "ERRANDER_APPROVAL_TIMEOUT",
            agent.approval_timeout_seconds if agent else None,
            1800,
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
        # Rolling updates
        rolling_update_percentage=_int_field(
            "ERRANDER_ROLLING_UPDATE_PCT",
            agent.rolling_update_percentage if agent else None,
            100,
        ),
        wave_failure_threshold=(
            agent.wave_failure_threshold if agent else 0.5
        ),
        health_check_command=(
            agent.health_check_command if agent else "echo ok"
        ),
        # Canary
        canary_enabled=_bool_field(
            "ERRANDER_CANARY_ENABLED",
            agent.canary_enabled if agent else None,
            False,
        ),
        canary_health_check_command=(
            agent.canary_health_check_command if agent else "systemctl is-system-running"
        ),
        # Drift detection
        drift_detection_enabled=_bool_field(
            "ERRANDER_DRIFT_DETECTION",
            agent.drift_detection_enabled if agent else None,
            False,
        ),
        drift_abort_on_detection=_bool_field(
            "ERRANDER_DRIFT_ABORT",
            agent.drift_abort_on_detection if agent else None,
            False,
        ),
        # Operational
        metrics_port=_int_field("ERRANDER_METRICS_PORT", None, 9090),
        dry_run_default=_bool_field("ERRANDER_DRY_RUN", None, True),
        # UI auth (env-only)
        ui_user=_load_env_str("ERRANDER_UI_USER"),
        ui_password=_load_env_str("ERRANDER_UI_PASSWORD"),
        # SSH host key verification
        ssh_known_hosts_path=_load_env_str("ERRANDER_SSH_KNOWN_HOSTS", ""),
        ssh_strict_host_keys=_load_env_bool("ERRANDER_SSH_STRICT_HOST_KEYS", True),
        # UI bind address
        ui_bind_address=_load_env_str("ERRANDER_UI_BIND", "127.0.0.1"),
        # SRE signals — build from YAML block, falling back to all defaults
        sre_signals=_build_sre_settings(sre_yaml),
        # Source tracking
        sources=sources,
    )
