"""VM setup — called by configure.sh (root).

For each VM in inventory.yaml:
  1. Verify SSH connectivity.
  2. Node Exporter — check :9100; prompt to install if missing.
  3. Docker wrappers — check errander-docker-assess-v2; prompt to install if
     docker_hygiene is enabled for this VM and wrappers are missing.
  4. Service restart wrapper — check errander-systemctl-restart; prompt to
     install if service_restart is enabled with units for this VM.

Writes updated node_exporter values to inventory.yaml using ruamel.yaml so
that all existing comments and formatting are preserved.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from ruamel.yaml import YAML

_NE_VERSION = "1.8.2"
_NE_PORT_DEFAULT = 9100
_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"

# Systemd unit written to target VM.
_NE_UNIT = """\
[Unit]
Description=Prometheus Node Exporter
Documentation=https://github.com/prometheus/node_exporter
After=network.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

# Install script run on target VM via SSH.
# Uses curl; falls back gracefully if the binary already exists.
_INSTALL_SCRIPT = f"""\
set -e
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  GOARCH=amd64 ;;
    aarch64) GOARCH=arm64 ;;
    armv7l)  GOARCH=armv7 ;;
    *)       echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac
NE_VERSION={_NE_VERSION}
NE_TAR="node_exporter-${{NE_VERSION}}.linux-$GOARCH.tar.gz"
NE_URL="https://github.com/prometheus/node_exporter/releases/download/v${{NE_VERSION}}/$NE_TAR"

echo "  Downloading node_exporter $NE_VERSION ($GOARCH)..."
curl -fsSL "$NE_URL" -o /tmp/$NE_TAR
tar -xzf /tmp/$NE_TAR -C /tmp/
sudo mv -f /tmp/node_exporter-${{NE_VERSION}}.linux-$GOARCH/node_exporter /usr/local/bin/node_exporter
sudo chmod 755 /usr/local/bin/node_exporter
rm -rf /tmp/$NE_TAR /tmp/node_exporter-${{NE_VERSION}}.linux-$GOARCH

sudo useradd --system --no-create-home --shell /bin/false node_exporter 2>/dev/null || true

sudo tee /etc/systemd/system/node_exporter.service > /dev/null << 'UNIT'
{_NE_UNIT}UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now node_exporter
echo "  node_exporter $NE_VERSION started"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hr(char: str = "─", width: int = 60) -> str:
    return char * width


def _ok(msg: str) -> None:
    print(f"    \033[32m✓\033[0m  {msg}")


def _warn(msg: str) -> None:
    print(f"    \033[33m⚠\033[0m  {msg}")


def _err(msg: str) -> None:
    print(f"    \033[31m✗\033[0m  {msg}")


def _prompt(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"    {question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


async def _check_ne(hostname: str, port: int, timeout: float = 3.0) -> bool:
    """Return True if Node Exporter is responding on host:port."""
    url = f"http://{hostname}:{port}/metrics"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            return resp.status == 200
    except Exception:
        return False


async def _check_ssh(
    hostname: str,
    ssh_user: str,
    ssh_key_path: str,
    timeout: float = 8.0,
) -> bool:
    """Return True if SSH connection succeeds."""
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed — cannot check SSH. Run: uv sync")
        return False
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                hostname,
                username=ssh_user,
                client_keys=[ssh_key_path],
                known_hosts=None,
                password=None,
                connect_timeout=5,
            ),
            timeout=timeout,
        )
        conn.close()
        return True
    except Exception as exc:
        _err(f"SSH failed: {exc}")
        return False


async def _install_ne(
    hostname: str,
    ssh_user: str,
    ssh_key_path: str,
) -> bool:
    """Run the Node Exporter install script on target VM. Returns True on success."""
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not available — cannot install Node Exporter")
        return False

    print("    Installing Node Exporter...")
    try:
        async with await asyncssh.connect(
            hostname,
            username=ssh_user,
            client_keys=[ssh_key_path],
            known_hosts=None,
            password=None,
            connect_timeout=10,
        ) as conn:
            result = await conn.run(_INSTALL_SCRIPT, check=False)
            if result.stdout:
                stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode()
                for line in stdout.strip().splitlines():
                    print(f"      {line}")
            if result.exit_status != 0:
                if result.stderr:
                    stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
                    _err(stderr.strip())
                return False
    except Exception as exc:
        _err(f"Install failed: {exc}")
        return False

    # Give it a moment to start
    await asyncio.sleep(2)
    running = await _check_ne(hostname, _NE_PORT_DEFAULT)
    if running:
        _ok(f"Node Exporter {_NE_VERSION} installed and running on :{_NE_PORT_DEFAULT}")
        return True
    _err("Installed but :9100 still not responding — check firewall rules.")
    return False


def _decode(v: str | bytes | None) -> str:
    """Decode asyncssh stdout/stderr which may be str or bytes."""
    if v is None:
        return ""
    return v if isinstance(v, str) else v.decode()


# ---------------------------------------------------------------------------
# Docker wrapper helpers
# ---------------------------------------------------------------------------

async def _check_docker_wrappers(
    hostname: str, ssh_user: str, ssh_key_path: str
) -> bool:
    """Return True if errander-docker-assess-v2 wrapper is installed and responds."""
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
    """Upload and run install-docker-wrappers-v2.sh on the target VM via sudo."""
    script_path = _SCRIPTS_DIR / "install-docker-wrappers-v2.sh"
    if not script_path.exists():
        _err(f"Script not found: {script_path}")
        return False
    script = script_path.read_text(encoding="utf-8")
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed — cannot install docker wrappers")
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


# ---------------------------------------------------------------------------
# Service restart wrapper helpers
# ---------------------------------------------------------------------------

async def _check_restart_wrapper(
    hostname: str, ssh_user: str, ssh_key_path: str
) -> bool:
    """Return True if errander-systemctl-restart wrapper is installed and responds."""
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
    """Upload and run install-systemctl-restart-wrapper.sh with unit names."""
    script_path = _SCRIPTS_DIR / "install-systemctl-restart-wrapper.sh"
    if not script_path.exists():
        _err(f"Script not found: {script_path}")
        return False
    script = script_path.read_text(encoding="utf-8")
    units_arg = " ".join(units)
    try:
        import asyncssh
    except ImportError:
        _err("asyncssh not installed — cannot install restart wrapper")
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
# Per-VM configure flow
# ---------------------------------------------------------------------------

async def _configure_vm(
    env_name: str,
    target_dict: dict[str, Any],
    ne_port: int,
) -> bool | None:
    """Run the full setup flow for one VM target dict.

    Returns:
        True  → node_exporter: true
        False → node_exporter: false
        None  → SSH unreachable, skipped (no change)
    """
    name = target_dict.get("name", target_dict.get("host", "?"))
    host = target_dict.get("host", "")
    ssh_user = target_dict.get("_resolved_ssh_user", "")
    ssh_key = target_dict.get("_resolved_ssh_key", "")
    docker_enabled: bool = target_dict.get("_resolved_docker_enabled", False)
    restart_enabled: bool = target_dict.get("_resolved_restart_enabled", False)
    restart_units: list[str] = target_dict.get("_resolved_restart_units", [])

    print(f"\n  {_hr()}")
    print(f"  {env_name}/{name}  ({host})")
    print(f"  {_hr()}")

    # 1. SSH connectivity
    print("    Checking SSH connectivity...", end=" ", flush=True)
    ssh_ok = await _check_ssh(host, ssh_user, ssh_key)
    if not ssh_ok:
        print()
        _warn("Skipping — SSH unreachable. Fix connectivity and re-run configure.sh.")
        return None
    print("\033[32mOK\033[0m")

    # 2. Node Exporter
    print(f"    Checking Node Exporter (:{ne_port})...", end=" ", flush=True)
    ne_running = await _check_ne(host, ne_port)
    if ne_running:
        print("\033[32mFOUND\033[0m")
        _ok("Already running — no install needed.")
        ne_result: bool | None = True
    else:
        print("\033[33mNOT FOUND\033[0m")
        if _prompt(f"Install Node Exporter {_NE_VERSION} on {name}?", default=True):
            success = await _install_ne(host, ssh_user, ssh_key)
            ne_result = success
        else:
            _warn("Skipping — SSH probe will be used for metrics.")
            ne_result = False

    # 3. Docker wrappers
    if docker_enabled:
        print("    Checking docker wrappers...", end=" ", flush=True)
        wrappers_ok = await _check_docker_wrappers(host, ssh_user, ssh_key)
        if wrappers_ok:
            print("\033[32mFOUND\033[0m")
            _ok("Docker wrappers already installed.")
        else:
            print("\033[33mNOT FOUND\033[0m")
            if _prompt(f"Install docker wrappers on {name}?", default=True):
                await _install_docker_wrappers(host, ssh_user, ssh_key)
            else:
                _warn("Skipping — docker_hygiene will not function until wrappers are installed.")

    # 4. Service restart wrapper
    if restart_enabled and restart_units:
        print("    Checking service restart wrapper...", end=" ", flush=True)
        restart_ok = await _check_restart_wrapper(host, ssh_user, ssh_key)
        if restart_ok:
            print("\033[32mFOUND\033[0m")
            _ok("Service restart wrapper already installed.")
        else:
            print("\033[33mNOT FOUND\033[0m")
            units_display = ", ".join(restart_units)
            if _prompt(
                f"Install service restart wrapper on {name} (units: {units_display})?",
                default=True,
            ):
                await _install_restart_wrapper(host, ssh_user, ssh_key, restart_units)
            else:
                _warn("Skipping — service_restart will not function until wrapper is installed.")

    return ne_result


# ---------------------------------------------------------------------------
# Inventory YAML update
# ---------------------------------------------------------------------------

def _update_inventory_yaml(
    inventory_path: Path,
    results: dict[str, dict[str, bool | None]],
) -> None:
    """Write node_exporter: true/false into each target in inventory.yaml.

    results: {env_name: {vm_name: True/False/None}}
    None entries (SSH unreachable) are left unchanged.

    Uses ruamel.yaml for a comment-preserving round-trip so that any
    documentation comments in the file survive the update.
    """
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # prevent unwanted line wrapping

    with open(inventory_path, encoding="utf-8") as fh:
        data: Any = ryaml.load(fh)

    for env_name, env_data in (data.get("environments") or {}).items():
        env_results = results.get(env_name, {})
        for target in (env_data.get("targets") or []):
            vm_name = target.get("name", target.get("host", ""))
            result = env_results.get(vm_name)
            if result is None:
                continue  # SSH unreachable — leave unchanged
            target["node_exporter"] = result

    with open(inventory_path, "w", encoding="utf-8") as fh:
        ryaml.dump(data, fh)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def _main(inventory_path: Path) -> None:
    ne_port = int(os.environ.get("ERRANDER_NODE_EXPORTER_PORT", str(_NE_PORT_DEFAULT)))

    print()
    print(_hr("═"))
    print("  Errander-AI — VM Setup")
    print(_hr("═"))
    print()
    print("  For each VM, this step will (with your confirmation):")
    print(f"    1. Check Node Exporter (:{ne_port}) — install if missing")
    print("    2. Check docker wrappers    — install if docker_hygiene is enabled")
    print("    3. Check restart wrapper    — install if service_restart is enabled")
    print()
    print(f"  Inventory: {inventory_path}")
    print()

    raw = inventory_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    environments: dict[str, Any] = data.get("environments", {})

    if not environments:
        print("  No environments found in inventory.yaml — nothing to configure.")
        sys.exit(1)

    # Collect env-level SSH defaults (needed for targets that don't override)
    results: dict[str, dict[str, bool | None]] = {}

    for env_name, env_data in environments.items():
        env_ssh_user = env_data.get("ssh_user", "errander")
        env_ssh_key = str(Path(env_data.get("ssh_key_path", "~/.ssh/errander")).expanduser())
        targets = env_data.get("targets", [])

        print(f"\n  Environment: {env_name.upper()}  ({len(targets)} VM{'s' if len(targets) != 1 else ''})")
        results[env_name] = {}

        # Env-level action defaults
        env_actions: dict[str, Any] = env_data.get("actions") or {}
        env_docker = (env_actions.get("docker_hygiene") or {}).get("enabled", False)
        env_restart = env_actions.get("service_restart") or {}

        for target in targets:
            # Inject resolved SSH creds
            target["_resolved_ssh_user"] = target.get("ssh_user") or env_ssh_user
            target["_resolved_ssh_key"] = str(
                Path(target.get("ssh_key_path") or env_ssh_key).expanduser()
            )
            # Resolve docker_hygiene — target override takes precedence over env
            t_actions: dict[str, Any] = target.get("actions") or {}
            t_docker = t_actions.get("docker_hygiene") or {}
            docker_enabled = t_docker.get("enabled", env_docker)
            target["_resolved_docker_enabled"] = bool(docker_enabled)

            # Resolve service_restart — target override takes precedence over env
            t_restart = t_actions.get("service_restart") or {}
            restart_enabled = t_restart.get("enabled", env_restart.get("enabled", False))
            restart_units: list[str] = (
                t_restart.get("restartable_units")
                or env_restart.get("restartable_units")
                or []
            )
            target["_resolved_restart_enabled"] = bool(restart_enabled)
            target["_resolved_restart_units"] = restart_units

            vm_name = target.get("name", target.get("host", "?"))
            result = await _configure_vm(env_name, target, ne_port)
            results[env_name][vm_name] = result

    # Write updated inventory
    print(f"\n\n  {_hr('═')}")
    print("  Summary")
    print(f"  {_hr('═')}")
    ne_count = ssh_count = skip_count = 0
    for env_name, env_results in results.items():
        for vm_name, result in env_results.items():
            if result is True:
                ne_count += 1
                print(f"    \033[32m✓\033[0m  {env_name}/{vm_name} → Node Exporter")
            elif result is False:
                ssh_count += 1
                print(f"    \033[33m~\033[0m  {env_name}/{vm_name} → SSH probe")
            else:
                skip_count += 1
                print(f"    \033[31m✗\033[0m  {env_name}/{vm_name} → skipped (unreachable)")

    _update_inventory_yaml(inventory_path, results)
    print(f"\n  inventory.yaml updated — {ne_count} Node Exporter, {ssh_count} SSH probe, {skip_count} skipped.")
    print("  Start Errander: uv run python -m errander\n")


def main() -> None:
    """Entry point called by configure.sh."""
    inventory_path = Path("inventory.yaml")
    if not inventory_path.exists():
        print("\n  \033[31mError:\033[0m inventory.yaml not found.")
        print("  Copy the example first:")
        print("    cp example/inventory.yaml inventory.yaml")
        print("  Then edit it with your VM hostnames and SSH keys.\n")
        sys.exit(1)

    asyncio.run(_main(inventory_path))


if __name__ == "__main__":
    main()
