"""add_target.py — Add one or more VMs to an existing inventory.yaml.

Run via scripts/add-target.sh instead of re-running the full configure.sh
wizard when you only need to add new target VMs. Leaves .env untouched.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from errander.config._prompts import (
    prompt_maintenance_days,
    prompt_maintenance_window,
    prompt_name,
    prompt_os_family,
    prompt_policy,
    prompt_systemd_units,
    prompt_timezone,
    prompt_val,
    prompt_yn,
)

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


# ---------------------------------------------------------------------------
# Inventory I/O
# ---------------------------------------------------------------------------

def _load_inventory(path: Path) -> Any:
    """Load inventory using ruamel.yaml to preserve existing comments."""
    ryaml = YAML()
    ryaml.preserve_quotes = True
    with open(path, encoding="utf-8") as fh:
        data = ryaml.load(fh)
    if not isinstance(data, dict) or "environments" not in data:
        raise ValueError("inventory.yaml must have a top-level 'environments:' key")
    return data


def _save_inventory(path: Path, data: Any) -> None:
    """Save inventory using ruamel.yaml so existing comments are preserved."""
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096
    with open(path, "w", encoding="utf-8") as fh:
        ryaml.dump(data, fh)


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
# Wrapper check / install helpers
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _decode(v: str | bytes | None) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else v.decode()


async def _check_ne(hostname: str, port: int = 9100, timeout: float = 3.0) -> bool:
    """Return True if Node Exporter responds on host:port."""
    import aiohttp
    url = f"http://{hostname}:{port}/metrics"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            return resp.status == 200
    except Exception:
        return False


async def _install_ne(hostname: str, ssh_user: str, ssh_key_path: str) -> bool:
    """Install Node Exporter on target via SSH. Returns True on success."""
    from errander.config.configure import _NE_VERSION
    from errander.config.configure import _install_ne as _conf_install_ne
    _ok(f"Installing Node Exporter {_NE_VERSION}...")
    result: bool = await _conf_install_ne(hostname, ssh_user, ssh_key_path)
    return result


async def _check_docker_wrappers(
    hostname: str, ssh_user: str, ssh_key_path: str
) -> bool:
    try:
        import asyncssh
    except ImportError:
        return False
    try:
        async with await asyncssh.connect(
            hostname, username=ssh_user, client_keys=[ssh_key_path],
            known_hosts=None, password=None, connect_timeout=8,
        ) as conn:
            result = await conn.run(
                "sudo /usr/local/sbin/errander-docker-assess-v2 --check 2>/dev/null",
                check=False,
            )
            return result.exit_status == 0 and "ok" in _decode(result.stdout)
    except Exception:
        return False
    return False  # unreachable; satisfies mypy


async def _install_docker_wrappers(
    hostname: str, ssh_user: str, ssh_key_path: str
) -> bool:
    script_path = _SCRIPTS_DIR / "install-docker-wrappers-v2.sh"
    if not script_path.exists():
        _err(f"Script not found: {script_path}")
        return False
    script = script_path.read_text(encoding="utf-8")
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed")
        return False
    print("    Installing docker wrappers...")
    try:
        async with await asyncssh.connect(
            hostname, username=ssh_user, client_keys=[ssh_key_path],
            known_hosts=None, password=None, connect_timeout=10,
        ) as conn:
            result = await conn.run("sudo bash -s", input=script, check=False)
            stdout = _decode(result.stdout)
            if stdout:
                for line in stdout.strip().splitlines():
                    print(f"      {line}")
            if result.exit_status != 0:
                stderr = _decode(result.stderr)
                if stderr:
                    _err(stderr.strip())
                return False
    except Exception as exc:
        _err(f"Docker wrapper install failed: {exc}")
        return False
    ok = await _check_docker_wrappers(hostname, ssh_user, ssh_key_path)
    if ok:
        _ok("Docker wrappers installed and responding.")
    else:
        _err("Installed but --check still failing — check sudo permissions.")
    return ok


async def _check_restart_wrapper(
    hostname: str, ssh_user: str, ssh_key_path: str
) -> bool:
    try:
        import asyncssh
    except ImportError:
        return False
    try:
        async with await asyncssh.connect(
            hostname, username=ssh_user, client_keys=[ssh_key_path],
            known_hosts=None, password=None, connect_timeout=8,
        ) as conn:
            result = await conn.run(
                "sudo /usr/local/sbin/errander-systemctl-restart --check 2>/dev/null",
                check=False,
            )
            return result.exit_status == 0 and "ok" in _decode(result.stdout)
    except Exception:
        return False
    return False  # unreachable; satisfies mypy


async def _install_restart_wrapper(
    hostname: str, ssh_user: str, ssh_key_path: str, units: list[str]
) -> bool:
    script_path = _SCRIPTS_DIR / "install-systemctl-restart-wrapper.sh"
    if not script_path.exists():
        _err(f"Script not found: {script_path}")
        return False
    script = script_path.read_text(encoding="utf-8")
    units_arg = " ".join(units)
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed")
        return False
    print(f"    Installing service restart wrapper ({units_arg})...")
    try:
        async with await asyncssh.connect(
            hostname, username=ssh_user, client_keys=[ssh_key_path],
            known_hosts=None, password=None, connect_timeout=10,
        ) as conn:
            result = await conn.run(
                f"sudo bash -s {units_arg}", input=script, check=False
            )
            stdout = _decode(result.stdout)
            if stdout:
                for line in stdout.strip().splitlines():
                    print(f"      {line}")
            if result.exit_status != 0:
                stderr = _decode(result.stderr)
                if stderr:
                    _err(stderr.strip())
                return False
    except Exception as exc:
        _err(f"Restart wrapper install failed: {exc}")
        return False
    ok = await _check_restart_wrapper(hostname, ssh_user, ssh_key_path)
    if ok:
        _ok(f"Service restart wrapper installed for: {units_arg}")
    else:
        _err("Installed but --check still failing — check sudo permissions.")
    return ok


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
        print("  [n] New environment")
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
        chosen_env = prompt_name("Environment name")
        if chosen_env in environments:
            _err(f"Environment '{chosen_env}' already exists — select it above instead.")
            sys.exit(1)
        ssh_user      = prompt_val("SSH user on target VMs", "errander")
        ssh_key_raw   = prompt_val("SSH key path", "~/.ssh/errander_prod")
        ssh_key_path  = str(Path(ssh_key_raw).expanduser())
        approval      = prompt_policy(indent=2)
        maint_win     = prompt_maintenance_window("08:00-20:00")
        maint_days    = prompt_maintenance_days("monday,tuesday,wednesday,thursday,friday")
        maint_tz      = prompt_timezone()

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

        host = prompt_val("VM hostname or private IP")
        default_name = f"{chosen_env}-vm-{vm_num:02d}"
        name = prompt_name("VM name", default_name)

        os_family = prompt_os_family()
        print()

        # Optional SSH verification
        if prompt_yn(f"Verify SSH connectivity to {host} now?", indent=2):
            print(f"    Connecting as {ssh_user}@{host} ...")
            ssh_ok = await _check_ssh(host, ssh_user, ssh_key_path)
            if ssh_ok:
                _ok("SSH connectivity verified")
            else:
                _warn("SSH check failed — VM will still be added to inventory.")
                _warn("Complete SETUP.md Steps 2–3 on the new VM before running the agent.")
        print()

        # Docker hygiene — only ask when docker_hygiene is enabled in this env
        env_actions: Any = env_data.get("actions", {}) or {}
        env_docker = (env_actions.get("docker_hygiene") or {}).get("enabled", False)
        target_overrides: dict[str, Any] = {}

        if env_docker:
            has_docker = prompt_yn("Is Docker installed on this VM?", default=True, indent=2)
            if not has_docker:
                target_overrides["docker_hygiene"] = {"enabled": False}

        # Service restart — collect unit names now; wrapper installed below
        print()
        print("  service_restart — lets operators restart specific systemd units via Errander.")
        if prompt_yn("Will this VM need operator-triggered service restarts?", default=False, indent=2):
            units = prompt_systemd_units(indent=2)
            target_overrides["service_restart"] = {
                "enabled": True,
                "restartable_units": units,
            }

        target: dict[str, Any] = {
            "host": host,
            "name": name,
            "os_family": os_family,
            "node_exporter": False,
        }
        if target_overrides:
            target["actions"] = target_overrides

        new_targets.append(target)
        _ok(f"Queued: {name}  ({host}, {os_family})")
        print()

        if not prompt_yn("Add another VM to this environment?", default=False, indent=2):
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

    # ── Per-VM software install ───────────────────────────────────────────────
    print()
    print("  Software setup for each new VM (SSH connectivity required)")
    print()

    for target in new_targets:
        t_host: str = target["host"]
        t_name: str = target["name"]
        t_actions: dict[str, Any] = target.get("actions") or {}

        # Resolve docker: env-level enabled, target override may disable
        t_docker_override = (t_actions.get("docker_hygiene") or {}).get("enabled")
        docker_enabled = t_docker_override if t_docker_override is not None else env_docker

        # Resolve restart units
        t_restart = t_actions.get("service_restart") or {}
        restart_units: list[str] = t_restart.get("restartable_units") or []
        restart_enabled: bool = bool(t_restart.get("enabled", False)) and bool(restart_units)

        print(f"  {_hr('─', 48)}")
        print(f"  {t_name}  ({t_host})")
        print()

        # Node Exporter
        print(f"    Checking Node Exporter ({t_host}:9100)...", end=" ", flush=True)
        ne_found = await _check_ne(t_host)
        if ne_found:
            print("\033[32mFOUND\033[0m")
            _ok("Already running.")
        else:
            print("\033[33mNOT FOUND\033[0m")
            if prompt_yn(f"Install Node Exporter on {t_name}?", default=True, indent=2):
                await _install_ne(t_host, ssh_user, ssh_key_path)
            else:
                _warn("Skipping — SSH probe will be used for metrics.")

        # Docker wrappers
        if docker_enabled:
            print("    Checking docker wrappers...", end=" ", flush=True)
            dw_found = await _check_docker_wrappers(t_host, ssh_user, ssh_key_path)
            if dw_found:
                print("\033[32mFOUND\033[0m")
                _ok("Docker wrappers already installed.")
            else:
                print("\033[33mNOT FOUND\033[0m")
                if prompt_yn(f"Install docker wrappers on {t_name}?", default=True, indent=2):
                    await _install_docker_wrappers(t_host, ssh_user, ssh_key_path)
                else:
                    _warn("Skipping — docker_hygiene will not work until wrappers are installed.")

        # Service restart wrapper
        if restart_enabled:
            print("    Checking service restart wrapper...", end=" ", flush=True)
            rw_found = await _check_restart_wrapper(t_host, ssh_user, ssh_key_path)
            if rw_found:
                print("\033[32mFOUND\033[0m")
                _ok("Service restart wrapper already installed.")
            else:
                print("\033[33mNOT FOUND\033[0m")
                units_display = ", ".join(restart_units)
                if prompt_yn(
                    f"Install service restart wrapper on {t_name} (units: {units_display})?",
                    default=True,
                    indent=2,
                ):
                    await _install_restart_wrapper(t_host, ssh_user, ssh_key_path, restart_units)
                else:
                    _warn("Skipping — service_restart will not work until wrapper is installed.")

        print()

    print(_hr("═"))
    print()
    print("  Remaining steps for each new VM:")
    print()
    print("  1. SSH user setup (SETUP.md Step 2):")
    print("       sudo useradd -m -s /bin/bash errander")
    print("       # install ~/.ssh/authorized_keys with errander_prod.pub")
    print()
    print("  2. Sudo permissions (SETUP.md Step 3):")
    print("       sudo tee /etc/sudoers.d/errander  # see SETUP.md")
    print()
    print("  3. Verify:")
    print(f"       uv run python -m errander --check-targets {chosen_env}")
    print()
    print("  4. Pin SSH host key:")
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
