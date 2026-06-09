"""Interactive inventory wizard — called by scripts/configure.sh.

Creates or replaces inventory.yaml via guided prompts.  Generates a richly
commented, enterprise-grade file with every option documented inline.

Entry point: ``uv run python -m errander.config.inventory_wizard``
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# ── Output helpers ──────────────────────────────────────────────────────────────



def _hr(char: str = "─", width: int = 62) -> str:
    return char * width


def _ok(msg: str) -> None:
    print(f"    \033[32m✓\033[0m  {msg}")


def _warn(msg: str) -> None:
    print(f"    \033[33m▶\033[0m  {msg}")


def _err(msg: str) -> None:
    print(f"    \033[31m✗\033[0m  {msg}")


def _prompt_val(label: str, default: str = "") -> str:
    """Prompt for a value; loop until non-empty or default accepted."""
    while True:
        if default:
            raw = input(f"    {label} [{default}]: ").strip()
            if not raw:
                return default
            return raw
        else:
            raw = input(f"    {label}: ").strip()
            if raw:
                return raw
            print("      (required — please enter a value)")


def _prompt_val_optional(label: str, hint: str = "") -> str:
    """Prompt for an optional value; returns empty string if skipped."""
    suffix = f"  e.g. {hint}" if hint else ""
    raw = input(f"    {label} (optional{suffix}): ").strip()
    return raw


def _prompt_yn(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"    {question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


def _prompt_policy() -> str:
    """Numbered menu for approval policy; returns 'strict'/'moderate'/'relaxed'."""
    print()
    print("    Approval policy:")
    print("      1) strict   — all actions require explicit Slack approval  (recommended for production)")
    print("      2) moderate — patching + Docker need Slack; cleanup is auto-approved")
    print("      3) relaxed  — most non-destructive actions auto-approved")
    print()
    raw = input("    Choice [1/3, Enter=1]: ").strip()
    if raw == "2":
        return "moderate"
    if raw == "3":
        return "relaxed"
    return "strict"


# ── Day normalisation ──────────────────────────────────────────────────────────

_DAY_ALIASES: dict[str, str] = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
    "monday": "monday", "tuesday": "tuesday", "wednesday": "wednesday",
    "thursday": "thursday", "friday": "friday", "saturday": "saturday", "sunday": "sunday",
}


def _parse_days(raw: str) -> list[str]:
    return [_DAY_ALIASES.get(d.strip().lower(), d.strip().lower()) for d in raw.split(",") if d.strip()]


# ── Data models (wizard-internal) ──────────────────────────────────────────────


@dataclass
class TargetData:
    host: str
    name: str
    os_family: str
    tags: list[str] = field(default_factory=list)
    critical_services: list[str] = field(default_factory=list)
    disable_docker_hygiene: bool = False   # per-target: override env docker_hygiene → false
    service_restart_units: list[str] = field(default_factory=list)  # per-target units


@dataclass
class EnvData:
    name: str
    ssh_user: str
    ssh_key_path: str
    approval_policy: str
    maintenance_window: str
    maintenance_days: list[str]
    maintenance_timezone: str
    enable_patching: bool
    enable_disk_cleanup: bool
    enable_log_rotation: bool
    enable_docker_hygiene: bool  # env-level; per-VM can override to false
    enable_backup_verify: bool
    targets: list[TargetData] = field(default_factory=list)


# ── SSH verification ───────────────────────────────────────────────────────────


async def _check_ssh_async(hostname: str, ssh_user: str, ssh_key_path: str, timeout: float = 10.0) -> bool:
    try:
        import asyncssh
    except ImportError:
        return False
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                hostname,
                username=ssh_user,
                client_keys=[ssh_key_path],
                known_hosts=None,
                password=None,
                connect_timeout=6,
            ),
            timeout=timeout,
        )
        conn.close()
        return True
    except Exception:
        return False


def _check_ssh(hostname: str, ssh_user: str, ssh_key_path: str) -> bool:
    try:
        return asyncio.run(_check_ssh_async(hostname, ssh_user, ssh_key_path))
    except Exception:
        return False


# ── Wizard prompts ─────────────────────────────────────────────────────────────

_ENV_DEFAULTS = ["production", "staging", "dev"]


def _wizard_env(env_number: int) -> EnvData:
    """Collect all fields for one environment via interactive prompts."""
    default_name = _ENV_DEFAULTS[env_number - 1] if env_number <= len(_ENV_DEFAULTS) else f"env-{env_number}"
    print()
    print(f"  {_hr()}")
    print(f"  Environment {env_number}")
    print(f"  {_hr()}")

    name = _prompt_val("Environment name", default_name)
    ssh_user = _prompt_val("SSH user on target VMs", "errander")
    key_default = f"~/.ssh/errander_{name}"
    ssh_key_path = _prompt_val("SSH key path", key_default)

    approval_policy = _prompt_policy()

    print()
    mw_default = "02:00-06:00" if name == "production" else ("22:00-06:00" if name == "staging" else "08:00-20:00")
    maintenance_window = _prompt_val("Maintenance window (HH:MM-HH:MM)", mw_default)

    if name == "production":
        days_default = "tuesday,thursday"
    elif name == "staging":
        days_default = "monday,tuesday,wednesday,thursday,friday"
    else:
        days_default = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
    raw_days = _prompt_val("Maintenance days (comma-separated)", days_default)
    maintenance_days = _parse_days(raw_days)

    maintenance_timezone = _prompt_val("Maintenance timezone", "UTC")

    print()
    print(f"  {_hr('─', 48)}")
    print("  Actions — which maintenance tasks should run?")
    print(f"  {_hr('─', 48)}")
    print()

    enable_patching = _prompt_yn("Enable patching (OS package updates, non-kernel)?", default=True)
    enable_disk_cleanup = _prompt_yn("Enable disk cleanup (/tmp, apt/yum cache, journal)?", default=True)
    enable_log_rotation = _prompt_yn("Enable log rotation?", default=True)
    print()
    print("    docker_hygiene requires wrapper scripts on each VM.")
    print("    Run scripts/install-docker-wrappers-v2.sh after setup to install them.")
    print()
    enable_docker_hygiene = _prompt_yn("Enable docker_hygiene?", default=False)
    print()
    enable_backup_verify = _prompt_yn("Enable backup_verify? (requires backup: in settings.yaml)", default=False)

    return EnvData(
        name=name,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        approval_policy=approval_policy,
        maintenance_window=maintenance_window,
        maintenance_days=maintenance_days,
        maintenance_timezone=maintenance_timezone,
        enable_patching=enable_patching,
        enable_disk_cleanup=enable_disk_cleanup,
        enable_log_rotation=enable_log_rotation,
        enable_docker_hygiene=enable_docker_hygiene,
        enable_backup_verify=enable_backup_verify,
    )


def _wizard_target(
    env: EnvData,
    target_number: int,
) -> TargetData | None:
    """Collect fields for one VM.  Returns None if user enters blank host (stop signal)."""
    print()
    print(f"    VM {target_number}:")
    host = input("      Host (IP or hostname, Enter to finish): ").strip()
    if not host:
        return None

    default_name = f"{env.name}-vm-{target_number:02d}"
    name = _prompt_val("      Name", default_name)

    print()
    print("      OS family:  1) ubuntu  2) debian  3) rhel")
    os_raw = input("      Choice [1/3, Enter=1]: ").strip()
    os_map = {"2": "debian", "3": "rhel"}
    os_family = os_map.get(os_raw, "ubuntu")

    raw_tags = _prompt_val_optional("      Tags (comma-sep)", f"{env.name},web")
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else [env.name]

    raw_svc = _prompt_val_optional("      Critical services to monitor (comma-sep)", "nginx,ssh")
    critical_services = [s.strip() for s in raw_svc.split(",") if s.strip()]

    # Optional SSH verification
    print()
    key_expanded = str(Path(env.ssh_key_path).expanduser())
    if _prompt_yn("      Verify SSH connectivity now?", default=True):
        print(f"        Checking SSH ({env.ssh_user}@{host})...", end=" ", flush=True)
        if Path(key_expanded).exists():
            ok = _check_ssh(host, env.ssh_user, key_expanded)
            if ok:
                print("\033[32mOK\033[0m")
            else:
                print("\033[33mFAILED\033[0m")
                _warn("SSH unreachable — you can fix this later (see SETUP.md Step 2)")
        else:
            print("\033[33mSKIPPED\033[0m")
            _warn(f"SSH key not found at {key_expanded} — check SETUP.md Step 2")

    # Per-target action overrides
    disable_docker_hygiene = False
    service_restart_units: list[str] = []

    print()
    if _prompt_yn("      Configure per-target action overrides?", default=False):
        print()
        if env.enable_docker_hygiene:
            disable_docker_hygiene = _prompt_yn(
                "        Disable docker_hygiene on this VM? (no Docker installed)",
                default=False,
            )
        print()
        print("        service_restart lets operators restart specific systemd units.")
        print("        Requires wrapper install — see SETUP.md #optional-service-restart.")
        print()
        if _prompt_yn("        Configure service_restart units for this VM?", default=False):
            while True:
                raw_units = _prompt_val("          Restartable units (comma-sep)", "nginx.service")
                units = [u.strip() for u in raw_units.split(",") if u.strip()]
                if units:
                    service_restart_units = units
                    break
                print("          At least one unit is required.")

    return TargetData(
        host=host,
        name=name,
        os_family=os_family,
        tags=tags,
        critical_services=critical_services,
        disable_docker_hygiene=disable_docker_hygiene,
        service_restart_units=service_restart_units,
    )


def _wizard_new_inventory() -> list[EnvData]:
    """Run the full interactive wizard and return collected environment data."""
    print()
    print(_hr("═"))
    print("  Errander-AI — Inventory Wizard")
    print(_hr("═"))
    print()
    print("  This wizard creates inventory.yaml with full documentation inline.")
    print("  You can add multiple environments (production, staging, dev, etc.).")
    print("  Press Enter to accept defaults shown in [brackets].")
    print()

    envs: list[EnvData] = []
    env_number = 1

    while True:
        env = _wizard_env(env_number)

        # Collect VMs for this environment
        print()
        print(f"  {_hr('─', 48)}")
        print(f"  Add VMs to '{env.name}'  (Enter blank host to stop)")
        print(f"  {_hr('─', 48)}")

        target_number = 1
        while True:
            target = _wizard_target(env, target_number)
            if target is None:
                break
            env.targets.append(target)
            target_number += 1
            _ok(f"Added {target.name}  ({target.host}, {target.os_family})")

        if env.targets:
            _ok(f"Environment '{env.name}' — {len(env.targets)} VM(s)")
        else:
            _warn(f"No VMs added to '{env.name}' — edit inventory.yaml to add them later")

        envs.append(env)
        env_number += 1

        print()
        if not _prompt_yn("  Add another environment?", default=False):
            break

    return envs


# ── YAML renderer ──────────────────────────────────────────────────────────────


def _render_tags(tags: list[str]) -> str:
    """Render tags as a flow list: [web, prod]"""
    return "[" + ", ".join(tags) + "]"


def _render_days(days: list[str]) -> list[str]:
    return [f"      - {d}" for d in days]


def _render_target(t: TargetData, env: EnvData) -> list[str]:
    lines: list[str] = []
    slug = t.name.replace(" ", "-")
    bar = "─" * max(1, 60 - len(slug) - 6)
    lines.append(f"      # ── {slug} {bar}")
    lines.append(f"      - host: {t.host}")
    lines.append(f"        name: {t.name}")
    lines.append(f"        os_family: {t.os_family}    # ubuntu | debian | rhel")
    lines.append(f"        tags: {_render_tags(t.tags)}")
    lines.append("        node_exporter: false  # updated automatically by configure.sh")

    if t.critical_services:
        lines.append("        critical_services:")
        for svc in t.critical_services:
            lines.append(f"          - {svc}")

    # Per-target action overrides — active block if user configured any
    has_active_overrides = t.disable_docker_hygiene or bool(t.service_restart_units)

    if has_active_overrides:
        lines.append("        actions:")
        if t.disable_docker_hygiene:
            lines.append("          docker_hygiene:")
            lines.append("            enabled: false  # Docker not installed on this VM")
        if t.service_restart_units:
            lines.append("          service_restart:")
            lines.append("            enabled: true")
            lines.append("            restartable_units:")
            for unit in t.service_restart_units:
                lines.append(f"              - {unit}")
    else:
        # Commented-out template so users know what's possible
        lines.append("        # Per-target overrides — uncomment + edit to activate:")
        if t.critical_services:
            pass  # already rendered critical_services above
        else:
            lines.append("        # critical_services: [nginx, ssh, prometheus-node-exporter]")
        docker_hint = "" if not env.enable_docker_hygiene else "   # no Docker on this VM"
        lines.append("        # actions:")
        lines.append(f"        #   docker_hygiene: {{enabled: false}}{docker_hint}")
        lines.append("        #   service_restart:")
        lines.append("        #     enabled: true")
        lines.append("        #     restartable_units: [nginx.service, gunicorn.service]")

    return lines


def _render_env(env: EnvData) -> list[str]:
    lines: list[str] = []
    lines.append(f"  {env.name}:")
    lines.append("")
    lines.append("    # ── SSH ──────────────────────────────────────────────────────────────")
    lines.append(f"    ssh_user: {env.ssh_user}")
    lines.append(f"    ssh_key_path: {env.ssh_key_path}")
    lines.append("")
    lines.append("    # ── Approval ─────────────────────────────────────────────────────────")
    lines.append("    # relaxed  → non-destructive actions auto-approved; high-risk needs Slack")
    lines.append("    # moderate → patching + Docker need Slack approval; cleanup auto-approved")
    lines.append("    # strict   → all actions require explicit human Slack approval (recommended)")
    lines.append(f"    approval_policy: {env.approval_policy}")
    lines.append("")
    lines.append("    # ── Maintenance window ────────────────────────────────────────────────")
    lines.append("    # Agent only runs during this window.  Crossing midnight is supported.")
    lines.append("    # Use --force to run outside it (requires --force-reason).")
    lines.append(f'    maintenance_window: "{env.maintenance_window}"')
    lines.append("    maintenance_days:")
    lines.extend(_render_days(env.maintenance_days))
    lines.append(f"    maintenance_timezone: {env.maintenance_timezone}")
    lines.append("")
    lines.append("    # ── Observability overrides (optional) ───────────────────────────────")
    lines.append("    # Override ERRANDER_PROMETHEUS_BASE_URL / ERRANDER_ELK_BASE_URL for this")
    lines.append("    # environment.  Leave commented out to use .env global defaults.")
    lines.append("    # prometheus_url: http://10.0.1.100:9090")
    lines.append("    # elk_url:        http://10.0.1.101:9200")
    lines.append("    # elk_index_pattern: prod-logs-*")
    lines.append("")
    lines.append("    # ── Node Exporter ─────────────────────────────────────────────────────")
    lines.append("    # Updated automatically by configure.sh (root) after probing each VM.")
    lines.append("    #   true  → scrape :9100  (richer metrics, no SSH cost)")
    lines.append("    #   false → SSH probe fallback  (vmstat + /proc/meminfo + df)")
    lines.append("    node_exporter: false")
    lines.append("")
    lines.append("    # ── Health sentinels ──────────────────────────────────────────────────")
    lines.append("    # Systemd units verified before and after every maintenance run.")
    lines.append("    # active → failed triggers SERVICE_HEALTH_REGRESSION audit event.")
    lines.append("    # Override per-target to add VM-specific services (e.g. nginx, postgresql).")
    lines.append("    critical_services:")
    lines.append("      - ssh")
    lines.append("      - prometheus-node-exporter")
    lines.append("")
    lines.append("    # ── Actions ───────────────────────────────────────────────────────────")
    lines.append("    # All opt-in.  Individual VMs can override any action (see targets below).")
    lines.append("    actions:")

    # patching
    lines.append("      patching:")
    enabled_str = "true" if env.enable_patching else "false"
    lines.append(f"        enabled: {enabled_str}       # OS package updates (non-kernel); Slack approval required")

    # disk_cleanup
    lines.append("      disk_cleanup:")
    enabled_str = "true" if env.enable_disk_cleanup else "false"
    lines.append(f"        enabled: {enabled_str}       # /tmp, apt/yum cache, journal; scope is hardcoded whitelist")

    # log_rotation
    lines.append("      log_rotation:")
    enabled_str = "true" if env.enable_log_rotation else "false"
    lines.append(f"        enabled: {enabled_str}       # Compress logs older than threshold; never deletes data")

    # docker_hygiene
    lines.append("      docker_hygiene:")
    if env.enable_docker_hygiene:
        lines.append("        enabled: true")
        lines.append("        command_mode: wrapper")
        lines.append("        # Optional v1.5 features (all default-off):")
        lines.append("        # volume_deletion_enabled: false")
        lines.append("        # volume_last_mount_days_threshold: 90")
        lines.append("        # build_cache_deletion_enabled: false")
    else:
        lines.append("        enabled: false      # Object-level Docker cleanup; requires wrapper install")
        lines.append("        command_mode: disabled")
        lines.append("        # To enable: install-docker-wrappers-v2.sh + set enabled: true, command_mode: wrapper")
        lines.append("        # Optional v1.5 features (all default-off):")
        lines.append("        # volume_deletion_enabled: false")
        lines.append("        # volume_last_mount_days_threshold: 90")
        lines.append("        # build_cache_deletion_enabled: false")

    # backup_verify
    lines.append("      backup_verify:")
    enabled_str = "true" if env.enable_backup_verify else "false"
    lines.append(f"        enabled: {enabled_str}      # Verify backup recency; requires backup: in settings.yaml")

    # service_restart — always env-level false; configured per-target
    lines.append("      service_restart:")
    lines.append("        enabled: false      # Operator-triggered; configure per-target (VMs run different services)")
    lines.append("        restartable_units: []")
    lines.append("        # To enable per-VM: scripts/install-systemctl-restart-wrapper.sh + add per-target override:")
    lines.append("        #   actions:")
    lines.append("        #     service_restart:")
    lines.append("        #       enabled: true")
    lines.append("        #       restartable_units: [nginx.service, gunicorn.service]")

    # targets
    lines.append("")
    lines.append("    targets:")
    lines.append("")

    if env.targets:
        for i, t in enumerate(env.targets):
            lines.extend(_render_target(t, env))
            if i < len(env.targets) - 1:
                lines.append("")
    else:
        lines.append("      # No VMs configured yet.  Add using bash scripts/add-target.sh")
        lines.append("      # or edit manually using this template:")
        lines.append("      # - host: 10.0.0.10")
        lines.append(f"      #   name: {env.name}-vm-01")
        lines.append("      #   os_family: ubuntu    # ubuntu | debian | rhel")
        lines.append(f"      #   tags: [{env.name}]")
        lines.append("      #   node_exporter: false")

    return lines


def _render_inventory_yaml(envs: list[EnvData], generated_at: str) -> str:
    """Build the complete richly documented inventory.yaml content as a string."""
    lines: list[str] = []

    # File header
    lines.append("# " + "─" * 77)
    lines.append("# Errander-AI — Inventory")
    lines.append(f"# Generated: {generated_at}  |  Edit manually or re-run scripts/configure.sh")
    lines.append("#")
    lines.append("# Structure")
    lines.append("#   environments.<name> — SSH credentials, maintenance window, enabled actions")
    lines.append("#   targets             — individual VMs; each inherits env defaults, overrides per-VM")
    lines.append("#")
    lines.append("# Per-target overrides (uncomment under any target to activate):")
    lines.append("#   actions:")
    lines.append("#     docker_hygiene:  {enabled: false}          # VM has no Docker installed")
    lines.append("#     service_restart: {enabled: true, restartable_units: [nginx.service]}")
    lines.append("#")
    lines.append("# To add more VMs later:      bash scripts/add-target.sh")
    lines.append("# Full annotated reference:   example/inventory.yaml")
    lines.append("# " + "─" * 77)
    lines.append("")
    lines.append("environments:")

    for i, env in enumerate(envs):
        lines.append("")
        lines.extend(_render_env(env))
        if i < len(envs) - 1:
            lines.append("")

    lines.append("")
    return "\n".join(lines)


# ── Existing inventory summary ─────────────────────────────────────────────────


def _summarise_existing(inventory_path: Path) -> str:
    """Return a human-readable one-liner describing the existing inventory."""
    try:
        raw = inventory_path.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(raw)
        envs: dict[str, Any] = data.get("environments", {}) if isinstance(data, dict) else {}
        parts: list[str] = []
        for env_name, env_data in envs.items():
            if not isinstance(env_data, dict):
                continue
            targets = env_data.get("targets", []) or []
            vm_count = len(targets) if isinstance(targets, list) else 0
            parts.append(f"{env_name} ({vm_count} VM{'s' if vm_count != 1 else ''})")
        return "  |  ".join(parts) if parts else "no environments found"
    except Exception:
        return "unreadable"


def _count_vms(inventory_path: Path) -> int:
    """Count total VMs across all environments in an existing inventory."""
    try:
        data: Any = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
        envs: dict[str, Any] = data.get("environments", {}) if isinstance(data, dict) else {}
        return sum(
            len(env_data.get("targets", []) or [])
            for env_data in envs.values()
            if isinstance(env_data, dict)
        )
    except Exception:
        return 0


def _first_env_vars(inventory_path: Path) -> tuple[str, str]:
    """Extract first env name and ssh_key_path from existing inventory for bash handoff."""
    try:
        raw = inventory_path.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(raw)
        envs: dict[str, Any] = data.get("environments", {}) if isinstance(data, dict) else {}
        for env_name, env_data in envs.items():
            if not isinstance(env_data, dict):
                continue
            key = env_data.get("ssh_key_path", "~/.ssh/errander_prod")
            return env_name, str(key)
    except Exception:
        pass
    return "dev", "~/.ssh/errander_prod"


# ── Result handoff to bash ─────────────────────────────────────────────────────


def _write_result(env_name: str, ssh_key_path: str, vm_count: int) -> None:
    """Write result vars to ~/.errander_wizard_result for scripts/configure.sh to source."""
    result_path = Path.home() / ".errander_wizard_result"
    result_path.write_text(
        f"ERRANDER_RESULT_ENV_NAME={env_name}\n"
        f"ERRANDER_RESULT_SSH_KEY_PATH={ssh_key_path}\n"
        f"ERRANDER_RESULT_VM_COUNT={vm_count}\n",
        encoding="utf-8",
    )


# ── Schema validation ──────────────────────────────────────────────────────────


def _validate_rendered(yaml_str: str) -> list[str]:
    """Validate rendered YAML via InventoryConfig.  Returns list of error strings."""
    try:
        from errander.config.schema import InventoryConfig  # local import to avoid circulars
        data = yaml.safe_load(yaml_str)
        InventoryConfig.model_validate(data)
        return []
    except Exception as exc:
        return [str(exc)]


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    inventory_path = Path("inventory.yaml")
    generated_at = datetime.now(UTC).date().isoformat()

    # ── Handle existing inventory ──────────────────────────────────────────────
    if inventory_path.exists():
        summary = _summarise_existing(inventory_path)
        print()
        print(_hr("═"))
        print("  Errander-AI — Inventory Wizard")
        print(_hr("═"))
        print()
        print("  Existing inventory.yaml found:")
        print(f"    {summary}")
        print()
        print("  Options:")
        print("    1) Keep existing  (default — recommended)")
        print("    2) Replace — run wizard to create a new inventory from scratch")
        print()
        choice = input("  Choice [1/2, Enter=1]: ").strip()
        if choice != "2":
            _ok("Keeping existing inventory.yaml")
            env_name, ssh_key_path = _first_env_vars(inventory_path)
            # Count actual VMs so bash gets the real number for SSH bootstrap etc.
            vm_count = _count_vms(inventory_path)
            _write_result(env_name, ssh_key_path, vm_count)
            return

    # ── Run wizard ─────────────────────────────────────────────────────────────
    envs = _wizard_new_inventory()

    if not envs:
        _warn("No environments configured — inventory.yaml not written")
        _write_result("dev", "~/.ssh/errander_prod", 0)
        return

    # ── Render ─────────────────────────────────────────────────────────────────
    rendered = _render_inventory_yaml(envs, generated_at)

    # ── Validate ───────────────────────────────────────────────────────────────
    errors = _validate_rendered(rendered)
    if errors:
        _err("Generated inventory failed schema validation — please check your inputs:")
        for e in errors:
            print(f"      {e}")
        print()
        _warn("inventory.yaml NOT written.  Fix the errors and re-run scripts/configure.sh")
        sys.exit(1)

    # ── Write ──────────────────────────────────────────────────────────────────
    inventory_path.write_text(rendered, encoding="utf-8")

    total_vms = sum(len(e.targets) for e in envs)
    print()
    print(_hr("═"))
    env_word = "environment" if len(envs) == 1 else "environments"
    vm_word = "VM" if total_vms == 1 else "VMs"
    _ok(f"inventory.yaml written  ({len(envs)} {env_word}, {total_vms} {vm_word})")
    _ok("Config validated — no schema errors")
    print()
    print("  Next:")
    print("    • Run configure.sh (root) to probe Node Exporter on each VM")
    print("    • uv run python -m errander --check-inventory")
    print("    • uv run python -m errander --check-targets <env>")
    print(_hr("═"))

    # ── Handoff ────────────────────────────────────────────────────────────────
    first_env = envs[0]
    _write_result(first_env.name, first_env.ssh_key_path, total_vms)


if __name__ == "__main__":
    main()
