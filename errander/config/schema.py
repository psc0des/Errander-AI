"""Configuration YAML schema validation.

Validates the structure and types of YAML configuration files
(inventory, maintenance windows, policies) against expected schemas.
Uses Pydantic models for validation.

Config inheritance: Global defaults → Environment settings → Host overrides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, field_validator

from errander.integrations.secrets import DecryptionError, SecretsManager


def _decrypt_yaml_strings(data: Any, path: str = "") -> Any:
    """Walk a parsed YAML structure and decrypt any enc:v1: string values.

    Args:
        data: Parsed YAML (dict, list, or scalar).
        path: Dot-separated field path for error messages.

    Returns:
        Same structure with encrypted strings replaced by decrypted values.

    Raises:
        DecryptionError: If a value starts with enc:v1: but decryption fails.
        MasterKeyMissingError: If encrypted values exist but no key is set.
    """
    sm = SecretsManager(require_key=False)
    if isinstance(data, dict):
        return {
            k: _decrypt_yaml_strings(v, f"{path}.{k}" if path else k)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_decrypt_yaml_strings(item, path) for item in data]
    if isinstance(data, str) and sm.is_encrypted(data):
        try:
            return sm.decrypt(data)
        except DecryptionError as exc:
            msg = f"Failed to decrypt field '{path}': {exc}"
            raise DecryptionError(msg) from exc
    return data


class TargetSchema(BaseModel):
    """Schema for a single VM target within an environment."""

    host: str
    name: str
    os_family: str
    tags: list[str] = []
    ssh_user: str | None = None
    ssh_key_path: str | None = None
    policy: str | None = None
    # Services checked pre/post maintenance for health regressions (Phase 1.3).
    # Host-level list overrides env-level list when both are set.
    critical_services: list[str] = []

    # Set true to skip the failed SSH login probe for this VM (e.g., honeypots
    # or bastion hosts where high failure counts are expected and not actionable).
    disable_failed_login_check: bool = False

    @field_validator("os_family")
    @classmethod
    def validate_os_family(cls, v: str) -> str:
        allowed = {"ubuntu", "debian", "rhel"}
        if v.lower() not in allowed:
            msg = f"os_family must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v.lower()

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        if not v.strip():
            msg = "host must be non-empty"
            raise ValueError(msg)
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            msg = "name must be non-empty"
            raise ValueError(msg)
        return v.strip()


class EnvironmentSchema(BaseModel):
    """Schema for an environment group (production, staging, dev)."""

    maintenance_window: str | None = None
    maintenance_days: list[str] = []
    maintenance_timezone: str = "UTC"
    approval_policy: str = "strict"
    ssh_user: str = "errander-ai"
    ssh_key_path: str = "~/.ssh/errander"
    # Environment-level default; host-level list overrides when set.
    critical_services: list[str] = []
    docker_command_mode: Literal["wrapper", "direct_sudo", "disabled"] = "wrapper"
    targets: list[TargetSchema]

    @field_validator("approval_policy")
    @classmethod
    def validate_policy(cls, v: str) -> str:
        allowed = {"relaxed", "moderate", "strict"}
        if v not in allowed:
            msg = f"approval_policy must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v


class PolicySchema(BaseModel):
    """Schema for a single policy definition."""

    auto_approve: list[str] = []
    human_approve: list[str] = []
    blocked: list[str] = []
    disk_cleanup_threshold: int = 80
    log_rotation_max_age_days: int = 7
    log_max_file_size_mb: int = 1000
    docker_prune_all: bool = False
    tmp_cleanup_age_days: int = 7
    journal_vacuum_days: int = 7


class InventoryConfig(BaseModel):
    """Top-level schema for inventory.yaml."""

    environments: dict[str, EnvironmentSchema]


class PoliciesConfig(BaseModel):
    """Top-level schema for policies.yaml."""

    policies: dict[str, PolicySchema]


class AgentSettingsSchema(BaseModel):
    """Schema for agent settings block."""

    approval_timeout_seconds: int = 1800
    ssh_command_timeout_seconds: int = 300
    ssh_reconnect_attempts: int = 3
    ssh_reconnect_backoff: list[int] = [5, 15, 45]
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

    @field_validator("rolling_update_percentage")
    @classmethod
    def validate_rolling_pct(cls, v: int) -> int:
        if not 1 <= v <= 100:
            msg = f"rolling_update_percentage must be in [1, 100], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("wave_failure_threshold", "fleet_failure_threshold")
    @classmethod
    def validate_failure_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            msg = f"failure threshold must be in [0.0, 1.0], got {v}"
            raise ValueError(msg)
        return v

    @field_validator(
        "approval_timeout_seconds",
        "ssh_command_timeout_seconds",
        "vm_lock_ttl_seconds",
        "graceful_shutdown_timeout_seconds",
    )
    @classmethod
    def validate_timeouts(cls, v: int) -> int:
        if not 1 <= v <= 86400:
            msg = f"timeout must be in [1, 86400], got {v}"
            raise ValueError(msg)
        return v


class SlackSettingsSchema(BaseModel):
    """Schema for Slack settings block."""

    approvals_channel_env: str = "ERRANDER_SLACK_CHANNEL_ID"
    status_channel: str = "errander-status"
    poll_interval_seconds: int = 30


class LLMSettingsSchema(BaseModel):
    """Schema for LLM settings block."""

    model: str = ""
    temperature: float = 0.1
    timeout_seconds: int = 30
    max_retries: int = 2

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            msg = f"temperature must be in [0.0, 2.0], got {v}"
            raise ValueError(msg)
        return v


class ScheduleSchema(BaseModel):
    """Schema for per-environment schedule."""

    maintenance: str | None = None
    discovery: str | None = None


class DiskGrowthTrendSchema(BaseModel):
    """Schema for disk growth trend settings."""

    enabled: bool = True
    threshold_pct: int = 10
    window_days: int = 7
    retention_days: int = 90


class DriftSignalsSchema(BaseModel):
    """Schema for per-kind drift detection toggles and tuning."""

    sudoers: bool = True
    authorized_keys: bool = True
    listening_ports: bool = True
    scheduled_jobs: bool = True
    diff_max_lines: int = 50
    retention_captures: int = 30


class FailedSSHLoginsSchema(BaseModel):
    """Schema for failed SSH login snapshot settings."""

    enabled: bool = True
    window_hours: int = 24


class SRESignalsSchema(BaseModel):
    """Schema for the sre_signals settings block.

    All features default to enabled; set to false to opt individual VMs out
    via inventory tags (Phase 2) or disable globally here.
    """

    preflight_lock_check: bool = True
    reboot_required_check: bool = True
    service_health_check: bool = True
    disk_growth_trend: DiskGrowthTrendSchema = DiskGrowthTrendSchema()
    drift: DriftSignalsSchema = DriftSignalsSchema()
    failed_ssh_logins: FailedSSHLoginsSchema = FailedSSHLoginsSchema()


class SettingsConfig(BaseModel):
    """Top-level schema for settings.yaml."""

    agent: AgentSettingsSchema = AgentSettingsSchema()
    slack: SlackSettingsSchema = SlackSettingsSchema()
    llm: LLMSettingsSchema = LLMSettingsSchema()
    schedules: dict[str, ScheduleSchema] = {}
    sre_signals: SRESignalsSchema = SRESignalsSchema()


def validate_inventory(config_path: Path) -> InventoryConfig:
    """Load and validate an inventory YAML file.

    Args:
        config_path: Path to inventory YAML file.

    Returns:
        Validated InventoryConfig.

    Raises:
        FileNotFoundError: If file doesn't exist.
        yaml.YAMLError: If YAML is malformed.
        pydantic.ValidationError: If schema validation fails.
    """
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    raw = config_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        msg = "Config file must contain a YAML mapping"
        raise ValueError(msg)

    data = _decrypt_yaml_strings(data)
    return InventoryConfig.model_validate(data)


def validate_policies(config_path: Path) -> PoliciesConfig:
    """Load and validate a policies YAML file.

    Args:
        config_path: Path to policies YAML file.

    Returns:
        Validated PoliciesConfig.

    Raises:
        FileNotFoundError: If file doesn't exist.
        yaml.YAMLError: If YAML is malformed.
        pydantic.ValidationError: If schema validation fails.
    """
    if not config_path.exists():
        msg = f"Policies file not found: {config_path}"
        raise FileNotFoundError(msg)

    raw = config_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        msg = "Policies file must contain a YAML mapping"
        raise ValueError(msg)

    data = _decrypt_yaml_strings(data)
    return PoliciesConfig.model_validate(data)


def validate_settings(config_path: Path) -> SettingsConfig:
    """Load and validate a settings YAML file.

    Args:
        config_path: Path to settings YAML file.

    Returns:
        Validated SettingsConfig.

    Raises:
        FileNotFoundError: If file doesn't exist.
        yaml.YAMLError: If YAML is malformed.
        pydantic.ValidationError: If schema validation fails.
    """
    if not config_path.exists():
        msg = f"Settings file not found: {config_path}"
        raise FileNotFoundError(msg)

    raw = config_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        msg = "Settings file must contain a YAML mapping"
        raise ValueError(msg)

    data = _decrypt_yaml_strings(data)
    return SettingsConfig.model_validate(data)
