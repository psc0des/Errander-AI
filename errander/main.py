"""Entry point for the Errander-AI agent.

Long-lived process that:
1. Loads configuration and validates inventory
2. Starts APScheduler for maintenance window scheduling
3. Starts Prometheus /metrics and /health HTTP server
4. Runs LangGraph batch orchestrator on schedule (or on-demand via --run-now)
5. Handles graceful shutdown on SIGTERM/SIGINT

Usage:
    uv run python -m errander [options]
    uv run python -m errander --run-now --env production --dry-run
    uv run python -m errander --run-now --env production --force --force-reason "emergency"
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from errander.config.schema import EnvironmentSchema, validate_inventory, validate_settings
from errander.config.settings import Settings, load_settings
from errander.db.core import AsyncDatabase
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager
from errander.integrations.llm import LLMClient
from errander.integrations.slack import SlackClient
from errander.models.events import EventType
from errander.observability.metrics import start_metrics_server
from errander.safety.approval_store import ApprovalRequestStore
from errander.safety.audit import AuditStore
from errander.safety.deferred import DeferredExecutionStore
from errander.safety.hygiene_approval import HygieneApprovalManager
from errander.safety.hygiene_store import HygieneApprovalStore
from errander.safety.locking import FileLocker
from errander.safety.overrides import OverridesStore
from errander.scheduling.scheduler import MaintenanceScheduler
from errander.scheduling.windows import (
    MaintenanceWindow,
    check_window_from_config,
    next_window_open,
    window_start_cron,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="errander-ai",
        description="Errander-AI — supervised agentic AI SRE platform",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("settings.yaml"),
        help="Path to settings.yaml (default: settings.yaml)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("inventory.yaml"),
        help="Path to inventory.yaml (default: inventory.yaml)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run a maintenance batch immediately, then exit",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Environment name to run (required with --run-now)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry-run mode — simulate commands, no live changes (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live execution (overrides --dry-run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass maintenance window check",
    )
    parser.add_argument(
        "--force-reason",
        default="",
        help="Reason for forcing outside window (required with --force)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    # Inventory check mode
    parser.add_argument(
        "--check-inventory",
        action="store_true",
        help="Validate inventory.yaml and print a target summary, then exit",
    )

    # LLM health check mode
    parser.add_argument(
        "--check-llm",
        action="store_true",
        help="Check LLM endpoint connectivity, model info, and latency, then exit",
    )

    parser.add_argument(
        "--check-targets",
        metavar="ENV",
        default=None,
        help="SSH to every VM in ENV and report sudo / binary / wrapper readiness. Read-only.",
    )

    parser.add_argument(
        "--migrate-inventory",
        metavar="PATH",
        default=None,
        help=(
            "Migrate a legacy inventory.yaml (flat docker_command_mode field) to "
            "the new nested actions: schema. Writes <PATH>.migrated for review."
        ),
    )

    parser.add_argument(
        "--probe-now",
        metavar="ENV",
        default=None,
        help=(
            "Run a proactive signal probe immediately for ENV and post digest to Slack. "
            "Read-only — no maintenance actions executed."
        ),
    )

    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        default=None,
        help=(
            "Investigate fleet state and answer QUESTION using LLM analysis. "
            "Layer A only — no changes made. Use --env to scope to one environment."
        ),
    )

    # Service restart (operator-triggered, HIGH risk tier)
    parser.add_argument(
        "--restart-service",
        metavar="ENV",
        default=None,
        help=(
            "Trigger an operator-initiated service restart for ENV. "
            "Requires --unit and --vm or --vms. Always requires human approval "
            "in the Web UI (Slack notifies and links)."
        ),
    )
    parser.add_argument(
        "--unit",
        default=None,
        help="Unit name for --restart-service (e.g. nginx.service, gunicorn.service)",
    )
    parser.add_argument(
        "--vm",
        default=None,
        help="Single VM name for --restart-service",
    )
    parser.add_argument(
        "--vms",
        default=None,
        help="Comma-separated VM names for --restart-service (e.g. web-01,web-02)",
    )
    parser.add_argument(
        "--restart-force",
        action="store_true",
        default=False,
        help="Bypass maintenance window check for --restart-service (requires --restart-force-reason)",
    )
    parser.add_argument(
        "--restart-force-reason",
        default=None,
        metavar="REASON",
        help="Mandatory reason when --restart-force is used (logged to audit trail)",
    )

    # SSH known-hosts bootstrap (finding #9)
    parser.add_argument(
        "--bootstrap-known-hosts",
        metavar="ENV",
        default=None,
        help=(
            "Connect once to every host in ENV inventory, pin their host keys "
            "into ERRANDER_SSH_KNOWN_HOSTS file, then exit"
        ),
    )

    # Secrets management
    parser.add_argument(
        "--generate-secrets-key",
        action="store_true",
        help="Generate a new ERRANDER_SECRETS_KEY and print it, then exit",
    )
    parser.add_argument(
        "--encrypt",
        metavar="VALUE",
        default=None,
        help="Encrypt VALUE with ERRANDER_SECRETS_KEY and print the enc:v1: blob, then exit",
    )

    # User management (R2: web-only approval with RBAC)
    parser.add_argument(
        "--user-add",
        metavar="USERNAME",
        default=None,
        help=(
            "Create a web UI user, then exit. Requires --user-groups. Password "
            "is read from ERRANDER_USER_PASSWORD or prompted interactively."
        ),
    )
    parser.add_argument(
        "--user-remove",
        metavar="USERNAME",
        default=None,
        help="Delete a web UI user (their sessions are revoked), then exit",
    )
    parser.add_argument(
        "--user-list",
        action="store_true",
        help="List web UI users with their groups, then exit",
    )
    parser.add_argument(
        "--user-set-groups",
        metavar="USERNAME",
        default=None,
        help="Replace USERNAME's group memberships with --user-groups, then exit",
    )
    parser.add_argument(
        "--user-set-password",
        metavar="USERNAME",
        default=None,
        help=(
            "Set a new password for USERNAME (from ERRANDER_USER_PASSWORD or "
            "interactive prompt), then exit"
        ),
    )
    parser.add_argument(
        "--user-groups",
        metavar="GROUPS",
        default=None,
        help="Comma-separated groups for --user-add / --user-set-groups (admin, reader)",
    )

    # Plan inspection (P2-1)
    parser.add_argument(
        "--plan-show",
        metavar="PLAN_ID",
        default=None,
        help="Pretty-print the full plan artifact for PLAN_ID from the audit DB, then exit",
    )

    # Audit query mode
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Query the audit trail and exit",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Filter audit events by batch ID (use with --audit)",
    )
    parser.add_argument(
        "--vm-id",
        default=None,
        help="Filter audit events by VM ID (use with --audit)",
    )
    parser.add_argument(
        "--action-type",
        default=None,
        help="Filter audit events by action type, e.g. disk_cleanup (use with --audit)",
    )
    parser.add_argument(
        "--event-type",
        default=None,
        help="Filter audit events by event type, e.g. action_started (use with --audit)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=50,
        help="Maximum events to return (default: 50, use with --audit)",
    )
    parser.add_argument(
        "--batches",
        action="store_true",
        help="Show recent batch summaries instead of individual events (use with --audit)",
    )

    # AI decision audit query mode
    parser.add_argument(
        "--ai-decisions",
        action="store_true",
        dest="ai_decisions",
        help="Query the AI decision audit log and exit",
    )
    parser.add_argument(
        "--ai-decision-show",
        type=int,
        metavar="ID",
        default=None,
        dest="ai_decision_show",
        help="Show full detail for a single AI decision by numeric ID, then exit",
    )
    parser.add_argument(
        "--decision-type",
        default=None,
        dest="decision_type",
        help="Filter AI decisions by type, e.g. prioritize_actions (use with --ai-decisions)",
    )

    # Replay eval (AI Trust Layer Phase 2)
    parser.add_argument(
        "--ai-eval-replay",
        action="store_true",
        dest="ai_eval_replay",
        help=(
            "Replay stored LLM decisions against a candidate model and exit. "
            "Use with --eval-model, --decision-type, --last, --batch-id."
        ),
    )
    parser.add_argument(
        "--eval-model",
        default=None,
        dest="eval_model",
        help="Candidate model ID to use for replay eval (use with --ai-eval-replay)",
    )

    # Durability measurement
    parser.add_argument(
        "--measure-durability",
        action="store_true",
        dest="measure_durability",
        help=(
            "Print a durability snapshot (batch completion rate, duration percentiles, "
            "approval wait, per-action stats) from the audit trail and exit"
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=14,
        dest="window_days",
        help="Look-back window in days for --measure-durability (default: 14)",
    )

    # Runs sub-commands (Project A, A6): list / inspect / resume
    parser.add_argument(
        "--runs",
        metavar="CMD",
        default=None,
        dest="runs_command",
        choices=["list", "inspect", "resume"],
        help="Batch run sub-command: list | inspect <id> | resume <id>",
    )
    parser.add_argument(
        "--run-id",
        metavar="BATCH_ID",
        default=None,
        dest="runs_batch_id",
        help="Batch ID for runs inspect / runs resume",
    )
    parser.add_argument(
        "--runs-limit",
        type=int,
        default=20,
        dest="runs_limit",
        help="Number of runs to show for runs list (default: 20)",
    )
    parser.add_argument(
        "--runs-force",
        action="store_true",
        dest="runs_force",
        help="Force resume at an unsafe node (OPERATOR_FORCE_RESUME)",
    )

    # vm-facts sub-command (Project B, B3): operational learning memory CLI
    parser.add_argument(
        "--vm-facts",
        metavar="VM_ID",
        default=None,
        dest="vm_facts_vm_id",
        nargs="?",
        const="",
        help="Print outcome/reboot/rejection facts for VM_ID (omit for cross-fleet by --action)",
    )
    parser.add_argument(
        "--vm-facts-action",
        metavar="ACTION_TYPE",
        default=None,
        dest="vm_facts_action",
        help="Filter vm-facts output to this action type (e.g. patching, disk_cleanup)",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

def _build_maintenance_window(env: EnvironmentSchema) -> MaintenanceWindow | None:
    """Build a MaintenanceWindow from an EnvironmentSchema, or None if unconfigured."""
    if not env.maintenance_window or not env.maintenance_days:
        return None

    # Parse "HH:MM-HH:MM" → (start_hour, end_hour)
    try:
        start_str, end_str = env.maintenance_window.split("-")
        start_hour = int(start_str.split(":")[0])
        end_hour = int(end_str.split(":")[0])
    except (ValueError, AttributeError) as exc:
        logger.warning(
            "Could not parse maintenance_window",
            window=env.maintenance_window,
            error=str(exc),
        )
        return None

    try:
        return MaintenanceWindow(
            days=[d.lower() for d in env.maintenance_days],
            start_hour=start_hour,
            end_hour=end_hour,
            timezone=env.maintenance_timezone,
        )
    except ValueError as exc:
        logger.warning("Invalid maintenance window config", error=str(exc))
        return None


def _resolve_prometheus_url(env: EnvironmentSchema | None, settings: Settings) -> str:
    """Return the effective Prometheus URL: env-level override takes priority."""
    if env is not None and env.prometheus_url:
        return env.prometheus_url
    return settings.prometheus_base_url


def _resolve_elk_config(
    env: EnvironmentSchema | None, settings: Settings
) -> tuple[str, str, str]:
    """Return (elk_url, elk_api_key, elk_index_pattern) with env-level priority."""
    if env is not None:
        url = env.elk_url or settings.elk_base_url
        api_key = env.elk_api_key or settings.elk_api_key
        index_pattern = env.elk_index_pattern or settings.elk_index_pattern
    else:
        url = settings.elk_base_url
        api_key = settings.elk_api_key
        index_pattern = settings.elk_index_pattern
    return url, api_key, index_pattern


def _build_components(settings: Settings) -> tuple[
    SSHConnectionManager,
    SandboxExecutor,
    FileLocker,
    SlackClient | None,
    LLMClient | None,
]:
    """Construct shared infrastructure components from settings."""
    ssh_manager = SSHConnectionManager(
        command_timeout=settings.ssh_command_timeout_seconds,
        reconnect_attempts=settings.ssh_reconnect_attempts,
        reconnect_backoff=settings.ssh_reconnect_backoff,
        known_hosts_path=settings.ssh_known_hosts_path,
        strict_host_keys=settings.ssh_strict_host_keys,
    )

    executor = SandboxExecutor(ssh_manager=ssh_manager, dry_run=settings.dry_run_default)

    locker = FileLocker(lock_dir=Path(".errander-locks"))

    slack: SlackClient | None = None
    if settings.slack_bot_token and settings.slack_channel_id:
        slack = SlackClient(
            bot_token=settings.slack_bot_token,
            channel_id=settings.slack_channel_id,
        )
    else:
        logger.warning("Slack not configured — approval notifications disabled")

    llm: LLMClient | None = None
    if settings.llm_base_url:
        if not settings.llm_model:
            logger.error(
                "ERRANDER_LLM_BASE_URL is set but llm.model is not configured — "
                "set ERRANDER_LLM_MODEL or llm.model in settings.yaml"
            )
            raise ValueError("llm_model is required when llm_base_url is configured")
        llm = LLMClient(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    else:
        logger.warning("LLM not configured — using hardcoded fallback logic")

    return ssh_manager, executor, locker, slack, llm


# ---------------------------------------------------------------------------
# Secrets management
# ---------------------------------------------------------------------------

def run_generate_secrets_key() -> int:
    """Generate and print a new ERRANDER_SECRETS_KEY."""
    from errander.integrations.secrets import SecretsManager

    key = SecretsManager.generate_key()
    print(f"ERRANDER_SECRETS_KEY={key}")
    print()
    print("Save this in a 0600-permissioned EnvironmentFile or your secrets manager.")
    print("Never commit it to git. Losing this key means losing all encrypted values.")
    return 0


def run_encrypt(value: str) -> int:
    """Encrypt VALUE with ERRANDER_SECRETS_KEY and print the enc:v1: blob."""
    from errander.integrations.secrets import MasterKeyMissingError, SecretsManager

    try:
        sm = SecretsManager()
    except MasterKeyMissingError:
        print("Error: ERRANDER_SECRETS_KEY is not set.")
        print("Generate one with: uv run python -m errander --generate-secrets-key")
        return 1

    print(sm.encrypt(value))
    return 0


# ---------------------------------------------------------------------------
# Inventory check
# ---------------------------------------------------------------------------

def run_inventory_check(inventory_path: Path) -> int:
    """Validate inventory.yaml and print a target summary."""
    if not inventory_path.exists():
        print(f"Error: inventory file not found: {inventory_path}")
        return 1
    try:
        inv = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: inventory validation failed: {exc}")
        return 1

    total = sum(len(e.targets) for e in inv.environments.values())
    print(f"Inventory OK — {len(inv.environments)} environment(s), {total} target(s)")
    for env_name, env in inv.environments.items():
        hosts = ", ".join(t.host for t in env.targets)
        print(f"  {env_name}: {hosts or '(no targets)'}")
    return 0


# ---------------------------------------------------------------------------
# LLM check
# ---------------------------------------------------------------------------

async def run_llm_check() -> int:
    """Check the LLM endpoint and print a human-readable status report.

    Reads LLM vars directly from the environment — no Settings object needed,
    so it works even when other env vars (e.g. ERRANDER_UI_PASSWORD) are
    encrypted with a key that isn't available in the current session.
    """
    import os

    from errander.integrations.secrets import SecretsManager
    _sm = SecretsManager(require_key=False)
    base_url = _sm.decrypt_if_needed(os.environ.get("ERRANDER_LLM_BASE_URL", ""))
    model = _sm.decrypt_if_needed(os.environ.get("ERRANDER_LLM_MODEL", ""))
    api_key = _sm.decrypt_if_needed(os.environ.get("ERRANDER_LLM_API_KEY", "not-needed"))

    if not base_url:
        print("LLM not configured — set ERRANDER_LLM_BASE_URL (e.g. http://10.0.1.5:8000/v1)")
        return 1

    if not model:
        print("LLM model not configured — set ERRANDER_LLM_MODEL or llm.model in settings.yaml")
        return 1

    from errander.integrations.llm import LLMClient

    client = LLMClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=0.1,
        timeout_seconds=60,
        max_retries=1,
    )

    print(f"Checking LLM endpoint: {base_url} (model: {model})")
    print("-" * 50)

    result = await client.check_endpoint()

    if not result["reachable"]:
        print("  Status   : UNREACHABLE")
        print(f"  Error    : {result['error']}")
        return 1

    model_ids = result["model_ids"]
    latency = result["latency_ms"]
    test_resp = result["test_response"]
    error = result["error"]

    print("  Status   : OK")
    model_ids_list = list(model_ids) if isinstance(model_ids, list) else []
    print(f"  Models   : {', '.join(str(m) for m in model_ids_list) if model_ids_list else '(none listed)'}")

    if latency is not None:
        print(f"  Latency  : {latency} ms (test completion)")
        print(f"  Response : {test_resp!r}")
    elif error:
        print(f"  Latency  : n/a — {error}")

    print("-" * 50)
    if error and latency is None:
        print("Endpoint reachable but test completion failed.")
        return 1

    print("LLM endpoint is healthy.")
    return 0


# ---------------------------------------------------------------------------
# SSH known-hosts bootstrap (finding #9)
# ---------------------------------------------------------------------------

async def run_bootstrap_known_hosts(env_name: str, inventory_path: Path) -> int:
    """SSH to every host in the given environment and pin host keys.

    Connects with known_hosts=None (TOFU) once, captures the server's host
    key via asyncssh's known_hosts API, and appends it to the file pointed
    to by ERRANDER_SSH_KNOWN_HOSTS (defaults to ~/.ssh/errander_known_hosts).

    Idempotent — if a host is already pinned, the existing entry is kept.
    After this command succeeds, set ERRANDER_SSH_STRICT_HOST_KEYS=true
    (the default) and the agent will enforce host keys from that file.
    """
    import os

    import asyncssh

    from errander.config.schema import validate_inventory

    out_path_str = os.environ.get(
        "ERRANDER_SSH_KNOWN_HOSTS",
        str(Path.home() / ".ssh" / "errander_known_hosts"),
    )
    out_path = Path(out_path_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing pinned keys so we can deduplicate
    existing_lines: set[str] = set()
    if out_path.exists():
        existing_lines = set(out_path.read_text().splitlines())

    try:
        inventory = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading inventory: {exc}")
        return 1

    env = inventory.environments.get(env_name)
    if env is None:
        print(f"Environment '{env_name}' not found in inventory")
        return 1

    targets = env.targets
    if not targets:
        print(f"No targets in environment '{env_name}'")
        return 1

    new_lines: list[str] = []
    errors = 0

    for target in targets:
        hostname = target.host
        ssh_user = env.ssh_user
        key_path = str(Path(env.ssh_key_path).expanduser())

        print(f"  Scanning {hostname} ... ", end="", flush=True)
        try:
            # One-shot connection with TOFU to retrieve server keys
            conn = await asyncssh.connect(
                hostname,
                username=ssh_user,
                client_keys=[key_path],
                known_hosts=None,
                password=None,
            )
            server_host_keys = conn.get_server_host_key()
            conn.close()

            if server_host_keys is not None:
                # Export in OpenSSH known_hosts format
                entry = f"{hostname} {server_host_keys.export_public_key('openssh').decode().strip()}"
                if entry not in existing_lines:
                    new_lines.append(entry)
                    print("pinned")
                else:
                    print("already pinned")
            else:
                print("WARNING — no host key returned")
                errors += 1
        except (OSError, asyncssh.Error) as exc:
            print(f"FAILED — {exc}")
            errors += 1

    if new_lines:
        with out_path.open("a") as f:
            f.write("\n".join(new_lines) + "\n")
        out_path.chmod(0o600)
        print(f"\nPinned {len(new_lines)} new host key(s) to {out_path}")

    if errors:
        print(f"\n{errors} host(s) failed — check network/SSH access")
        return 1

    env_file = Path(".env")
    env_key = "ERRANDER_SSH_KNOWN_HOSTS"
    if env_file.exists():
        existing = env_file.read_text()
        if env_key not in existing:
            with env_file.open("a") as f:
                f.write(f"\n{env_key}={out_path}\n")
            print(f"\nDone. Added {env_key}={out_path} to .env")
        else:
            print(f"\nDone. {env_key} already present in .env")
    else:
        print(f"\nDone. Set {env_key}={out_path} in your .env")
    return 0


# ---------------------------------------------------------------------------
# --migrate-inventory: legacy schema migration helper


def _run_migrate_inventory(path: Path) -> int:
    from errander.config.migrate import migrate_inventory

    if not path.exists():
        print(f"Error: inventory file not found: {path}", flush=True)
        return 1
    try:
        migrated = migrate_inventory(path)
        print(f"\nMigrated inventory written to {migrated}", flush=True)
        print("Review the diff above, then rename to use the new file.", flush=True)
        return 0
    except FileExistsError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}", flush=True)
        return 1


# ---------------------------------------------------------------------------
# --check-targets: pre-flight VM readiness validation
# ---------------------------------------------------------------------------

async def run_check_targets(env_name: str, inventory_path: Path) -> int:
    """SSH to every VM in ENV and report sudo / binary / wrapper readiness.

    Read-only — no mutation of target VMs. Exit code 0 if all ready, 1 if any blocked.
    """
    from errander.config.schema import validate_inventory
    from errander.config.settings import load_settings
    from errander.execution.ssh import SSHConnectionManager
    from errander.execution.target_validation import check_target, render_readiness_report

    try:
        inventory = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading inventory: {exc}")
        return 1

    env = inventory.environments.get(env_name)
    if env is None:
        print(f"Unknown environment: {env_name}")
        return 1

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading settings: {exc}")
        return 1

    ssh_manager = SSHConnectionManager(
        known_hosts_path=settings.ssh_known_hosts_path,
        strict_host_keys=settings.ssh_strict_host_keys,
    )
    results = []
    try:
        for target in env.targets:
            # Resolve per-target effective actions (target overrides env defaults)
            resolved = target.resolve_actions(env.actions)
            t_docker_cfg = resolved.get("docker_hygiene")
            t_docker_mode = (
                (t_docker_cfg.command_mode or "wrapper")
                if t_docker_cfg and t_docker_cfg.enabled
                else "disabled"
            )
            t_enabled = [name for name, cfg in resolved.items() if cfg.enabled]

            username = target.ssh_user or env.ssh_user
            key_path = str(Path(target.ssh_key_path or env.ssh_key_path).expanduser())
            readiness = await check_target(
                vm_id=target.name,
                hostname=target.host,
                username=username,
                key_path=key_path,
                os_family=target.os_family,
                docker_command_mode=t_docker_mode,
                ssh_manager=ssh_manager,
                enabled_actions=t_enabled,
            )
            results.append(readiness)

        # Allowlist drift check — per-target resolved restartable_units
        for target in env.targets:
            resolved_restart = target.resolve_actions(env.actions).get("service_restart")
            if not resolved_restart or not resolved_restart.enabled:
                continue
            inventory_units = set(resolved_restart.restartable_units)
            username = target.ssh_user or env.ssh_user
            key_path = str(Path(target.ssh_key_path or env.ssh_key_path).expanduser())
            try:
                cmd = "cat /etc/errander/restart-allowlist 2>/dev/null || echo '__not_found__'"
                ssh_result = await ssh_manager.execute(
                    target.name, target.host, username, key_path, cmd
                )
                if ssh_result.success and "__not_found__" not in ssh_result.stdout:
                    on_target_units = {
                        line.strip()
                        for line in ssh_result.stdout.splitlines()
                        if line.strip()
                    }
                    drift_found = False
                    for unit in sorted(inventory_units - on_target_units):
                        drift_found = True
                        print(
                            f"  ALLOWLIST DRIFT {target.name}: "
                            f"'{unit}' in inventory but missing from "
                            f"/etc/errander/restart-allowlist"
                        )
                    for unit in sorted(on_target_units - inventory_units):
                        drift_found = True
                        print(
                            f"  ALLOWLIST DRIFT {target.name}: "
                            f"'{unit}' in /etc/errander/restart-allowlist "
                            f"but not in inventory restartable_units"
                        )
                    if not drift_found:
                        units_str = ", ".join(sorted(on_target_units))
                        print(
                            f"  ALLOWLIST OK {target.name}: "
                            f"{units_str} ({len(on_target_units)} unit(s) verified)"
                        )
                else:
                    print(
                        f"  WARN {target.name}: "
                        f"/etc/errander/restart-allowlist not readable — "
                        f"run install-systemctl-restart-wrapper.sh"
                    )
            except Exception:  # noqa: BLE001
                print(
                    f"  WARN {target.name}: "
                    f"failed to read /etc/errander/restart-allowlist"
                )
    finally:
        await ssh_manager.close_all()

    print(render_readiness_report(results))
    return 1 if any(r.verdict == "blocked" for r in results) else 0


# ---------------------------------------------------------------------------
# Probe CLI
# ---------------------------------------------------------------------------


async def run_env_probe_main(env_name: str, inventory_path: Path) -> int:
    """Run a proactive signal probe for ENV and post digest to Slack.

    Read-only — collects disk growth, drift, and failed-login signals
    without executing any maintenance actions.
    """
    from errander.agent.probe import run_env_probe
    from errander.config.schema import validate_inventory
    from errander.config.settings import load_settings
    from errander.execution.sandbox import SandboxExecutor
    from errander.execution.ssh import SSHConnectionManager
    from errander.integrations.slack import SlackClient
    from errander.observability.reporting import render_digest_report
    from errander.safety.audit import AuditStore
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore

    try:
        inventory = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading inventory: {exc}")
        return 1

    env = inventory.environments.get(env_name)
    if env is None:
        print(f"Unknown environment: {env_name}")
        return 1

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading settings: {exc}")
        return 1

    _db_probe = AsyncDatabase(settings.audit_db_url)
    audit_store = AuditStore(_db_probe, strict_mode=(settings.audit_mode == "strict"))
    disk_history_store = VMDiskHistoryStore(_db_probe)
    baseline_store = BaselineStore(_db_probe)
    ssh_manager = SSHConnectionManager(
        known_hosts_path=settings.ssh_known_hosts_path,
        strict_host_keys=settings.ssh_strict_host_keys,
    )
    executor = SandboxExecutor(ssh_manager=ssh_manager, dry_run=False)

    slack: SlackClient | None = None
    if settings.slack_bot_token and settings.slack_channel_id:
        slack = SlackClient(
            bot_token=settings.slack_bot_token,
            channel_id=settings.slack_channel_id,
        )

    from errander.integrations.elk import ElkClient as _ElkClient
    from errander.integrations.prometheus import PrometheusClient as _PromClient
    _prom_url = _resolve_prometheus_url(env, settings)
    _elk_url, _elk_api_key, _elk_index = _resolve_elk_config(env, settings)
    prom: _PromClient | None = _PromClient(_prom_url) if _prom_url else None
    elk: _ElkClient | None = (
        _ElkClient(_elk_url, api_key=_elk_api_key, index_pattern=_elk_index)
        if _elk_url else None
    )

    async with audit_store:
        await disk_history_store.initialize()
        await baseline_store.initialize()

        vms = [
            {
                "vm_id": t.name,
                "hostname": t.host,
                "ssh_user": t.ssh_user or env.ssh_user,
                "ssh_key_path": str(Path(t.ssh_key_path or env.ssh_key_path).expanduser()),
                "os_family": t.os_family,
                "disable_failed_login_check": t.disable_failed_login_check,
            }
            for t in env.targets
        ]

        try:
            report = await run_env_probe(
                env_name=env_name,
                vms=vms,
                ssh_manager=ssh_manager,
                executor=executor,
                disk_history_store=disk_history_store,
                baseline_store=baseline_store,
                audit_store=audit_store,
                sre_settings=settings.sre_signals,
                prometheus_client=prom,
                elk_client=elk,
            )
        finally:
            if prom is not None:
                await prom.close()
            if elk is not None:
                await elk.close()

    digest_text = render_digest_report(report)
    print(digest_text)

    if slack is not None:
        await slack.post_digest(digest_text)
        logger.info("Daily probe digest posted to Slack", env=env_name)

    if report.escalation_needed and slack is not None:
        reasons_text = "\n".join(f"• {r}" for r in report.escalation_reasons)
        await slack.post_alert(
            f":rotating_light: *Probe escalation: {env_name}*\n"
            f"Critical signals detected — consider running an emergency batch:\n"
            f"{reasons_text}\n\n"
            f"Run: `errander --run-now --env {env_name} --force"
            f" --force-reason 'probe escalation'`"
        )
        logger.warning(
            "Probe escalation for %s: %d reason(s)", env_name, len(report.escalation_reasons)
        )

    await ssh_manager.close_all()
    return 0


# ---------------------------------------------------------------------------
# Operator Assistant CLI  (Layer A — read-only)
# ---------------------------------------------------------------------------


async def run_ask_query(
    question: str,
    inventory_path: Path,
    env_name: str | None,
) -> int:
    """Investigate fleet state and answer a question via LLM. Layer A — read-only."""
    from errander.agent.operator_assistant import OperatorAssistant
    from errander.config.schema import validate_inventory
    from errander.config.settings import load_settings
    from errander.integrations.llm import LLMClient
    from errander.safety.audit import AuditStore
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore

    try:
        inventory = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading inventory: {exc}")
        return 1

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading settings: {exc}")
        return 1

    _db_ask = AsyncDatabase(settings.audit_db_url)
    audit_store = AuditStore(_db_ask, strict_mode=False)
    disk_history_store = VMDiskHistoryStore(_db_ask)
    baseline_store = BaselineStore(_db_ask)

    llm: LLMClient | None = None
    if settings.llm_base_url:
        llm = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )

    _ask_env: EnvironmentSchema | None = (
        inventory.environments.get(env_name) if env_name else None
    )
    from errander.integrations.elk import ElkClient as _ElkClientAsk
    from errander.integrations.prometheus import PrometheusClient as _PromClientAsk
    _ask_prom_url = _resolve_prometheus_url(_ask_env, settings)
    _ask_elk_url, _ask_elk_key, _ask_elk_idx = _resolve_elk_config(_ask_env, settings)
    prom: _PromClientAsk | None = _PromClientAsk(_ask_prom_url) if _ask_prom_url else None
    elk_ask: _ElkClientAsk | None = (
        _ElkClientAsk(_ask_elk_url, api_key=_ask_elk_key, index_pattern=_ask_elk_idx)
        if _ask_elk_url else None
    )

    from errander.safety.ai_audit import AIDecisionStore as _AIDecisionStoreAsk
    ai_decision_store_ask = _AIDecisionStoreAsk(_db_ask)

    async with (
        audit_store,
        ai_decision_store_ask,
    ):
        await disk_history_store.initialize()
        await baseline_store.initialize()

        try:
            assistant = OperatorAssistant()
            response = await assistant.investigate(
                question,
                audit_store=audit_store,
                disk_history_store=disk_history_store,
                baseline_store=baseline_store,
                inventory=inventory,
                env_name=env_name,
                llm_client=llm,
                prometheus_client=prom,
                elk_client=elk_ask,
                ai_decision_store=ai_decision_store_ask,
            )
        finally:
            if prom is not None:
                await prom.close()
            if elk_ask is not None:
                await elk_ask.close()

    print(f"\n[{response.risk_level.upper()} RISK] {response.summary}\n")
    print("Findings:")
    for finding in response.findings:
        print(f"  - {finding.text}")
    if response.recommendations:
        print("\nRecommendations:")
        for rec in response.recommendations:
            print(f"  - {rec}")
    if response.data_sources:
        print(f"\nSources consulted: {', '.join(response.data_sources)}")
        tips: list[str] = []
        if not any("elk" in s for s in response.data_sources):
            tips.append("set ERRANDER_ELK_BASE_URL for log analysis")
        if not any("live" in s for s in response.data_sources):
            tips.append("use --live for SSH probe")
        if tips:
            print(f"Tip: {' | '.join(tips)}")
    return 0


# ---------------------------------------------------------------------------
# Service restart CLI  (Layer B — deterministic, audited, approval-gated)
# ---------------------------------------------------------------------------


async def run_restart_service(
    env_name: str,
    unit_name: str,
    vm_ids: list[str],
    dry_run: bool,
    inventory_path: Path,
    force: bool = False,
    force_reason: str | None = None,
) -> int:
    """Operator-triggered service restart — validates inputs, then either prints a plan
    (dry-run) or goes through the Slack approval gate and executes (live).

    Layer B: deterministic validation + audit log. No LLM in this path.
    HIGH risk tier — always requires human approval in the Web UI (Slack
    notifies and links) before live execution.
    Enforces maintenance window (override with --restart-force + --restart-force-reason).
    Acquires a per-VM lock before execution to prevent concurrent maintenance.
    """
    import uuid

    from errander.models.events import AuditEvent

    try:
        inventory = validate_inventory(inventory_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading inventory: {exc}")
        return 1

    env = inventory.environments.get(env_name)
    if env is None:
        print(f"Unknown environment: {env_name}")
        return 1

    all_vm_names = {t.name for t in env.targets}
    targets_by_name = {t.name: t for t in env.targets}

    # Validate VM existence first, then per-VM resolved service_restart config.
    for vm_id in vm_ids:
        if vm_id not in all_vm_names:
            print(
                f"VM '{vm_id}' not found in environment '{env_name}'. "
                f"Known VMs: {sorted(all_vm_names)}"
            )
            return 1

    all_allowed_units: set[str] = set()
    for vm_id in vm_ids:
        target = targets_by_name[vm_id]
        vm_restart_cfg = target.resolve_actions(env.actions).get("service_restart")
        if not vm_restart_cfg or not vm_restart_cfg.enabled:
            print(
                f"service_restart is not enabled for VM '{vm_id}' in '{env_name}'. "
                "Set actions.service_restart.enabled: true in inventory.yaml "
                "for this target or the environment."
            )
            return 1
        if unit_name not in vm_restart_cfg.restartable_units:
            print(
                f"Unit '{unit_name}' is not in restartable_units for VM '{vm_id}'. "
                f"Allowed: {vm_restart_cfg.restartable_units}"
            )
            return 1
        all_allowed_units.update(vm_restart_cfg.restartable_units)

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading settings: {exc}")
        return 1

    # --- Maintenance window check ---
    window = _build_maintenance_window(env)
    if window is not None:
        now = datetime.now(tz=UTC)
        if not check_window_from_config(now, window):
            if force:
                if not force_reason:
                    print(
                        "Error: --restart-force requires --restart-force-reason <reason>"
                    )
                    return 1
                logger.warning(
                    "Maintenance window bypassed for service_restart (force=True): %s",
                    force_reason,
                )
            else:
                next_open = next_window_open(now, window)
                print(
                    f"Error: outside maintenance window "
                    f"(days={window.days}, {window.start_hour:02d}:00-{window.end_hour:02d}:00 "
                    f"{window.timezone}). Next window opens at {next_open.strftime('%Y-%m-%d %H:%M UTC')}.\n"
                    "Use --restart-force --restart-force-reason <reason> to override."
                )
                return 1

    batch_id = str(uuid.uuid4())
    vm_list = ", ".join(vm_ids)

    # Print plan regardless of dry-run / live mode.
    print(f"Service Restart Plan — {env_name}")
    print(f"  Unit      : {unit_name}")
    print(f"  VMs       : {vm_list}")
    print(f"  Allowlist : inventory ✓ ({', '.join(sorted(all_allowed_units))})")
    print("  Risk      : HIGH — human approval required in the Web UI (Slack notifies and links) before execution")
    print(f"  Mode      : {'DRY RUN' if dry_run else 'LIVE'}")

    audit_store = AuditStore(AsyncDatabase(settings.audit_db_url), strict_mode=False)
    async with audit_store:
        await audit_store.log_event(AuditEvent(
            event_type=EventType.SERVICE_RESTART_REQUESTED,
            batch_id=batch_id,
            detail=f"unit={unit_name} vms={vm_ids} env={env_name} dry_run={dry_run}",
        ))

    if dry_run:
        print("\nDRY RUN — plan generated, no execution.")
        return 0

    # --- Live mode: durable web approval gate + per-VM subgraph execution ---
    # R2: the decision is recorded only in the authenticated web UI (the
    # approval_requests row is visible on /ui/approvals of the running agent,
    # which shares this PostgreSQL database). Slack is notify-and-link.
    import hashlib as _hashlib

    from errander.agent.subgraphs.service_restart import build_service_restart_subgraph
    from errander.models.events import AuditEvent as _AuditEvent
    from errander.models.events import EventType as _EventType
    from errander.models.service_restart import ServiceRestartState  # noqa: TC001
    from errander.safety.approval import request_approval
    from errander.safety.approval_store import ApprovalRequestStore

    slack_client: SlackClient | None = None
    if settings.slack_bot_token and settings.slack_channel_id:
        slack_client = SlackClient(
            bot_token=settings.slack_bot_token,
            channel_id=settings.slack_channel_id,
        )

    approval_report = (
        f"Service Restart — Live Execution Approval\n"
        f"  Env     : {env_name}\n"
        f"  Unit    : {unit_name}\n"
        f"  VMs     : {vm_list}\n"
        f"  Allowlist : inventory OK; on-target /etc/errander/restart-allowlist enforced by wrapper\n"
        f"  Batch   : {batch_id}\n"
        f"  Risk    : HIGH — this will restart the unit on the listed VMs"
    )
    plan_hash = _hashlib.sha256(
        f"{env_name}|{unit_name}|{'|'.join(sorted(vm_ids))}".encode()
    ).hexdigest()

    approval_store = ApprovalRequestStore(AsyncDatabase(settings.audit_db_url))
    try:
        await approval_store.create(
            batch_id,
            env_name=env_name,
            plan_id=f"service-restart-{batch_id[:8]}",
            plan_hash=plan_hash,
            report=approval_report,
            vm_plans=None,
            timeout_seconds=settings.approval_timeout_seconds,
        )

        if slack_client is not None:
            try:
                slack_ts = await request_approval(
                    slack_client, batch_id, approval_report,
                    web_base_url=settings.web_base_url or None,
                    timeout_seconds=settings.approval_timeout_seconds,
                )
                await approval_store.set_slack_ts(batch_id, slack_ts)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: Slack notification failed ({exc}) — approve via the web UI.")
        else:
            print(
                "Note: Slack is not configured — no notification sent. "
                "Approve on the running agent's web UI under /ui/approvals."
            )

        print(
            f"\nWaiting for web UI approval (batch {batch_id}, "
            f"timeout {settings.approval_timeout_seconds // 60} min)…"
        )
        request_row = await approval_store.wait_for_decision(
            batch_id, timeout_seconds=settings.approval_timeout_seconds,
        )
        approved = request_row.status == "approved"
        approver = request_row.decided_by
        approver_group = request_row.decided_by_group

        async with audit_store:
            if approved:
                await audit_store.log_event(_AuditEvent(
                    event_type=_EventType.SERVICE_RESTART_APPROVED,
                    batch_id=batch_id,
                    detail=(
                        f"unit={unit_name} vms={vm_ids} approver={approver}"
                        f" group={approver_group or 'n/a'}"
                    ),
                ))
            else:
                await audit_store.log_event(_AuditEvent(
                    event_type=_EventType.SERVICE_RESTART_REJECTED,
                    batch_id=batch_id,
                    detail=(
                        f"unit={unit_name} vms={vm_ids} approver={approver}"
                        f" status={request_row.status}"
                    ),
                ))

        if not approved:
            print(f"Restart {request_row.status.upper()} (approver={approver}). No action taken.")
            return 1

        # Claim the approval before executing — the agent's restart
        # reconciler must never pick this batch up as an orphan.
        if not await approval_store.mark_execution_started(batch_id):
            print("Error: approval already claimed by another executor. No action taken.")
            return 1
    finally:
        await approval_store.close()

    print(f"Restart APPROVED by {approver} ({approver_group or 'n/a'}). Executing on {len(vm_ids)} VM(s)…")

    ssh_manager = SSHConnectionManager(
        known_hosts_path=settings.ssh_known_hosts_path,
        strict_host_keys=settings.ssh_strict_host_keys,
    )
    executor = SandboxExecutor(ssh_manager=ssh_manager, dry_run=False)
    subgraph_compiled = build_service_restart_subgraph(
        executor, audit_store=audit_store, batch_id=batch_id,
    ).compile()
    locker = FileLocker(lock_dir=Path(".errander-locks"))

    overall_success = True
    for vm_id in vm_ids:
        target = targets_by_name[vm_id]
        username = target.ssh_user or env.ssh_user
        key_path = str(Path(target.ssh_key_path or env.ssh_key_path).expanduser())

        # Acquire VM lock — skip if already locked by another batch.
        acquired = await locker.acquire(vm_id, batch_id, ttl_seconds=300)
        if not acquired:
            print(f"  [{vm_id}] SKIPPED — VM is locked by another maintenance batch")
            overall_success = False
            continue

        vm_restart_cfg_exec = target.resolve_actions(env.actions).get("service_restart")
        sub_state: ServiceRestartState = {
            "vm_id": vm_id,
            "os_family": target.os_family,
            "dry_run": False,
            "unit_name": unit_name,
            "restartable_units": vm_restart_cfg_exec.restartable_units if vm_restart_cfg_exec else [],
            "hostname": target.host,
            "username": username,
            "key_path": key_path,
        }
        try:
            final = await subgraph_compiled.ainvoke(sub_state)
        except Exception as exc:  # noqa: BLE001
            logger.error("service_restart subgraph raised for %s: %s", vm_id, exc)
            final = {"status": "failed", "error": str(exc)}
        finally:
            await locker.release(vm_id, batch_id)

        status = final.get("status", "failed")
        if status not in ("success", "dry_run_ok"):
            overall_success = False
            print(f"  [{vm_id}] FAILED: {final.get('error', 'unknown error')}")
        else:
            print(f"  [{vm_id}] OK — {unit_name} is active")

        async with audit_store:
            await audit_store.log_event(_AuditEvent(
                event_type=_EventType.SERVICE_RESTART_EXECUTED,
                batch_id=batch_id,
                vm_id=vm_id,
                action_type="service_restart",
                detail=f"unit={unit_name} status={status}",
            ))

    return 0 if overall_success else 1


# ---------------------------------------------------------------------------
# Audit CLI
# ---------------------------------------------------------------------------

_COL_WIDTHS = (26, 12, 20, 16, 14, 50)
_HEADERS = ("timestamp", "event_type", "batch_id", "vm_id", "action_type", "detail")


def _truncate(s: str, width: int) -> str:
    return s if len(s) <= width else s[: width - 1] + "…"


def _print_audit_table(rows: list[tuple[str, str, str, str, str, str]]) -> None:
    header = "  ".join(h.ljust(w) for h, w in zip(_HEADERS, _COL_WIDTHS, strict=False))
    separator = "  ".join("-" * w for w in _COL_WIDTHS)
    print(header)
    print(separator)
    for row in rows:
        line = "  ".join(_truncate(v, w).ljust(w) for v, w in zip(row, _COL_WIDTHS, strict=False))
        print(line)


def _user_mgmt_actor() -> str:
    """Acting identity recorded on CLI user-management audit events."""
    import getpass as _getpass
    try:
        return f"cli:{_getpass.getuser()}"
    except Exception:  # noqa: BLE001 — getuser can fail on stripped-down envs
        return "cli:unknown"


def _read_new_password(username: str) -> str | None:
    """Password for CLI user commands: ERRANDER_USER_PASSWORD or prompt."""
    import getpass as _getpass
    import os as _os

    env_pw = _os.environ.get("ERRANDER_USER_PASSWORD", "")
    if env_pw:
        return env_pw
    try:
        pw = _getpass.getpass(f"New password for {username!r}: ")
        confirm = _getpass.getpass("Confirm password: ")
    except (EOFError, KeyboardInterrupt):
        return None
    if not pw or pw != confirm:
        return None
    return pw


async def run_user_management(args: argparse.Namespace, audit_db_url: str) -> int:
    """Handle --user-add / --user-remove / --user-list / --user-set-groups /
    --user-set-password. Every mutation is audit-logged with the acting
    identity (R2 acceptance: membership changes are themselves on the record).
    """
    from errander.models.events import AuditEvent as _AuditEvent
    from errander.models.events import EventType as _EventType
    from errander.safety.user_store import UserStore

    db = AsyncDatabase(audit_db_url)
    user_store = UserStore(db)
    await user_store.initialize()
    audit_store = AuditStore(db, strict_mode=False)
    actor = _user_mgmt_actor()
    groups = [g.strip() for g in (args.user_groups or "").split(",") if g.strip()]

    try:
        if args.user_list:
            users = await user_store.list_users()
            if not users:
                print("No users. Create one: python -m errander --user-add <name> --user-groups admin")
                return 0
            print(f"{'USERNAME':<24} {'GROUPS':<24} CREATED")
            for u in users:
                created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—"
                print(f"{u.username:<24} {','.join(u.groups):<24} {created} (by {u.created_by or '—'})")
            return 0

        if args.user_add is not None:
            if not groups:
                print("Error: --user-add requires --user-groups (e.g. --user-groups admin)")
                return 1
            password = _read_new_password(args.user_add)
            if password is None:
                print("Error: empty or mismatched password — user not created.")
                return 1
            try:
                user = await user_store.create_user(
                    args.user_add, password, groups=groups, actor=actor,
                )
            except ValueError as exc:
                print(f"Error: {exc}")
                return 1
            await audit_store.log_event(_AuditEvent(
                event_type=_EventType.USER_CREATED,
                batch_id="user-management",
                detail=f"user={user.username} groups={','.join(user.groups)} by={actor}",
            ))
            print(f"User {user.username!r} created (groups: {', '.join(user.groups)}).")
            return 0

        if args.user_remove is not None:
            if not await user_store.delete_user(args.user_remove):
                print(f"Error: user {args.user_remove!r} not found.")
                return 1
            await audit_store.log_event(_AuditEvent(
                event_type=_EventType.USER_DELETED,
                batch_id="user-management",
                detail=f"user={args.user_remove} by={actor}",
            ))
            print(f"User {args.user_remove!r} deleted (sessions revoked).")
            return 0

        if args.user_set_groups is not None:
            if not groups:
                print("Error: --user-set-groups requires --user-groups")
                return 1
            try:
                changed = await user_store.set_groups(args.user_set_groups, groups, actor=actor)
            except ValueError as exc:
                print(f"Error: {exc}")
                return 1
            if not changed:
                print(f"Error: user {args.user_set_groups!r} not found.")
                return 1
            await audit_store.log_event(_AuditEvent(
                event_type=_EventType.USER_GROUPS_CHANGED,
                batch_id="user-management",
                detail=f"user={args.user_set_groups} groups={','.join(groups)} by={actor}",
            ))
            print(f"User {args.user_set_groups!r} groups set to: {', '.join(groups)} "
                  "(takes effect on their next request).")
            return 0

        if args.user_set_password is not None:
            password = _read_new_password(args.user_set_password)
            if password is None:
                print("Error: empty or mismatched password — unchanged.")
                return 1
            if not await user_store.set_password(args.user_set_password, password):
                print(f"Error: user {args.user_set_password!r} not found.")
                return 1
            await audit_store.log_event(_AuditEvent(
                event_type=_EventType.USER_PASSWORD_CHANGED,
                batch_id="user-management",
                detail=f"user={args.user_set_password} by={actor}",
            ))
            print(f"Password updated for {args.user_set_password!r}.")
            return 0

        print("No user-management action given.")
        return 1
    finally:
        await db.close()


async def run_plan_show(plan_id: str, audit_db_url: str) -> int:
    """Pretty-print the full plan artifact for plan_id (P2-1)."""
    import json as _json

    audit_store = AuditStore(AsyncDatabase(audit_db_url), strict_mode=False)
    async with audit_store:
        snapshot = await audit_store.get_plan_snapshot(plan_id)

    if snapshot is None:
        print(f"Plan '{plan_id}' not found in audit DB.")
        return 1

    print(f"Plan ID  : {snapshot['plan_id']}")
    print(f"Batch ID : {snapshot['batch_id']}")
    print(f"Env      : {snapshot['env_name']}")
    print(f"Hash     : {snapshot['plan_hash']}")
    print(f"Created  : {snapshot['created_at']}")
    print()

    try:
        plan_data = _json.loads(str(snapshot["plan_json"]))
    except Exception:  # noqa: BLE001
        print("(stored plan JSON is malformed)")
        return 1

    for vm in plan_data.get("vm_plans", []):
        vm_id = vm.get("vm_id", "?")
        print(f"VM: {vm_id}")
        for action in (vm.get("planned_actions") or []):
            atype = action.get("action_type", "?")
            preview = action.get("preview") if isinstance(action.get("preview"), dict) else {}
            assert isinstance(preview, dict)
            if atype == "patching":
                pkgs = preview.get("packages") or []
                print(f"  patching: {len(pkgs)} package(s)")
                for pkg in pkgs:
                    if not isinstance(pkg, dict):
                        continue
                    name = pkg.get("name", "?")
                    cur = pkg.get("current", "")
                    tgt = pkg.get("target", "")
                    print(f"    {name}  {cur} -> {tgt}")
            else:
                print(f"  {atype}")
        print()
    return 0


async def run_ai_decisions_query(args: argparse.Namespace, settings: Settings) -> int:
    """Query the AI decision audit log and print results to stdout."""
    import json as _json
    from urllib.parse import urlparse as _urlparse

    from errander.safety.ai_audit import AIDecisionStore

    def _redact_url(url: str) -> str:
        """Return host:port only from a URL (omits path, credentials)."""
        try:
            return _urlparse(url).netloc or url
        except Exception:  # noqa: BLE001
            return url

    try:
        async with AIDecisionStore(AsyncDatabase(settings.audit_db_url)) as store:
            if args.ai_decision_show is not None:
                decision = await store.get_decision_by_id(args.ai_decision_show)
                if decision is None:
                    print(f"AI decision ID {args.ai_decision_show} not found.")
                    return 1
                print(f"ID            : {decision.decision_id}")
                print(f"Batch ID      : {decision.batch_id}")
                print(f"VM ID         : {decision.vm_id or '(batch-level)'}")
                print(f"Decision type : {decision.decision_type}")
                print(f"Model         : {decision.model}")
                print(f"LLM endpoint  : {_redact_url(decision.base_url)}")
                print(f"Template ID   : {decision.prompt_template_id}")
                print(f"Prompt hash   : {decision.prompt_hash}")
                print(f"Outcome       : {decision.outcome}")
                if decision.latency_ms is not None:
                    print(f"Latency       : {decision.latency_ms:.0f} ms")
                if decision.prompt_tokens is not None:
                    print(f"Tokens        : {decision.prompt_tokens} prompt / {decision.completion_tokens} completion")
                print(f"Timestamp     : {decision.timestamp.isoformat()}")
                if decision.model_params:
                    try:
                        mp = _json.loads(decision.model_params)
                        print(f"Model params  : {_json.dumps(mp)}")
                    except Exception:  # noqa: BLE001
                        print(f"Model params  : {decision.model_params}")
                if decision.context_snapshot:
                    print()
                    print("--- Context snapshot ---")
                    try:
                        ctx = _json.loads(decision.context_snapshot)
                        print(_json.dumps(ctx, indent=2))
                    except Exception:  # noqa: BLE001
                        print(decision.context_snapshot)
                if decision.prompt_full:
                    print()
                    print("--- Full prompt ---")
                    print(decision.prompt_full)
                if decision.response_raw:
                    print()
                    print("--- LLM response ---")
                    print(decision.response_raw)
                return 0

            # List mode
            decisions = await store.get_decisions(
                batch_id=args.batch_id,
                vm_id=args.vm_id,
                decision_type=args.decision_type,
                limit=args.last,
            )
            if not decisions:
                print("No AI decisions found matching the given filters.")
                return 0

            header = f"{'ID':>6}  {'batch_id':<36}  {'vm_id':<20}  {'type':<20}  {'outcome':<10}  {'ms':>6}  timestamp"
            print(header)
            print("-" * len(header))
            for d in decisions:
                bid = (d.batch_id or "")[:36]
                vid = (d.vm_id or "")[:20]
                dtype = (d.decision_type or "")[:20]
                outcome = (d.outcome or "")[:10]
                ms = f"{d.latency_ms:.0f}" if d.latency_ms is not None else "—"
                ts = d.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{d.decision_id or 0:>6}  {bid:<36}  {vid:<20}  {dtype:<20}  {outcome:<10}  {ms:>6}  {ts}")
            print(f"\n{len(decisions)} decision(s) shown.")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading AI decision store '{settings.audit_db_url}': {exc}")
        return 1


async def run_ai_eval_replay(args: argparse.Namespace, settings: Settings) -> int:
    """Replay stored LLM decisions against a candidate model and print a summary."""
    from errander.evals.replay import EvalStore, run_replay
    from errander.integrations.llm import LLMClient
    from errander.safety.ai_audit import AIDecisionStore

    candidate_model = args.eval_model or settings.llm_model
    print(f"Replay eval — candidate model: {candidate_model}")
    print(f"Source: {settings.audit_db_url}")

    try:
        candidate_client = LLMClient(
            base_url=settings.llm_base_url,
            model=candidate_model,
            api_key=settings.llm_api_key or "not-needed",
        )
        async with (
            AIDecisionStore(AsyncDatabase(settings.audit_db_url)) as ai_store,
            EvalStore(AsyncDatabase(settings.audit_db_url)) as eval_store,
        ):
            run = await run_replay(
                ai_store=ai_store,
                eval_store=eval_store,
                candidate_client=candidate_client,
                decision_type=args.decision_type,
                batch_id=getattr(args, "batch_id", None),
                limit=getattr(args, "last", 20),
            )

        if run.source_count == 0:
            print("No decisions with stored prompts found matching the filters.")
            return 0

        print(f"\nResults for run {run.run_id}:")
        print(f"  source_count : {run.source_count}")
        print(f"  pass         : {run.pass_count}")
        print(f"  fail         : {run.fail_count}")
        print(f"  error        : {run.error_count}")
        skipped = run.source_count - run.pass_count - run.fail_count - run.error_count
        if skipped:
            print(f"  skipped      : {skipped}")

        violations_found = [r for r in run.results if r.violations]
        if violations_found:
            print(f"\nViolations ({len(violations_found)} decision(s)):")
            for r in violations_found:
                print(f"  [{r.original_id}] {r.decision_type}: {'; '.join(r.violations)}")

        if run.fail_count > 0 or run.error_count > 0:
            print(f"\nEval FAILED: {run.fail_count} violation(s), {run.error_count} error(s).")
            return 1
        print("\nEval PASSED.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error running replay eval: {exc}")
        return 1


async def run_audit_query(args: argparse.Namespace, settings: Settings) -> int:
    """Run an audit query and print results to stdout."""
    audit_store = AuditStore(AsyncDatabase(settings.audit_db_url), strict_mode=False)
    await audit_store.__aenter__()
    try:
        if args.batches:
            batches = await audit_store.get_recent_batches(limit=args.last)
            if not batches:
                print("No batches found.")
                return 0
            print(f"{'batch_id':<36}  {'started_at':<26}  {'events':>6}  vms")
            print("-" * 80)
            for b in batches:
                vm_ids_raw = b["vm_ids"]
                vm_ids_list = list(vm_ids_raw) if isinstance(vm_ids_raw, list) else []
                vms = ", ".join(str(v) for v in vm_ids_list) if vm_ids_list else "(none)"
                print(
                    f"{str(b['batch_id']):<36}  "
                    f"{str(b['started_at']):<26}  "
                    f"{str(b['event_count']):>6}  "
                    f"{vms}"
                )
            return 0

        event_type: EventType | None = None
        if args.event_type:
            try:
                event_type = EventType(args.event_type.lower())
            except ValueError:
                valid = [e.value for e in EventType]
                print(f"Unknown event type '{args.event_type}'. Valid: {valid}")
                return 1

        events = await audit_store.get_events(
            batch_id=args.batch_id,
            vm_id=args.vm_id,
            event_type=event_type,
            action_type=args.action_type,
            limit=args.last,
        )

        if not events:
            print("No events found matching the given filters.")
            return 0

        rows = [
            (
                e.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                e.event_type.value,
                e.batch_id,
                e.vm_id or "",
                e.action_type or "",
                e.detail,
            )
            for e in events
        ]
        _print_audit_table(rows)
        print(f"\n{len(events)} event(s) shown.")
        return 0
    finally:
        await audit_store.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Durability measurement CLI  (Phase A1.3)
# ---------------------------------------------------------------------------


async def run_measure_durability(db_path: str, window_days: int = 14) -> int:
    """Print a durability snapshot from audit_events and exit."""
    from errander.observability.durability import (
        compute_durability_report,
        print_durability_report,
    )
    from errander.safety.migrations import run_migrations

    try:
        db = AsyncDatabase(db_path)
        async with db.begin() as conn:
            await run_migrations(conn)
        report = await compute_durability_report(db, window_days)
        await db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading audit database '{db_path}': {exc}")
        return 1

    print_durability_report(report)
    return 0


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def run_env_batch(
    env_name: str,
    env_schema: EnvironmentSchema,
    settings: Settings,
    executor: SandboxExecutor,
    locker: FileLocker,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore,
    dry_run: bool = True,
    force: bool = False,
    force_reason: str = "",
    approval_store: ApprovalRequestStore | None = None,
    slack_client: SlackClient | None = None,
    hygiene_manager: HygieneApprovalManager | None = None,
    overrides_store: OverridesStore | None = None,
    deferred_store: DeferredExecutionStore | None = None,
    llm_client: LLMClient | None = None,
    disk_history_store: object = None,
    baseline_store: object = None,
    vm_state_store: object = None,
    is_deferred_reapproval: bool = False,
    preloaded_plan_json: str | None = None,
    preloaded_plan_hash: str | None = None,
    preloaded_plan_id: str | None = None,
    preloaded_approved_at: str | None = None,
    preloaded_batch_id: str | None = None,
    preloaded_approved_items: list[dict[str, object]] | None = None,
) -> None:
    """Run a full maintenance batch for one environment.

    Builds the batch graph, constructs initial state from inventory,
    and invokes the compiled graph.
    """
    import uuid as _uuid

    from errander.agent.graph import build_batch_graph, build_operator_approved_packages

    window = _build_maintenance_window(env_schema)

    from errander.safety.ai_audit import AIDecisionStore

    ai_db_path = settings.audit_db_url  # share same PostgreSQL database
    ai_decision_store = AIDecisionStore(AsyncDatabase(ai_db_path))
    await ai_decision_store.initialize()

    # AsyncPostgresSaver: LangGraph checkpoint persistence (Project A, A5).
    # Each batch run gets a unique thread_id so checkpoints don't collide.
    # The same database is used for audit + checkpoints (separate tables).
    # Note: langgraph-checkpoint-postgres uses psycopg3, which wants a plain
    # postgresql:// URL (no +asyncpg driver suffix).
    _thread_id = f"batch-{_uuid.uuid4().hex[:12]}"
    _checkpointer_cm: Any = None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as _AsyncPostgresSaver
        _pg_url = settings.audit_db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        _checkpointer_cm = _AsyncPostgresSaver.from_conn_string(_pg_url)
        _checkpointer = await _checkpointer_cm.__aenter__()
        await _checkpointer.setup()  # idempotent — creates checkpoint tables
        _checkpointer_entered = True
    except Exception as _exc:
        logger.warning("Could not init AsyncPostgresSaver — running without checkpointing: %s", _exc)
        _checkpointer = None
        _checkpointer_entered = False

    sre = settings.sre_signals
    _compiled = build_batch_graph(
        executor=executor,
        locker=locker,
        audit_store=audit_store,
        ssh_manager=ssh_manager,
        window=window,
        approval_store=approval_store,
        slack_client=slack_client,
        hygiene_manager=hygiene_manager,
        web_base_url=settings.web_base_url,
        settings=settings,
        deferred_store=deferred_store,
        llm_client=llm_client,
        ai_decision_store=ai_decision_store,
        disk_history_store=disk_history_store if sre.disk_growth_trend.enabled else None,
        sre_disk_settings=sre.disk_growth_trend if sre.disk_growth_trend.enabled else None,
        baseline_store=baseline_store if (
            sre.drift.sudoers or sre.drift.authorized_keys
            or sre.drift.listening_ports or sre.drift.scheduled_jobs
        ) else None,
        sre_drift_settings=sre.drift,
        sre_failed_logins_settings=sre.failed_ssh_logins if sre.failed_ssh_logins.enabled else None,
        vm_state_store=vm_state_store,
    ).compile(checkpointer=_checkpointer)
    graph = _compiled

    from errander.agent.subgraphs import BUILTIN_ACTIONS

    # Build effective target list: YAML base, apply DB overrides (disable/add)
    # Each target dict carries per-target resolved enabled_actions and docker_command_mode
    # so the fan-out dispatchers can use per-VM action config instead of a single env value.
    yaml_targets: list[dict[str, object]] = []
    for t in env_schema.targets:
        resolved = t.resolve_actions(env_schema.actions)
        t_docker_cfg = resolved.get("docker_hygiene")
        t_docker_mode = (
            (t_docker_cfg.command_mode or "wrapper")
            if t_docker_cfg and t_docker_cfg.enabled
            else "disabled"
        )
        t_enabled = [
            name for name, cfg in resolved.items()
            if cfg.enabled and (
                name not in BUILTIN_ACTIONS or not BUILTIN_ACTIONS[name].operator_triggered
            )
        ]
        yaml_targets.append({
            "vm_id": f"{env_name}/{t.name}",
            "hostname": t.host,
            "ssh_user": t.ssh_user or env_schema.ssh_user,
            "ssh_key_path": t.ssh_key_path or env_schema.ssh_key_path,
            "os_family": t.os_family,
            "disable_failed_login_check": t.disable_failed_login_check,
            "critical_services": list(t.critical_services),
            "_name": t.name,
            "enabled_actions": t_enabled,
            "docker_command_mode": t_docker_mode,
        })

    db_overrides: list[dict[str, object]] = []
    if overrides_store is not None:
        db_overrides = await overrides_store.get_inventory_overrides(env_name)

    disabled_names = {
        str(row["vm_name"])
        for row in db_overrides
        if row["source"] == "yaml_override" and bool(row["disabled"])
    }
    db_additions = [
        row for row in db_overrides
        if row["source"] == "db_addition" and not bool(row["disabled"])
    ]

    targets = [entry for entry in yaml_targets if entry["_name"] not in disabled_names]
    for entry in targets:
        del entry["_name"]

    for row in db_additions:
        targets.append({
            "vm_id": f"{env_name}/{row['vm_name']}",
            "hostname": str(row["host"] or ""),
            "ssh_user": str(row["ssh_user"] or env_schema.ssh_user),
            "ssh_key_path": str(row["ssh_key_path"] or env_schema.ssh_key_path),
            "os_family": str(row["os_family"] or "ubuntu"),
            "critical_services": [],  # DB-added VMs have no inventory schema
        })

    yaml_count = len(env_schema.targets)
    disabled_count = len(disabled_names)
    added_count = len(db_additions)
    logger.info(
        "Inventory: %d YAML targets, %d disabled via UI, %d added via UI, effective: %d",
        yaml_count, disabled_count, added_count, len(targets),
    )

    # Batch-level defaults (env-level, used as fallback for DB-added VMs without per-target config)
    _docker_hygiene_cfg = env_schema.actions.get("docker_hygiene")
    _docker_mode = (
        (_docker_hygiene_cfg.command_mode or "wrapper")
        if _docker_hygiene_cfg and _docker_hygiene_cfg.enabled
        else "disabled"
    )
    _enabled_actions = [
        name for name, cfg in env_schema.actions.items()
        if cfg.enabled and (
            name not in BUILTIN_ACTIONS or not BUILTIN_ACTIONS[name].operator_triggered
        )
    ]

    initial_state = {
        "targets": targets,
        "dry_run": dry_run,
        "force": force,
        "force_reason": force_reason,
        "vm_results": [],
        "env_name": env_name,
        "env_policy": env_schema.approval_policy,
        "docker_command_mode": _docker_mode,
        "enabled_actions": _enabled_actions,
        "ai_db_path": ai_db_path,
        "is_deferred_reapproval": is_deferred_reapproval,
        "preloaded_plan_json": preloaded_plan_json,
        "preloaded_plan_hash": preloaded_plan_hash,
        "preloaded_plan_id": preloaded_plan_id,
        "preloaded_approved_at": preloaded_approved_at,
        # Replay reuses the original batch_id — the approved plan_hash commits
        # to it (fresh IDs made every deferred replay abort at hash verify).
        "preloaded_batch_id": preloaded_batch_id,
        "is_deferred_replay": preloaded_plan_json is not None,
        # Exact-object approval survives defer/restart: per-item selections
        # recovered from the approval row filter patching at wave dispatch.
        "operator_approved_packages": build_operator_approved_packages(
            preloaded_approved_items,
        ),
    }

    logger.info(
        "Starting batch",
        env=env_name,
        targets=len(targets),
        dry_run=dry_run,
        force=force,
    )

    _invoke_config = {"configurable": {"thread_id": _thread_id}} if _checkpointer is not None else {}
    try:
        final = await graph.ainvoke(initial_state, config=_invoke_config)  # type: ignore[call-overload]
    finally:
        await ai_decision_store.close()
        if _checkpointer_entered:
            with contextlib.suppress(Exception):
                await _checkpointer_cm.__aexit__(None, None, None)

    logger.info(
        "Batch complete",
        env=env_name,
        batch_id=final.get("batch_id"),
        results=len(final.get("vm_results", [])),
    )

    if final.get("report"):
        logger.info("Batch report", report=final["report"])

    if final.get("error"):
        logger.warning("Batch ended with error", error=final["error"])


# ---------------------------------------------------------------------------
# Window opener (executes deferred batches at window start)
# ---------------------------------------------------------------------------

async def _window_opener(
    env_name: str,
    env_schema: EnvironmentSchema,
    settings: Settings,
    executor: SandboxExecutor,
    locker: FileLocker,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore,
    deferred_store: DeferredExecutionStore,
    approval_store: ApprovalRequestStore,
    slack_client: SlackClient | None,
    overrides_store: OverridesStore,
    hygiene_manager: HygieneApprovalManager | None = None,
    llm_client: LLMClient | None = None,
    disk_history_store: object = None,
    baseline_store: object = None,
    vm_state_store: object = None,
) -> None:
    """Execute pending deferred batches when a maintenance window opens."""
    from errander.models.events import AuditEvent, EventType

    await deferred_store.expire_old()
    pending = await deferred_store.get_pending(env_name)
    if not pending:
        logger.info("Window opened — no pending deferred batches", env=env_name)
        return

    for record in pending:
        logger.info(
            "Executing deferred batch",
            env=env_name,
            batch_id=record.batch_id,
            approved_by=record.approved_by,
        )
        await deferred_store.mark_executing(record.batch_id)
        # P0-2: distinguish exact-artifact replay from legacy re-plan fallback
        _has_artifact = bool(record.plan_json and record.plan_hash)
        await audit_store.log_event(AuditEvent(
            event_type=EventType.DEFERRED_EXECUTION_STARTED,
            batch_id=record.batch_id,
            detail=(
                f"P0-2 replay: executing exact approved artifact "
                f"(original approval by {record.approved_by} at {record.approved_at.isoformat()})"
                if _has_artifact else
                f"Legacy re-plan: no stored artifact — re-planning and requesting fresh "
                f"approval (original approval by {record.approved_by} "
                f"at {record.approved_at.isoformat()})"
            ),
            metadata={"replay_mode": _has_artifact},
        ))
        # Exact-object approval: recover per-item selections from the durable
        # approval row — a window defer must not widen scope to the full plan.
        _approval_row = await approval_store.get(record.batch_id)
        _approved_items = _approval_row.approved_items if _approval_row is not None else None
        try:
            if _has_artifact:
                # P0-2: replay exact artifact — no re-planning, no Slack re-approval
                await run_env_batch(
                    env_name=env_name,
                    env_schema=env_schema,
                    settings=settings,
                    executor=executor,
                    locker=locker,
                    ssh_manager=ssh_manager,
                    audit_store=audit_store,
                    dry_run=False,
                    force=True,
                    force_reason=(
                        f"P0-2 replay: original approval by {record.approved_by} "
                        f"at {record.approved_at.isoformat()}"
                    ),
                    approval_store=approval_store,
                    slack_client=slack_client,
                    hygiene_manager=hygiene_manager,
                    overrides_store=overrides_store,
                    deferred_store=deferred_store,
                    llm_client=llm_client,
                    disk_history_store=disk_history_store,
                    baseline_store=baseline_store,
                    vm_state_store=vm_state_store,
                    preloaded_plan_json=record.plan_json,
                    preloaded_plan_hash=record.plan_hash,
                    preloaded_approved_at=record.approved_at.isoformat(),
                    preloaded_batch_id=record.batch_id,
                    preloaded_approved_items=_approved_items,
                )
            else:
                # Legacy records saved before P0-2 — fall back to re-plan + re-approve
                logger.warning(
                    "Deferred record %s has no stored artifact — falling back to re-plan",
                    record.batch_id,
                )
                await run_env_batch(
                    env_name=env_name,
                    env_schema=env_schema,
                    settings=settings,
                    executor=executor,
                    locker=locker,
                    ssh_manager=ssh_manager,
                    audit_store=audit_store,
                    dry_run=False,
                    force=True,
                    force_reason=(
                        f"Deferred re-approval: original approval by {record.approved_by} "
                        f"at {record.approved_at.isoformat()} — fresh re-approval required at window time"
                    ),
                    approval_store=approval_store,
                    slack_client=slack_client,
                    hygiene_manager=hygiene_manager,
                    overrides_store=overrides_store,
                    deferred_store=deferred_store,
                    llm_client=llm_client,
                    disk_history_store=disk_history_store,
                    baseline_store=baseline_store,
                    vm_state_store=vm_state_store,
                    is_deferred_reapproval=True,
                )
        finally:
            await deferred_store.mark_done(record.batch_id)


# ---------------------------------------------------------------------------
# Approval reconciler (restart recovery for durable approvals — R3)
# ---------------------------------------------------------------------------

#: Grace period before the reconciler claims an approved-but-unclaimed
#: request. A cross-process executor (e.g. the --restart-service CLI waiting
#: on its own approval row) sits between the operator's decision and its
#: mark_execution_started claim for a few seconds — don't steal the claim.
_RECONCILER_CLAIM_GRACE_SECONDS = 120


async def _approval_reconciler(
    environments: dict[str, EnvironmentSchema],
    settings: Settings,
    executor: SandboxExecutor,
    locker: FileLocker,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore,
    approval_store: ApprovalRequestStore,
    deferred_store: DeferredExecutionStore,
    slack_client: SlackClient | None,
    overrides_store: OverridesStore,
    hygiene_manager: HygieneApprovalManager | None = None,
    hygiene_store: HygieneApprovalStore | None = None,
    llm_client: LLMClient | None = None,
    disk_history_store: object = None,
    baseline_store: object = None,
    vm_state_store: object = None,
) -> None:
    """Reconcile durable approval state after agent restarts (R3).

    The approval gate persists every live-approval request before waiting on
    it, so a crash mid-wait leaves recoverable rows instead of orphaned
    approvals. This job runs on an interval and makes two passes:

    1. Expire — pending requests past expires_at become 'timeout' (auto-REJECT).
    2. Execute — approved requests no executor ever claimed are atomically
       claimed and executed through the exact-artifact replay path (or handed
       to the deferred store when outside the maintenance window).

    (The pre-R2 pass that re-spawned Slack reaction watchers is gone: Slack
    no longer carries decision authority. Orphaned pending requests stay
    decidable from the web UI without any in-process watcher.)
    """
    import json as _json

    from errander.models.events import AuditEvent, EventType

    now = datetime.now(tz=UTC)

    # --- Pass 1: expire overdue pending requests ---
    for batch_id in await approval_store.expire_overdue():
        await audit_store.log_event(AuditEvent(
            event_type=EventType.APPROVAL_TIMEOUT,
            batch_id=batch_id,
            detail="Reconciler: pending approval expired — auto-rejected",
            metadata={"reconciler": True},
        ))
    if hygiene_store is not None:
        await hygiene_store.expire_overdue()

    # --- Pass 2: execute approved-but-unclaimed requests ---
    for req in await approval_store.get_orphaned_approved():
        if approval_store.has_waiter(req.batch_id):
            continue  # live gate is between decision and claim — let it claim
        if (
            req.decided_at is not None
            and (now - req.decided_at).total_seconds() < _RECONCILER_CLAIM_GRACE_SECONDS
        ):
            continue  # freshly decided — its executor may be about to claim
        env_schema = environments.get(req.env_name)
        if env_schema is None:
            logger.error(
                "Reconciler: approved batch references unknown environment — skipping",
                batch_id=req.batch_id, env=req.env_name,
            )
            continue
        if not req.vm_plans or not req.plan_hash:
            # No replayable artifact — claim so this row stops looping, and
            # tell the operator a fresh run is needed (fail closed, audited).
            if await approval_store.mark_execution_started(req.batch_id):
                await audit_store.log_event(AuditEvent(
                    event_type=EventType.APPROVAL_GRANTED,
                    batch_id=req.batch_id,
                    detail=(
                        "Reconciler: approved request has no stored plan artifact — "
                        "cannot replay safely; trigger a fresh run"
                    ),
                    metadata={"reconciler": True, "replayable": False},
                ))
            continue
        if not await approval_store.mark_execution_started(req.batch_id):
            continue  # lost the claim race — another executor owns it

        plan_json = _json.dumps(
            {"plan_id": req.plan_id, "vm_plans": req.vm_plans}, default=str,
        )
        approved_at = (req.decided_at or now).isoformat()

        # Outside the maintenance window → hand off to the deferred store;
        # the window-opener job replays it at window start.
        env_window = _build_maintenance_window(env_schema)
        if env_window is not None and not check_window_from_config(now, env_window):
            next_open = next_window_open(now, env_window)
            await deferred_store.save(
                batch_id=req.batch_id,
                env_name=req.env_name,
                approved_by=req.decided_by,
                window_start=next_open,
                plan_json=plan_json,
                plan_hash=req.plan_hash,
            )
            await audit_store.log_event(AuditEvent(
                event_type=EventType.EXECUTION_DEFERRED,
                batch_id=req.batch_id,
                detail=f"Reconciler: orphaned approval deferred to {next_open.isoformat()}",
                metadata={
                    "reconciler": True,
                    "window_start": next_open.isoformat(),
                    "approved_by": req.decided_by,
                },
            ))
            continue

        logger.info(
            "Reconciler: executing orphaned approved batch",
            batch_id=req.batch_id, env=req.env_name, approved_by=req.decided_by,
        )
        await audit_store.log_event(AuditEvent(
            event_type=EventType.DEFERRED_EXECUTION_STARTED,
            batch_id=req.batch_id,
            detail=(
                f"Reconciler: executing approved batch orphaned by restart "
                f"(approved by {req.decided_by} at {approved_at})"
            ),
            metadata={"reconciler": True, "replay_mode": True},
        ))
        await run_env_batch(
            env_name=req.env_name,
            env_schema=env_schema,
            settings=settings,
            executor=executor,
            locker=locker,
            ssh_manager=ssh_manager,
            audit_store=audit_store,
            dry_run=False,
            force=True,
            force_reason=(
                f"R3 reconciler: original approval by {req.decided_by} at {approved_at}"
            ),
            approval_store=approval_store,
            slack_client=slack_client,
            hygiene_manager=hygiene_manager,
            overrides_store=overrides_store,
            deferred_store=deferred_store,
            llm_client=llm_client,
            disk_history_store=disk_history_store,
            baseline_store=baseline_store,
            vm_state_store=vm_state_store,
            preloaded_plan_json=plan_json,
            preloaded_plan_hash=req.plan_hash,
            preloaded_plan_id=req.plan_id,
            preloaded_approved_at=approved_at,
            preloaded_batch_id=req.batch_id,
            preloaded_approved_items=req.approved_items,
        )


# ---------------------------------------------------------------------------
# Long-running agent (scheduler mode)
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> int:
    """Main async entry point.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    # --- Modes that need no settings at all ---
    if args.generate_secrets_key:
        return run_generate_secrets_key()

    if args.encrypt is not None:
        return run_encrypt(args.encrypt)

    if args.check_inventory:
        return run_inventory_check(args.inventory)

    # LLM check only needs plaintext LLM env vars — run before load_settings()
    # so a decryption error in an unrelated secret (e.g. ERRANDER_UI_PASSWORD)
    # doesn't block connectivity verification.
    if args.check_llm:
        return await run_llm_check()

    if args.bootstrap_known_hosts:
        return await run_bootstrap_known_hosts(
            env_name=args.bootstrap_known_hosts,
            inventory_path=args.inventory,
        )

    # Runs sub-commands: need settings for DB path, but no agent infra
    if args.runs_command is not None:
        from errander.commands.runs import dispatch_runs
        try:
            _runs_settings = load_settings(
                settings_path=args.config if args.config.exists() else None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Error loading settings: {exc}")
            return 1
        return await dispatch_runs(args, _runs_settings.audit_db_url)

    # vm-facts sub-command (Project B, B3)
    if args.vm_facts_vm_id is not None or args.vm_facts_action is not None:
        from errander.commands.vm_facts import dispatch_vm_facts
        try:
            _vf_settings = load_settings(
                settings_path=args.config if args.config.exists() else None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Error loading settings: {exc}")
            return 1
        return dispatch_vm_facts(args, _vf_settings.audit_db_url)

    if args.migrate_inventory:
        return _run_migrate_inventory(Path(args.migrate_inventory))

    if args.check_targets:
        return await run_check_targets(
            env_name=args.check_targets,
            inventory_path=args.inventory,
        )

    if args.probe_now:
        return await run_env_probe_main(
            env_name=args.probe_now,
            inventory_path=args.inventory,
        )

    if args.ask:
        return await run_ask_query(
            question=args.ask,
            inventory_path=args.inventory,
            env_name=args.env,
        )

    if args.restart_service is not None:
        vm_ids: list[str] = []
        if args.vm:
            vm_ids = [args.vm]
        elif args.vms:
            vm_ids = [v.strip() for v in args.vms.split(",") if v.strip()]
        if not vm_ids:
            print(
                "Error: --vm <vm-id> or --vms <comma,separated,ids> is required "
                "with --restart-service"
            )
            return 1
        if not args.unit:
            print("Error: --unit <name> is required with --restart-service")
            return 1
        dry_run = not args.live
        return await run_restart_service(
            env_name=args.restart_service,
            unit_name=args.unit,
            vm_ids=vm_ids,
            dry_run=dry_run,
            inventory_path=args.inventory,
            force=args.restart_force,
            force_reason=args.restart_force_reason,
        )

    # --- Configuration (first pass: no DB overrides yet) ---
    try:
        settings = load_settings(
            settings_path=args.config if args.config.exists() else None,
        )
    except Exception as exc:  # noqa: BLE001
        from errander.integrations.secrets import MasterKeyMissingError
        if isinstance(exc, MasterKeyMissingError):
            print(
                "Error: .env contains encrypted values but ERRANDER_SECRETS_KEY is not set.\n"
                "Fix: export $(cat ~/.errander.key)"
            )
            return 1
        raise

    # --- User management: mutate users/groups and exit (R2) ---
    if (
        args.user_add is not None or args.user_remove is not None or args.user_list
        or args.user_set_groups is not None or args.user_set_password is not None
    ):
        return await run_user_management(args, settings.audit_db_url)

    # --- Audit mode: query and exit, no scheduler or metrics needed ---
    if args.audit:
        return await run_audit_query(args, settings)

    # --- Plan inspection: query and exit (P2-1) ---
    if args.plan_show:
        return await run_plan_show(args.plan_show, settings.audit_db_url)

    # --- AI decision audit: query and exit ---
    if args.ai_decisions or args.ai_decision_show is not None:
        return await run_ai_decisions_query(args, settings)

    # --- Replay eval: replay stored decisions against candidate model and exit ---
    if args.ai_eval_replay:
        return await run_ai_eval_replay(args, settings)

    # --- Durability measurement: query and exit ---
    if args.measure_durability:
        return await run_measure_durability(settings.audit_db_url, args.window_days)

    inventory_path: Path = args.inventory
    if not inventory_path.exists():
        logger.error("Inventory file not found", path=str(inventory_path))
        return 1

    inventory = validate_inventory(inventory_path)
    from errander.config.inventory import load_inventory as _load_inventory
    flat_inventory = _load_inventory(inventory_path)

    # --- Shared AsyncDatabase instance for all stores ---
    _db = AsyncDatabase(settings.audit_db_url)

    # --- Overrides store: must be initialized before building components so
    #     DB-persisted LLM settings are applied on restart (finding #4). ---
    _early_overrides_store = OverridesStore(_db)
    await _early_overrides_store.initialize()
    _db_overrides = await _early_overrides_store.get_settings_overrides()
    if _db_overrides:
        settings = load_settings(
            settings_path=args.config if args.config.exists() else None,
            db_overrides=_db_overrides,
        )

    # --- Shared components (built after DB overrides applied) ---
    ssh_manager, executor, locker, slack, llm = _build_components(settings)

    dry_run = not args.live  # --live overrides --dry-run

    force = args.force
    force_reason = args.force_reason

    if force and not force_reason:
        logger.error("--force requires --force-reason")
        return 1

    # --- Early validation for --run-now (before binding ports) ---
    if args.run_now:
        env_name = args.env
        if not env_name:
            logger.error("--run-now requires --env <environment-name>")
            return 1
        if env_name not in inventory.environments:
            logger.error(
                "Environment not found in inventory",
                env=env_name,
                available=list(inventory.environments.keys()),
            )
            return 1
    else:
        env_name = None

    # --- Audit store ---
    audit_store = AuditStore(
        _db,
        strict_mode=(settings.audit_mode == "strict"),
    )
    await audit_store.__aenter__()

    # --- Agent lease: single-process enforcement (Project A, A5) ---
    from errander.safety.agent_lease import AgentLease, AgentLeaseError
    _agent_lease = AgentLease(_db)
    try:
        await _agent_lease.acquire()
    except AgentLeaseError as _lease_exc:
        logger.error("Cannot start: %s", _lease_exc)
        await audit_store.__aexit__(None, None, None)
        return 1

    # --- Phase A1: startup instrumentation ---
    from errander.observability.metrics import AGENT_STARTS_TOTAL, BATCHES_INTERRUPTED_TOTAL
    AGENT_STARTS_TOTAL.inc()

    from errander.observability.startup_scan import scan_orphan_batches
    _interrupted = await scan_orphan_batches(_db)
    if _interrupted > 0:
        BATCHES_INTERRUPTED_TOTAL.inc(_interrupted)

    # --- Deferred execution store (same DB file, separate table) ---
    deferred_store = DeferredExecutionStore(_db)
    await deferred_store.initialize()

    # --- Overrides store (same DB, separate tables) ---
    overrides_store = _early_overrides_store  # already initialized above

    # --- SRE signal stores (same DB file, separate tables created by migrations) ---
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore
    from errander.safety.vm_state import VMStateStore as _VMStateStore

    disk_history_store = VMDiskHistoryStore(_db)
    await disk_history_store.initialize()

    baseline_store = BaselineStore(_db)
    await baseline_store.initialize()

    vm_state_store = _VMStateStore(_db)
    await vm_state_store.initialize()

    # --- Approval stores (shared between graph, web UI, and reconciler) ---
    approval_store = ApprovalRequestStore(_db)
    await approval_store.initialize()
    hygiene_store = HygieneApprovalStore(_db)
    await hygiene_store.initialize()
    hygiene_manager = HygieneApprovalManager(hygiene_store)

    try:
        # --- Metrics server (agent process: /metrics + /health only) ---
        metrics_runner = await start_metrics_server(
            port=settings.metrics_port,
            bind_address=settings.ui_bind_address,
        )

        # --- --run-now mode: run once and exit ---
        if args.run_now:

            await run_env_batch(
                env_name=env_name,
                env_schema=inventory.environments[env_name],
                settings=settings,
                executor=executor,
                locker=locker,
                ssh_manager=ssh_manager,
                audit_store=audit_store,
                dry_run=dry_run,
                force=force,
                force_reason=force_reason,
                approval_store=approval_store,
                slack_client=slack,
                hygiene_manager=hygiene_manager,
                overrides_store=overrides_store,
                deferred_store=deferred_store,
                llm_client=llm,
                disk_history_store=disk_history_store,
                baseline_store=baseline_store,
                vm_state_store=vm_state_store,
            )
            return 0

        # --- Scheduler mode: run continuously ---
        scheduler_settings = validate_settings(args.config) if args.config.exists() else None
        scheduler = MaintenanceScheduler()

        for env_name, env_schema in inventory.environments.items():
            cron: str | None = None
            if scheduler_settings and env_name in scheduler_settings.schedules:
                cron = scheduler_settings.schedules[env_name].maintenance

            if not cron:
                logger.info("No schedule for environment — skipping", env=env_name)
                continue

            async def _run(
                _env: str = env_name,
                _schema: EnvironmentSchema = env_schema,
            ) -> None:
                await run_env_batch(
                    env_name=_env,
                    env_schema=_schema,
                    settings=settings,
                    executor=executor,
                    locker=locker,
                    ssh_manager=ssh_manager,
                    audit_store=audit_store,
                    dry_run=dry_run,
                    approval_store=approval_store,
                    slack_client=slack,
                    hygiene_manager=hygiene_manager,
                    overrides_store=overrides_store,
                    deferred_store=deferred_store,
                    llm_client=llm,
                    disk_history_store=disk_history_store,
                    baseline_store=baseline_store,
                    vm_state_store=vm_state_store,
                )

            scheduler.add_maintenance_job(_run, cron, job_id=f"maint-{env_name}")

            # Register window-opener job for envs with a maintenance window.
            env_window = _build_maintenance_window(env_schema)
            if env_window is not None:
                opener_cron = window_start_cron(env_window)

                async def _open_window(
                    _env: str = env_name,
                    _schema: EnvironmentSchema = env_schema,
                ) -> None:
                    await _window_opener(
                        env_name=_env,
                        env_schema=_schema,
                        settings=settings,
                        executor=executor,
                        locker=locker,
                        ssh_manager=ssh_manager,
                        audit_store=audit_store,
                        deferred_store=deferred_store,
                        approval_store=approval_store,
                        slack_client=slack,
                        overrides_store=overrides_store,
                        hygiene_manager=hygiene_manager,
                        llm_client=llm,
                        disk_history_store=disk_history_store,
                        baseline_store=baseline_store,
                        vm_state_store=vm_state_store,
                    )

                scheduler.add_maintenance_job(
                    _open_window,
                    opener_cron,
                    job_id=f"window-opener-{env_name}",
                )

            # Register daily probe job when signals cron is configured.
            signals_cron: str | None = None
            if scheduler_settings and env_name in scheduler_settings.schedules:
                signals_cron = scheduler_settings.schedules[env_name].signals

            if signals_cron:
                async def _run_probe(
                    _env: str = env_name,
                    _schema: EnvironmentSchema = env_schema,
                ) -> None:
                    from errander.agent.probe import run_env_probe
                    from errander.integrations.elk import ElkClient as _SchedElkClient
                    from errander.integrations.prometheus import PrometheusClient as _PrometheusClient
                    from errander.observability.reporting import render_digest_report

                    vms = [
                        {
                            "vm_id": t.name,
                            "hostname": t.host,
                            "ssh_user": t.ssh_user or _schema.ssh_user,
                            "ssh_key_path": str(Path(t.ssh_key_path or _schema.ssh_key_path).expanduser()),
                            "os_family": t.os_family,
                            "disable_failed_login_check": t.disable_failed_login_check,
                        }
                        for t in _schema.targets
                    ]
                    _sched_prom_url = _resolve_prometheus_url(_schema, settings)
                    _sched_elk_url, _sched_elk_key, _sched_elk_idx = _resolve_elk_config(_schema, settings)
                    _prom = _PrometheusClient(_sched_prom_url) if _sched_prom_url else None
                    _sched_elk = (
                        _SchedElkClient(_sched_elk_url, api_key=_sched_elk_key, index_pattern=_sched_elk_idx)
                        if _sched_elk_url else None
                    )
                    try:
                        report = await run_env_probe(
                            env_name=_env,
                            vms=vms,
                            ssh_manager=ssh_manager,
                            executor=executor,
                            disk_history_store=disk_history_store,
                            baseline_store=baseline_store,
                            audit_store=audit_store,
                            sre_settings=settings.sre_signals,
                            prometheus_client=_prom,
                            elk_client=_sched_elk,
                        )
                    finally:
                        if _prom is not None:
                            await _prom.close()
                        if _sched_elk is not None:
                            await _sched_elk.close()
                    digest_text = render_digest_report(report)
                    if slack is not None:
                        await slack.post_digest(digest_text)
                    else:
                        logger.info("Probe digest (no Slack):\n%s", digest_text)

                scheduler.add_maintenance_job(
                    _run_probe,
                    signals_cron,
                    job_id=f"probe-{env_name}",
                )
                logger.info("Registered daily probe job", env=env_name, cron=signals_cron)

        # R3: approval reconciler — restart recovery for durable approvals.
        # Expires overdue requests, resumes orphaned Slack watchers, and
        # executes approved batches whose executor died before claiming them.
        async def _reconcile_approvals() -> None:
            await _approval_reconciler(
                environments=dict(inventory.environments),
                settings=settings,
                executor=executor,
                locker=locker,
                ssh_manager=ssh_manager,
                audit_store=audit_store,
                approval_store=approval_store,
                deferred_store=deferred_store,
                slack_client=slack,
                overrides_store=overrides_store,
                hygiene_manager=hygiene_manager,
                hygiene_store=hygiene_store,
                llm_client=llm,
                disk_history_store=disk_history_store,
                baseline_store=baseline_store,
                vm_state_store=vm_state_store,
            )

        scheduler.add_interval_job(
            _reconcile_approvals, seconds=60, job_id="approval-reconciler",
        )

        await scheduler.start()

        logger.info(
            "Errander-AI agent running",
            jobs=scheduler.list_jobs(),
            metrics_port=settings.metrics_port,
            dry_run=dry_run,
        )

        # --- Graceful shutdown ---
        stop_event = asyncio.Event()

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info("Shutdown signal received", signal=sig.name)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # Windows doesn't support add_signal_handler for all signals
            with contextlib.suppress(NotImplementedError, OSError):
                loop.add_signal_handler(sig, _handle_signal, sig)

        await stop_event.wait()

        logger.info("Shutting down scheduler...")
        await scheduler.stop()
        await metrics_runner.cleanup()
        return 0

    finally:
        if slack is not None:
            await slack.close()
        await vm_state_store.close()
        await baseline_store.close()
        await disk_history_store.close()
        await overrides_store.close()
        await deferred_store.close()
        # Release agent lease before closing DB (A5)
        with contextlib.suppress(Exception):
            await _agent_lease.release()
        await audit_store.__aexit__(None, None, None)


def main(argv: list[str] | None = None) -> None:
    """Synchronous entry point — parses args and runs the async main."""
    from dotenv import load_dotenv
    load_dotenv()  # no-op if .env absent; never overrides vars already in the environment
    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from errander.observability.redaction import SecretsRedactingFilter
    logging.root.addFilter(SecretsRedactingFilter())

    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
