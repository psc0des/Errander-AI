"""add_target.py — Add one or more VMs to an existing inventory.yaml.

Run via scripts/add-target.sh instead of re-running the full configure.sh
wizard when you only need to add new target VMs. Leaves .env untouched.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m  {msg}")


def _warn(msg: str) -> None:
    print(f"  \033[33m▶\033[0m  {msg}")


def _err(msg: str) -> None:
    print(f"  \033[31m✗\033[0m  {msg}")


def _hr(char: str = "─", width: int = 55) -> str:
    return char * width


def _prompt_val(label: str, default: str = "") -> str:
    """Prompt for a value; re-prompt if empty and no default."""
    if default:
        raw = input(f"    {label} [{default}]: ").strip()
        return raw if raw else default
    while True:
        raw = input(f"    {label}: ").strip()
        if raw:
            return raw
        print("    (required — cannot be empty)")


def _prompt_yn(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Inventory I/O
# ---------------------------------------------------------------------------

def _load_inventory(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(text)
    if not isinstance(data, dict) or "environments" not in data:
        raise ValueError("inventory.yaml must have a top-level 'environments:' key")
    return data


def _save_inventory(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# SSH verification (optional, non-blocking)
# ---------------------------------------------------------------------------

async def _check_ssh(
    hostname: str,
    ssh_user: str,
    ssh_key_path: str,
    timeout: float = 10.0,
) -> bool:
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed — cannot verify SSH. Run: uv sync")
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
    except Exception as exc:
        _err(f"SSH failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main interactive flow
# ---------------------------------------------------------------------------

async def _main(inventory_path: Path) -> None:
    print()
    print(_hr("═"))
    print("  Errander-AI — Add Target VMs")
    print(_hr("═"))
    print()
    print("  Adds new VMs to an existing environment in inventory.yaml.")
    print("  Your .env and all other settings remain unchanged.")
    print()

    data = _load_inventory(inventory_path)
    environments: dict[str, Any] = data["environments"]
    env_names = list(environments.keys())

    # Show current state
    print(f"  Inventory: {inventory_path}\n")
    if env_names:
        for i, env_name in enumerate(env_names, 1):
            env = environments[env_name]
            targets = env.get("targets", [])
            print(f"  [{i}] {env_name}  ({len(targets)} VM{'s' if len(targets) != 1 else ''})")
            for t in targets:
                print(f"       - {t.get('name', t.get('host', '?'))}  ({t.get('host', '?')})")
        print(f"  [n] New environment")
    else:
        print("  (no environments yet — you will create the first one)")
    print()

    # Choose environment
    new_env = False
    chosen_env = ""
    while True:
        if env_names:
            raw = input(
                f"  Which environment to add to? (1–{len(env_names)}, name, or n for new): "
            ).strip()
        else:
            raw = "n"
        if not raw:
            continue
        if raw.lower() == "n":
            new_env = True
            break
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(env_names):
                chosen_env = env_names[idx]
                break
            print(f"  Enter a number between 1 and {len(env_names)}, or 'n'")
        elif raw in environments:
            chosen_env = raw
            break
        else:
            print(f"  '{raw}' not found. Options: {', '.join(env_names)}, n")

    if new_env:
        print()
        print("  Creating new environment")
        print()
        chosen_env = _prompt_val("Environment name")
        if chosen_env in environments:
            _err(f"Environment '{chosen_env}' already exists — select it above instead.")
            sys.exit(1)
        ssh_user      = _prompt_val("SSH user on target VMs", "errander")
        ssh_key_raw   = _prompt_val("SSH key path", "~/.ssh/errander_prod")
        ssh_key_path  = str(Path(ssh_key_raw).expanduser())
        approval      = _prompt_val("Approval policy  (relaxed / moderate / strict)", "relaxed")
        maint_win     = _prompt_val("Maintenance window  (HH:MM-HH:MM)", "08:00-20:00")
        maint_days_raw = _prompt_val(
            "Maintenance days  (comma-separated)",
            "monday,tuesday,wednesday,thursday,friday",
        )
        maint_days = [d.strip() for d in maint_days_raw.split(",") if d.strip()]
        maint_tz   = _prompt_val("Maintenance timezone", "UTC")

        env_data: dict[str, Any] = {
            "ssh_user":             ssh_user,
            "ssh_key_path":         ssh_key_raw,   # store tilde form, not expanded
            "approval_policy":      approval,
            "maintenance_window":   maint_win,
            "maintenance_days":     maint_days,
            "maintenance_timezone": maint_tz,
            "targets":              [],
        }
        environments[chosen_env] = env_data
        existing_targets: list[dict[str, Any]] = []
    else:
        env_data = environments[chosen_env]
        ssh_user = env_data.get("ssh_user", "errander")
        ssh_key_path = str(
            Path(env_data.get("ssh_key_path", "~/.ssh/errander_prod")).expanduser()
        )
        existing_targets = env_data.get("targets", [])

    print()
    print(f"  Environment : {chosen_env}")
    print(f"  SSH user    : {ssh_user}")
    print(f"  SSH key     : {ssh_key_path}")
    if not new_env:
        print(f"  Existing VMs: {len(existing_targets)}")
    print()

    new_targets: list[dict[str, Any]] = []

    while True:
        vm_num = len(existing_targets) + len(new_targets) + 1
        print(_hr())
        print(f"  New VM #{len(new_targets) + 1}")
        print()

        host = _prompt_val("VM hostname or private IP")
        default_name = f"{chosen_env}-vm-{vm_num:02d}"
        name = _prompt_val("VM name", default_name)
        os_family = _prompt_val("OS family  (ubuntu / debian / rhel)", "ubuntu")
        print()

        # Optional SSH verification
        if _prompt_yn(f"Verify SSH connectivity to {host} now?"):
            print(f"    Connecting as {ssh_user}@{host} ...")
            ssh_ok = await _check_ssh(host, ssh_user, ssh_key_path)
            if ssh_ok:
                _ok("SSH connectivity verified")
            else:
                _warn("SSH check failed — VM will still be added to inventory.")
                _warn("Complete SETUP.md Steps 2–3 on the new VM before running the agent.")
        print()

        new_targets.append({"host": host, "name": name, "os_family": os_family})
        _ok(f"Queued: {name}  ({host}, {os_family})")
        print()

        if not _prompt_yn("Add another VM to this environment?", default=False):
            break
        print()

    # Append and save
    if "targets" not in env_data:
        env_data["targets"] = []
    env_data["targets"].extend(new_targets)
    data["environments"][chosen_env] = env_data

    _save_inventory(inventory_path, data)

    print(_hr("═"))
    _ok(f"inventory.yaml updated — {len(new_targets)} VM(s) added to '{chosen_env}'")
    print()
    print("  Next steps for each new VM:")
    print()
    print("  1. SSH user setup (SETUP.md Step 2):")
    print("       sudo useradd -m -s /bin/bash errander")
    print("       # install ~/.ssh/authorized_keys with errander_prod.pub")
    print()
    print("  2. Sudo permissions (SETUP.md Step 3):")
    print("       sudo tee /etc/sudoers.d/errander  # see SETUP.md")
    print()
    print("  3. If using Docker hygiene or service restart — install wrappers.")
    print()
    print("  4. Verify:")
    print(f"       uv run python -m errander --check-targets {chosen_env}")
    print()
    print("  5. Pin SSH host key for the new VM:")
    print(f"       uv run python -m errander --bootstrap-known-hosts {chosen_env}")
    print()
    print(_hr("═"))
    print()


def main() -> None:
    """Entry point called by scripts/add-target.sh."""
    inventory_path = Path("inventory.yaml")
    if not inventory_path.exists():
        print("\n  \033[31mError:\033[0m inventory.yaml not found.")
        print("  Run configure.sh first to create the initial inventory.")
        print("  Or copy the example: cp example/inventory.yaml inventory.yaml\n")
        sys.exit(1)

    asyncio.run(_main(inventory_path))


if __name__ == "__main__":
    main()
