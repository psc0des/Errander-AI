# Phase A — Privilege Model Implementation Plan (for Sonnet)

> Author: Opus (planning) → Sonnet (execution)
> Date: 2026-05-15
> SRE sign-off: see `ai_sre_audit_v2.md` "Two-Layer AI Architecture Validation"
> Positioning docs already landed (Commit 0). This document covers Commits 1–3.

---

## Pre-flight before you start

1. Read `docs/AI-ARCHITECTURE.md` end to end. Internalize the two-layer model. Every change in this plan is Layer B (deterministic execution path). No LLM, MCP, CLI, or Skills calls in any code you write here.
2. Read the latest section of `ai_sre_audit_v2.md` — "Two-Layer AI Architecture Validation" — for the SRE's anchor phrases. Both phrases must appear verbatim in `docs/AI-ARCHITECTURE.md` (already there) and in `CLAUDE.md` (already there).
3. Run the existing test suite once before touching anything to confirm a clean baseline: `uv run pytest`. Expected: 1310+ passing, 111 skipped.
4. Confirm git state is clean: `git status`. Branch should be `main`.

If any of the above fails, stop and report instead of proceeding.

---

## Working principles

- **Scope discipline**: This plan covers privilege model fixes only. Do NOT touch unrelated files. Do NOT do a full ruff/mypy sweep. Do NOT redesign anything.
- **Opportunistic cleanup only**: While editing each privilege file, fix ruff/mypy errors *in that file*. Do not chase the whole repo.
- **One commit per major chunk**: Commit 1 → Commit 2 → Commit 3. Each must be independently shippable. Do not bundle.
- **Tests prove the fix**: Every behavior change gets a test. Refactor without a test = not done.
- **Use existing patterns**: `privileged()` helper exists in `errander/execution/privilege.py`. Use it. Don't write parallel helpers.
- **Don't reformat for the sake of reformatting**: minimize the diff for review.

---

## Commit 1 — Quick privilege fixes

### Goal

Close the four remaining gaps from the previous SRE round:
1. Remove `/usr/bin/env` from privileged apt commands (it's not in sudoers — would fail in prod).
2. Drop sudo from `apt-get --simulate` (dry-run should avoid privilege escalation where possible).
3. Add a proper `SUDO_PREFLIGHT_FAILED` event type (currently misuses `PREFLIGHT_LOCK_DETECTED`).
4. Add the missing preflight behavior tests.

### Files and changes

#### 1.1 — `errander/safety/rollback.py` — `_rollback_patching_apt`

Find the existing rollback command construction. Current state should be similar to:

```python
rollback_cmd = privileged(
    "/usr/bin/env DEBIAN_FRONTEND=noninteractive "
    "/usr/bin/apt-get install -y --allow-downgrades "
    + " ".join(install_specs)
)
```

Change to:

```python
rollback_cmd = privileged(
    "/usr/bin/apt-get install -y "
    "-o Dpkg::Options::=--force-confdef "
    "-o Dpkg::Options::=--force-confold "
    "--allow-downgrades "
    + " ".join(install_specs)
)
```

Rationale:
- `sudo -n /usr/bin/env <anything>` requires `/usr/bin/env` to be in sudoers, which makes sudo trivially bypassable (`sudo env rm -rf /`). We never want that. Removing it.
- `apt-get -y` without a controlling TTY is already noninteractive in practice. The `-o Dpkg::Options::=` flags handle config-file prompts deterministically.

#### 1.2 — `errander/execution/commands.py` — `AptManager.install_version`

Same pattern. Find the current implementation (it currently uses `privileged("/usr/bin/env DEBIAN_FRONTEND=noninteractive ...")` or similar).

Change to:

```python
def install_version(self, package: str, version: str) -> str:
    return privileged(
        "/usr/bin/apt-get install -y "
        "-o Dpkg::Options::=--force-confdef "
        "-o Dpkg::Options::=--force-confold "
        "--allow-downgrades "
        f"{safe_pkg(package)}={safe_ver(version)}"
    )
```

#### 1.3 — `errander/execution/commands.py` — `AptManager.simulate_upgrade`

Find:
```python
def simulate_upgrade(self) -> str:
    return privileged("/usr/bin/apt-get --simulate upgrade")
```

Change to:
```python
def simulate_upgrade(self) -> str:
    # Dry-run simulation does not modify state; runs unprivileged.
    return "apt-get --simulate upgrade"
```

#### 1.4 — `errander/models/events.py` — add `SUDO_PREFLIGHT_FAILED`

Find the `EventType` enum. Add:

```python
SUDO_PREFLIGHT_FAILED = "sudo_preflight_failed"
```

Keep alphabetical or grouped per existing convention — match the surrounding style.

#### 1.5 — `errander/agent/vm_graph.py` — `sudo_preflight_node`

Find the two places `EventType.PREFLIGHT_LOCK_DETECTED` is used inside `sudo_preflight_node`. Replace both with `EventType.SUDO_PREFLIGHT_FAILED`. Do NOT change the use of `PREFLIGHT_LOCK_DETECTED` in `patching.py` — that's the correct event for its context.

### New test file — `tests/agent/test_sudo_preflight.py`

Tests required:

```python
"""Behavior tests for sudo_preflight_node and routing.

Per ai_sre_audit_v2.md Phase A.4 — the SRE explicitly asked for a test that
proves missing sudo causes a failed action, not silent success.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

# Import targets — adjust if module paths differ
from errander.agent.vm_graph import sudo_preflight_node, route_after_sudo_preflight
from errander.models.events import EventType


# --- Routing tests ---

def test_route_after_preflight_routes_to_audit_on_error():
    assert route_after_sudo_preflight({"error": "anything"}) == "audit_results"


def test_route_after_preflight_routes_to_dispatch_when_clear():
    assert route_after_sudo_preflight({}) == "dispatch_action"


# --- Behavior tests ---

@pytest.mark.asyncio
async def test_dry_run_skips_preflight():
    # State has dry_run=True — node should return {} without calling SSH
    state = {"dry_run": True, "vm_id": "v1", "planned_actions": [{"action_type": "patching"}]}
    executor = MagicMock()
    executor.execute = AsyncMock()
    result = await sudo_preflight_node(state, executor=executor)
    assert result == {}
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_passes_when_all_ok():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    # Mock executor returns SUDO_OK for the queried binary
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" not in result
    executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_fails_closed_when_binary_fails():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_FAIL /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" in result
    assert "journalctl" in result["error"]


@pytest.mark.asyncio
async def test_preflight_fails_on_ssh_error():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=False, stdout="", stderr="SSH connection refused")
    executor.execute = AsyncMock(return_value=mock_result)
    result = await sudo_preflight_node(state, executor=executor)
    assert "error" in result


@pytest.mark.asyncio
async def test_preflight_emits_sudo_preflight_failed_event():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "batch_id": "b1",
        "planned_actions": [{"action_type": "disk_cleanup"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_FAIL /usr/bin/journalctl\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()

    await sudo_preflight_node(state, executor=executor, audit_store=audit_store)

    audit_store.log_event.assert_awaited_once()
    logged_event = audit_store.log_event.await_args.args[0]
    assert logged_event.event_type == EventType.SUDO_PREFLIGHT_FAILED


# --- Regression / contract tests ---

def test_no_env_in_privileged_apt_commands():
    """SRE-explicit: /usr/bin/env must not appear in privileged apt commands."""
    from errander.execution.commands import AptManager
    apt = AptManager()
    install_cmd = apt.install_version("nginx", "1.18.0-0ubuntu1")
    assert "/usr/bin/env" not in install_cmd
    assert "DEBIAN_FRONTEND" not in install_cmd


def test_apt_simulate_no_sudo():
    """Dry-run simulate command should not require sudo escalation."""
    from errander.execution.commands import AptManager
    apt = AptManager()
    cmd = apt.simulate_upgrade()
    assert "sudo" not in cmd
```

Adjust imports if any module paths differ. Use existing test fixtures where present in `tests/conftest.py`.

### Tests that should keep passing

Run `uv run pytest tests/execution/test_commands_sudo.py tests/safety/test_rollback_sudo.py` after the edit. The existing assertions about sudo prefix may need updating to match the new `-o Dpkg::Options::=...` flags. Update assertions as needed but keep the spirit (sudo present, /usr/bin/apt-get present, --allow-downgrades present, no /usr/bin/env).

### Acceptance criteria

- `uv run pytest` — all tests pass
- `grep -r "/usr/bin/env" errander/safety/ errander/execution/` — no matches in privileged contexts
- `grep "PREFLIGHT_LOCK_DETECTED" errander/agent/vm_graph.py` inside `sudo_preflight_node` body — 0 matches
- New test file `tests/agent/test_sudo_preflight.py` exists with all the tests above passing
- Opportunistic ruff/mypy cleanup applied only to: `rollback.py`, `commands.py`, `vm_graph.py`, `models/events.py`

### Commit message

```
fix: close fifth-pass SRE residuals — env removal, simulate sudo, preflight event type
```

---

## Commit 2 — Docker wrapper mode

### Goal

Implement the SRE's hardening direction for Docker: wrapper scripts as production default, raw `sudo docker` as explicit lab/pre-prod override, disabled as a third option. Make the preflight aware of mode.

### Files and changes

#### 2.1 — `errander/config/schema.py` — `EnvironmentSchema`

Add field:
```python
from typing import Literal

class EnvironmentSchema(BaseModel):
    ...existing fields...
    docker_command_mode: Literal["wrapper", "direct_sudo", "disabled"] = "wrapper"
```

If the schema uses Pydantic v2 style with `Field(...)`, mirror that style. Default value must be `"wrapper"`.

#### 2.2 — `example/inventory.yaml`

Find the existing environments block. Add a commented example showing all three values:

```yaml
environments:
  prod:
    # docker_command_mode controls how Errander invokes Docker on this env's VMs.
    # - "wrapper" (default, production): uses root-owned wrapper scripts at
    #     /usr/local/sbin/errander-docker-{assess,prune-safe,prune-aggressive}.
    #     This is the secure mode — narrow sudoers, no raw `sudo docker`.
    # - "direct_sudo": uses `sudo -n /usr/bin/docker ...` directly. Lab/pre-prod
    #     only. Logs a warning every batch. Not enterprise-hardened.
    # - "disabled": Errander will not plan or execute docker_prune on this env.
    docker_command_mode: wrapper
    ...
```

#### 2.3 — `errander/agent/subgraphs/docker_prune.py` — refactor for mode

Add to the state TypedDict:
```python
class DockerPruneGraphState(TypedDict, total=False):
    ...existing fields...
    docker_command_mode: str  # "wrapper" | "direct_sudo" | "disabled"
```

Refactor `validate_node`:
```python
async def validate_node(state: DockerPruneGraphState) -> dict:
    mode = state.get("docker_command_mode", "wrapper")
    if mode == "disabled":
        logger.info("Docker prune disabled for this environment — skipping")
        return {"status": ActionStatus.SKIPPED.value, "reason": "docker_command_mode=disabled"}
    # ...rest of existing validation...
```

Refactor `assess_node` to branch on mode:
- If `mode == "wrapper"`: call `sudo -n /usr/local/sbin/errander-docker-assess` (single call), parse output with the new `parse_assess_output()` helper.
- If `mode == "direct_sudo"`: keep current 4-call behavior (`docker info`, `docker system df`, `docker images -f dangling=true -q | wc -l`, `docker ps -a -f status=exited -q | wc -l`).

Refactor `execute_node` to branch on mode:
- If `mode == "wrapper"`: call `sudo -n /usr/local/sbin/errander-docker-prune-safe` or `errander-docker-prune-aggressive` depending on `aggressive` param.
- If `mode == "direct_sudo"`: current behavior with `sudo -n /usr/bin/docker image prune -f && sudo -n /usr/bin/docker container prune -f`.

Add `parse_assess_output()` helper at module bottom:

```python
def parse_assess_output(stdout: str) -> dict:
    """Parse the errander-docker-assess wrapper output.

    Expected format:
        reachable=yes|no
        dangling_images=N
        stopped_containers=N
        error=<optional error message>
        system_df_begin
        <raw docker system df output>
        system_df_end

    Returns a dict with parsed fields. Missing fields default to safe values.
    """
    result = {
        "reachable": False,
        "dangling_images": 0,
        "stopped_containers": 0,
        "error": None,
        "system_df": "",
    }
    lines = stdout.splitlines()
    in_df_block = False
    df_lines = []
    for raw in lines:
        line = raw.rstrip()
        if line == "system_df_begin":
            in_df_block = True
            continue
        if line == "system_df_end":
            in_df_block = False
            continue
        if in_df_block:
            df_lines.append(line)
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "reachable":
            result["reachable"] = value.lower() in ("yes", "true", "1")
        elif key == "dangling_images":
            try:
                result["dangling_images"] = int(value)
            except ValueError:
                pass
        elif key == "stopped_containers":
            try:
                result["stopped_containers"] = int(value)
            except ValueError:
                pass
        elif key == "error":
            result["error"] = value or None
    result["system_df"] = "\n".join(df_lines)
    return result
```

#### 2.4 — Plumb mode through graphs

In `errander/agent/vm_graph.py`:
- Add `docker_command_mode: str` to `VMGraphState`
- In `_run_docker_prune`, pass `docker_command_mode` from VM state into the sub-graph state

In `errander/agent/graph.py`:
- Add a mechanism to load per-environment `docker_command_mode` and inject it into each VM's state during fan-out. Pattern: load from inventory, store on `BatchGraphState`, propagate during `Send()`.

In `errander/main.py` (`run_env_batch`):
- Read `docker_command_mode` from the loaded environment config, propagate to `BatchGraphState`, ultimately reaching each VM's state.

Default everywhere is `"wrapper"`.

#### 2.5 — Mode-aware preflight in `errander/execution/privilege.py`

Find `REQUIRED_BINARIES_BY_ACTION`. Replace the `docker_prune` key with two keys:

```python
REQUIRED_BINARIES_BY_ACTION = {
    ...other entries...
    "docker_prune_wrapper": [
        "/usr/local/sbin/errander-docker-assess",
        "/usr/local/sbin/errander-docker-prune-safe",
        "/usr/local/sbin/errander-docker-prune-aggressive",
    ],
    "docker_prune_direct": ["/usr/bin/docker"],
}
```

In `errander/agent/vm_graph.py` `sudo_preflight_node`, when iterating planned actions to determine which binaries to check, branch on `state.get("docker_command_mode")`:

```python
for action in state.get("planned_actions", []):
    action_type = action.get("action_type")
    if action_type == "docker_prune":
        mode = state.get("docker_command_mode", "wrapper")
        if mode == "disabled":
            continue  # no preflight needed
        key = f"docker_prune_{mode}"  # wrapper or direct
        binaries_to_check.extend(REQUIRED_BINARIES_BY_ACTION.get(key, []))
        if mode == "direct_sudo":
            logger.warning(
                "VM %s using direct_sudo Docker mode — not production hardened",
                state["vm_id"],
            )
            # Emit informational audit event
            if audit_store is not None:
                await audit_store.log_event(AuditEvent(
                    event_type=EventType.SUDO_PREFLIGHT_FAILED,  # reused for visibility, not blocking
                    batch_id=state.get("batch_id", ""),
                    vm_id=state["vm_id"],
                    action_type="docker_prune",
                    detail="WARNING: direct_sudo Docker mode is not production hardened",
                    metadata={"docker_command_mode": "direct_sudo"},
                ))
    else:
        binaries_to_check.extend(REQUIRED_BINARIES_BY_ACTION.get(action_type, []))
```

Adjust to match the existing structure of `sudo_preflight_node` — don't duplicate logic if there's already a binary-collection loop.

#### 2.6 — `SETUP.md` Docker hardening section

Find the existing Docker hardening section (added in previous commit). Update to reflect:

- Single assess wrapper `errander-docker-assess` replaces the previous 4-command pattern
- Wrapper output format spec (key=value + `system_df_begin/end` markers — copy from §2.3 above)
- Each wrapper supports `--check` flag → prints `ok` and exits 0
- All three wrappers must be in sudoers (replacing any previous `/usr/bin/docker` entry for production)
- New `docker_command_mode` config option in `inventory.yaml`, default `wrapper`

Wrapper script template (in SETUP.md):

```bash
# /usr/local/sbin/errander-docker-assess
#!/bin/bash
set -euo pipefail

# --check probe support (for sudo capability tests)
if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

# Reachability
if ! /usr/bin/docker info >/dev/null 2>&1; then
    echo "reachable=no"
    echo "error=docker daemon not reachable"
    exit 0
fi

echo "reachable=yes"

dangling=$(/usr/bin/docker images -f dangling=true -q 2>/dev/null | wc -l)
echo "dangling_images=${dangling}"

stopped=$(/usr/bin/docker ps -a -f status=exited -q 2>/dev/null | wc -l)
echo "stopped_containers=${stopped}"

echo "error="

echo "system_df_begin"
/usr/bin/docker system df 2>/dev/null || true
echo "system_df_end"
```

And the two prune wrappers (with `--check` and the actual command). Document the sudoers entries:

```
errander ALL=(root) NOPASSWD: \
  /usr/local/sbin/errander-docker-assess, \
  /usr/local/sbin/errander-docker-prune-safe, \
  /usr/local/sbin/errander-docker-prune-aggressive
```

#### 2.7 — New test file `tests/agent/subgraphs/test_docker_prune_modes.py`

Tests for the three modes:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from errander.agent.subgraphs.docker_prune import (
    validate_node, assess_node, execute_node, parse_assess_output,
)
from errander.models.actions import ActionStatus


# --- Mode dispatch ---

@pytest.mark.asyncio
async def test_disabled_mode_skips_validation():
    state = {"vm_id": "v1", "docker_command_mode": "disabled"}
    result = await validate_node(state)
    assert result["status"] == ActionStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_wrapper_mode_calls_assess_wrapper():
    state = {
        "vm_id": "v1", "docker_command_mode": "wrapper",
        "hostname": "h", "username": "u", "key_path": "k",
        "dry_run": False,
    }
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        success=True,
        stdout="reachable=yes\ndangling_images=2\nstopped_containers=1\nerror=\nsystem_df_begin\n\nsystem_df_end\n",
    ))
    await assess_node(state, executor=executor)
    # Inspect what command was actually called
    called_cmd = executor.execute.await_args.kwargs.get("command") or executor.execute.await_args.args[4]
    assert "errander-docker-assess" in called_cmd


@pytest.mark.asyncio
async def test_direct_sudo_mode_calls_raw_docker():
    state = {
        "vm_id": "v1", "docker_command_mode": "direct_sudo",
        "hostname": "h", "username": "u", "key_path": "k",
        "dry_run": False,
    }
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, stdout="0\n"))
    await assess_node(state, executor=executor)
    # At least one call should be to docker info / docker system df via sudo -n /usr/bin/docker
    all_cmds = [c.kwargs.get("command") or c.args[4] for c in executor.execute.await_args_list]
    assert any("sudo -n /usr/bin/docker" in cmd for cmd in all_cmds)


# --- Output parsing ---

def test_parse_assess_output_key_value():
    sample = (
        "reachable=yes\n"
        "dangling_images=5\n"
        "stopped_containers=2\n"
        "error=\n"
        "system_df_begin\n"
        "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\n"
        "Images 12 4 8.2GB 2.1GB\n"
        "system_df_end\n"
    )
    parsed = parse_assess_output(sample)
    assert parsed["reachable"] is True
    assert parsed["dangling_images"] == 5
    assert parsed["stopped_containers"] == 2
    assert parsed["error"] is None
    assert "Images 12" in parsed["system_df"]


def test_parse_assess_output_handles_error():
    sample = "reachable=no\nerror=docker daemon not reachable\n"
    parsed = parse_assess_output(sample)
    assert parsed["reachable"] is False
    assert parsed["error"] == "docker daemon not reachable"


def test_parse_assess_output_handles_missing_fields():
    parsed = parse_assess_output("")
    assert parsed["reachable"] is False
    assert parsed["dangling_images"] == 0
    assert parsed["stopped_containers"] == 0


# --- Execute wrapper dispatch ---

@pytest.mark.asyncio
async def test_wrapper_mode_prune_safe_call():
    state = {
        "vm_id": "v1", "docker_command_mode": "wrapper",
        "hostname": "h", "username": "u", "key_path": "k",
        "dry_run": False, "aggressive": False,
        "docker_info": {"reachable": True, "dangling_images": 5, "stopped_containers": 2},
    }
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))
    await execute_node(state, executor=executor)
    all_cmds = [c.kwargs.get("command") or c.args[4] for c in executor.execute.await_args_list]
    assert any("errander-docker-prune-safe" in c for c in all_cmds)


@pytest.mark.asyncio
async def test_wrapper_mode_prune_aggressive_call():
    state = {
        "vm_id": "v1", "docker_command_mode": "wrapper",
        "hostname": "h", "username": "u", "key_path": "k",
        "dry_run": False, "aggressive": True,
        "docker_info": {"reachable": True, "dangling_images": 5, "stopped_containers": 2},
    }
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))
    await execute_node(state, executor=executor)
    all_cmds = [c.kwargs.get("command") or c.args[4] for c in executor.execute.await_args_list]
    assert any("errander-docker-prune-aggressive" in c for c in all_cmds)
```

Note: the exact parameter names and call patterns depend on the existing signature of `assess_node` / `execute_node`. Adjust to fit. The intent is: assert the expected wrapper/raw command path is taken.

#### 2.8 — Extend `tests/agent/test_sudo_preflight.py`

Add these tests:

```python
@pytest.mark.asyncio
async def test_wrapper_mode_preflight_checks_wrapper_paths():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "wrapper",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/local/sbin/errander-docker-assess\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    called_cmd = executor.execute.await_args.kwargs.get("command") or executor.execute.await_args.args[4]
    assert "errander-docker-assess" in called_cmd
    assert "/usr/bin/docker" not in called_cmd


@pytest.mark.asyncio
async def test_direct_sudo_mode_preflight_checks_usr_bin_docker():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "direct_sudo",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/docker\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    called_cmd = executor.execute.await_args.kwargs.get("command") or executor.execute.await_args.args[4]
    assert "/usr/bin/docker" in called_cmd


@pytest.mark.asyncio
async def test_direct_sudo_mode_emits_warning_audit_event():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "direct_sudo",
        "batch_id": "b1",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="SUDO_OK /usr/bin/docker\n", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()
    await sudo_preflight_node(state, executor=executor, audit_store=audit_store)
    # At least one logged event should be the WARN about direct_sudo
    logged_events = [c.args[0] for c in audit_store.log_event.await_args_list]
    assert any("direct_sudo" in (e.detail or "") for e in logged_events)


@pytest.mark.asyncio
async def test_disabled_mode_preflight_skips_docker():
    state = {
        "dry_run": False, "vm_id": "v1",
        "hostname": "h", "ssh_user": "u", "ssh_key_path": "k",
        "os_family": "ubuntu",
        "docker_command_mode": "disabled",
        "planned_actions": [{"action_type": "docker_prune"}],
    }
    executor = MagicMock()
    mock_result = MagicMock(success=True, stdout="", stderr="")
    executor.execute = AsyncMock(return_value=mock_result)
    await sudo_preflight_node(state, executor=executor)
    # Either executor was not called for docker binaries, OR the capability check string contains no docker paths
    if executor.execute.await_count > 0:
        called_cmd = executor.execute.await_args.kwargs.get("command") or executor.execute.await_args.args[4]
        assert "docker" not in called_cmd
```

### Acceptance criteria

- All new tests pass
- `example/inventory.yaml` documents `docker_command_mode` with all three values
- SETUP.md Docker section is internally consistent: wrapper output format matches `parse_assess_output()` parser
- Default mode is `wrapper` (verified by a test asserting the default when no mode specified)
- Opportunistic ruff/mypy cleanup applied to: `schema.py`, `docker_prune.py`, `vm_graph.py`, `graph.py`, `main.py`, `privilege.py`

### Commit message

```
feat: docker_command_mode (wrapper/direct_sudo/disabled) per environment
```

---

## Commit 3 — `--check-targets <env>` CLI

### Goal

A new CLI command that SSHs to each VM in a given environment and produces a readiness report: are required binaries present? Does `sudo -n` work for each? In wrapper mode, do the wrapper scripts exist and respond to `--check`? Operators run this before a maintenance window to find broken setups in advance.

### Files and changes

#### 3.1 — SETUP.md supported distro matrix

Find the Prerequisites section. Add (or update) a table:

```markdown
### Supported target operating systems

| OS family | Versions officially supported |
|---|---|
| Ubuntu | 20.04 LTS, 22.04 LTS, 24.04 LTS |
| Debian | 11 (Bullseye), 12 (Bookworm) |
| RHEL / Rocky / Alma | 8.x, 9.x |

Older distros may work, but Errander expects FHS-compliant absolute binary paths
(`/usr/bin/apt-get`, `/usr/sbin/logrotate`, etc.). Older distros that have not
adopted the `/usr → /` merge may need a runtime path resolver. That's tracked
as a separate compatibility project — `--check-targets <env>` will report any
missing binaries.
```

In README.md, add one line in the appropriate spot:

```markdown
**Supported target OS:** Ubuntu 20.04+, Debian 11+, RHEL/Rocky/Alma 8+.
```

#### 3.2 — New module `errander/execution/target_validation.py`

```python
"""Per-VM readiness validation for --check-targets CLI.

Runs SSH probes to confirm each target VM has the binaries Errander needs and
that sudo -n is configured for them. In wrapper mode, also probes the docker
wrapper scripts via their --check flag.

Read-only: no mutation of any kind.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from errander.execution.privilege import (
    PRIVILEGED_PATHS,
    REQUIRED_BINARIES_BY_ACTION,
)
from errander.execution.ssh import SSHConnectionManager

logger = logging.getLogger(__name__)

Verdict = Literal["ready", "warnings", "blocked"]


@dataclass
class TargetReadiness:
    vm_id: str
    hostname: str
    binaries_present: dict[str, bool] = field(default_factory=dict)
    sudo_ok: dict[str, bool] = field(default_factory=dict)
    wrappers_ok: dict[str, bool] = field(default_factory=dict)
    verdict: Verdict = "ready"
    issues: list[str] = field(default_factory=list)


# Core privileged binaries every VM needs (regardless of action mix)
_CORE_BINARIES = [
    "/usr/bin/apt-get",        # or /usr/bin/dnf — checked per OS in caller
    "/usr/bin/journalctl",
    "/usr/sbin/logrotate",
    "/usr/bin/gzip",
    "/usr/bin/truncate",
    "/usr/bin/cp",
]

_WRAPPER_PATHS = [
    "/usr/local/sbin/errander-docker-assess",
    "/usr/local/sbin/errander-docker-prune-safe",
    "/usr/local/sbin/errander-docker-prune-aggressive",
]


def _binaries_for_os(os_family: str) -> list[str]:
    """Return the per-OS list of expected privileged binaries."""
    base = [
        "/usr/bin/journalctl",
        "/usr/sbin/logrotate",
        "/usr/bin/gzip",
        "/usr/bin/truncate",
        "/usr/bin/cp",
    ]
    if os_family in ("ubuntu", "debian"):
        base.extend(["/usr/bin/apt-get", "/usr/bin/apt-mark"])
    elif os_family in ("rhel", "rocky", "alma", "centos"):
        base.extend(["/usr/bin/dnf"])
    return base


async def check_target(
    vm_id: str,
    hostname: str,
    username: str,
    key_path: str,
    os_family: str,
    docker_command_mode: str,
    ssh_manager: SSHConnectionManager,
) -> TargetReadiness:
    """Run all readiness checks against a single target VM. Read-only."""
    readiness = TargetReadiness(vm_id=vm_id, hostname=hostname)
    binaries = _binaries_for_os(os_family)

    # 1. Binary presence via `command -v`
    for binary in binaries:
        cmd = f"command -v {binary} >/dev/null 2>&1 && echo present || echo missing"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        present = result.success and "present" in result.stdout
        readiness.binaries_present[binary] = present
        if not present:
            readiness.issues.append(f"missing binary: {binary}")

    # 2. Sudo -n capability per binary
    for binary in binaries:
        if not readiness.binaries_present.get(binary):
            readiness.sudo_ok[binary] = False
            continue
        cmd = f"sudo -n {binary} --version >/dev/null 2>&1 && echo ok || echo fail"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        ok = result.success and "ok" in result.stdout
        readiness.sudo_ok[binary] = ok
        if not ok:
            readiness.issues.append(f"sudo -n denied for: {binary}")

    # 3. Docker wrapper probes (wrapper mode only)
    if docker_command_mode == "wrapper":
        for wrapper in _WRAPPER_PATHS:
            cmd = f"sudo -n {wrapper} --check 2>/dev/null"
            result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
            ok = result.success and "ok" in result.stdout.strip()
            readiness.wrappers_ok[wrapper] = ok
            if not ok:
                readiness.issues.append(f"wrapper script not ready: {wrapper}")
    elif docker_command_mode == "direct_sudo":
        cmd = "sudo -n /usr/bin/docker version >/dev/null 2>&1 && echo ok || echo fail"
        result = await ssh_manager.execute(vm_id, hostname, username, key_path, cmd)
        ok = result.success and "ok" in result.stdout
        readiness.wrappers_ok["/usr/bin/docker"] = ok
        if not ok:
            readiness.issues.append("sudo -n denied for: /usr/bin/docker")
    # disabled mode: no docker check needed

    # Verdict
    if readiness.issues:
        readiness.verdict = "blocked"
    else:
        readiness.verdict = "ready"

    return readiness


def render_readiness_report(results: list[TargetReadiness]) -> str:
    """Render a per-VM readiness table for terminal output."""
    lines = []
    lines.append(f"{'VM':<30} {'Host':<20} {'Verdict':<10} {'Issues':<60}")
    lines.append("-" * 120)
    for r in results:
        issues_str = "; ".join(r.issues) if r.issues else "—"
        if len(issues_str) > 58:
            issues_str = issues_str[:55] + "..."
        lines.append(f"{r.vm_id:<30} {r.hostname:<20} {r.verdict:<10} {issues_str:<60}")
    lines.append("")
    blocked = sum(1 for r in results if r.verdict == "blocked")
    lines.append(f"Summary: {len(results) - blocked} ready, {blocked} blocked")
    return "\n".join(lines)
```

#### 3.3 — Wire into `errander/main.py`

Find the argparse setup. Add a new mutually-exclusive flag:

```python
parser.add_argument(
    "--check-targets",
    metavar="ENV",
    help="SSH to every VM in <ENV> and report sudo / binary / wrapper readiness. Read-only.",
)
```

Add the corresponding handler. Where the dispatcher decides which subcommand to run (depending on existing structure):

```python
if args.check_targets:
    return asyncio.run(check_targets_cmd(args.check_targets))


async def check_targets_cmd(env_name: str) -> int:
    """Run --check-targets for the given environment."""
    from errander.execution.target_validation import check_target, render_readiness_report
    from errander.execution.ssh import SSHConnectionManager
    from errander.config.inventory import load_inventory
    from errander.config.settings import load_settings

    settings = load_settings()
    inventory = load_inventory(settings.inventory_path)
    env = inventory.environments.get(env_name)
    if env is None:
        print(f"Unknown environment: {env_name}")
        return 1

    ssh_manager = SSHConnectionManager()
    results = []
    try:
        for vm in env.hosts:
            readiness = await check_target(
                vm_id=vm.vm_id,
                hostname=vm.hostname,
                username=vm.ssh_user,
                key_path=vm.ssh_key_path,
                os_family=vm.os_family,
                docker_command_mode=env.docker_command_mode,
                ssh_manager=ssh_manager,
            )
            results.append(readiness)
    finally:
        await ssh_manager.close_all()

    print(render_readiness_report(results))
    blocked = any(r.verdict == "blocked" for r in results)
    return 1 if blocked else 0
```

Adjust to match the existing `async_main` / argparse structure. Use the same patterns the other `--check-*` flags use.

#### 3.4 — Wrapper `--check` flag in SETUP.md

In the wrapper script section, make sure each of the three wrappers has this at the top:

```bash
if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi
```

This MUST come before any privileged operation in each script.

#### 3.5 — New test file `tests/execution/test_target_validation.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from errander.execution.target_validation import (
    TargetReadiness, check_target, render_readiness_report,
)


@pytest.mark.asyncio
async def test_all_binaries_present_and_sudo_ok():
    ssh = MagicMock()
    # All probes succeed
    ssh.execute = AsyncMock(side_effect=lambda *a, **k: MagicMock(
        success=True,
        stdout="present\n" if "command -v" in a[4] else "ok\n",
    ))
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert r.verdict == "ready"
    assert r.issues == []


@pytest.mark.asyncio
async def test_missing_binary_blocks():
    ssh = MagicMock()
    # apt-get missing, all others present and sudo ok
    def fake_exec(*args, **kwargs):
        cmd = args[4] if len(args) > 4 else kwargs.get("command", "")
        if "command -v /usr/bin/apt-get" in cmd:
            return MagicMock(success=True, stdout="missing\n")
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")
    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert r.verdict == "blocked"
    assert any("apt-get" in issue for issue in r.issues)


@pytest.mark.asyncio
async def test_wrapper_mode_checks_wrapper_scripts():
    ssh = MagicMock()
    calls = []
    def fake_exec(*args, **kwargs):
        cmd = args[4] if len(args) > 4 else kwargs.get("command", "")
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")
    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="wrapper",
        ssh_manager=ssh,
    )
    assert any("errander-docker-assess --check" in c for c in calls)
    assert any("errander-docker-prune-safe --check" in c for c in calls)
    assert any("errander-docker-prune-aggressive --check" in c for c in calls)


@pytest.mark.asyncio
async def test_direct_sudo_mode_checks_raw_docker():
    ssh = MagicMock()
    calls = []
    def fake_exec(*args, **kwargs):
        cmd = args[4] if len(args) > 4 else kwargs.get("command", "")
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")
    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="direct_sudo",
        ssh_manager=ssh,
    )
    assert any("sudo -n /usr/bin/docker version" in c for c in calls)


@pytest.mark.asyncio
async def test_disabled_mode_skips_docker_checks():
    ssh = MagicMock()
    calls = []
    def fake_exec(*args, **kwargs):
        cmd = args[4] if len(args) > 4 else kwargs.get("command", "")
        calls.append(cmd)
        if "command -v" in cmd:
            return MagicMock(success=True, stdout="present\n")
        return MagicMock(success=True, stdout="ok\n")
    ssh.execute = AsyncMock(side_effect=fake_exec)
    r = await check_target(
        vm_id="v1", hostname="h", username="u", key_path="k",
        os_family="ubuntu", docker_command_mode="disabled",
        ssh_manager=ssh,
    )
    assert not any("docker" in c for c in calls)


def test_render_readiness_report_format():
    results = [
        TargetReadiness(vm_id="v1", hostname="h1", verdict="ready"),
        TargetReadiness(vm_id="v2", hostname="h2", verdict="blocked",
                        issues=["missing binary: /usr/bin/apt-get"]),
    ]
    output = render_readiness_report(results)
    assert "v1" in output
    assert "v2" in output
    assert "ready" in output
    assert "blocked" in output
    assert "1 ready, 1 blocked" in output
```

#### 3.6 — Extend `tests/test_main_cli.py` (or wherever CLI argparse tests live)

```python
def test_check_targets_flag_parses(capsys):
    # Import the parser builder if exported, or invoke main with --help
    # Adjust to fit existing CLI test pattern in this repo
    ...

def test_check_targets_exits_0_when_all_ready(monkeypatch):
    # Mock check_target to return all-ready, invoke check_targets_cmd, assert exit 0
    ...

def test_check_targets_exits_1_when_any_blocked(monkeypatch):
    # Mock check_target to return one blocked, invoke, assert exit 1
    ...
```

Use the existing CLI test patterns in this repo. If `tests/test_main_cli.py` doesn't exist, find the closest equivalent.

### Acceptance criteria

- New file `errander/execution/target_validation.py` exists with `TargetReadiness`, `check_target()`, `render_readiness_report()`
- `--check-targets <env>` CLI flag works end-to-end against mocked SSH responses
- Exit code semantics: 0 if all ready, 1 if any blocked
- SETUP.md and README.md document supported distro matrix
- All three wrapper scripts in SETUP.md include `--check` support
- All new tests pass
- Opportunistic ruff/mypy cleanup applied to: `main.py`, `target_validation.py` (write clean from the start)

### Commit message

```
feat: --check-targets CLI for pre-flight VM readiness validation
```

---

## Final Phase A deliverables

After all three commits land:

### Definition of done

| Check | Command | Expected |
|---|---|---|
| Full test suite | `uv run pytest` | All pass |
| Anchor phrases present | `grep -r "MCP belongs in the operator brain" docs/ CLAUDE.md` | ≥ 2 matches |
| `/usr/bin/env` removed | `grep "/usr/bin/env" errander/safety/ errander/execution/` | No matches in privileged code |
| Preflight event correct | `grep "PREFLIGHT_LOCK_DETECTED" errander/agent/vm_graph.py` | 0 inside `sudo_preflight_node` |
| `STATUS.md` updated | manual inspection | Phase A marked complete |
| `tasks/todo.md` updated | manual inspection | Phase A items checked off |
| `docs/learning/XX-sudo-privilege-model.md` exists | manual inspection | One new learning doc covering the privilege model, wrapper mode, preflight design |
| No regressions in mypy / ruff in touched files | `uv run mypy <touched files>` and `uv run ruff check <touched files>` | At minimum no new errors in those files |

### Post-Phase A

Once Phase A is done, the next planned work is:

- **Phase B — Proactive signals (deterministic)**: `ProactiveSignal` engine, signal catalog, native SSH probes, daily digest. See discussion thread for design — engine + catalog + LLM-summarizes-only.
- **Phase C — Optional observability adapters**: Direct Python adapters for Prometheus, ELK. Optional, never required.
- **Phase D — Operator Assistant Layer**: The full Layer A vision — MCP / CLI / Skills for investigation and recommendation. Comes after B and C are stable.

Do not start any of these without an explicit go-ahead. Phase A first.

---

## Questions for the user (only if blocked)

If you hit a structural ambiguity that isn't covered by this plan, ask the user before guessing. Examples of valid questions:
- The schema may use Pydantic v1 or v2 syntax — please confirm.
- The current `sudo_preflight_node` signature differs from the plan's assumption — should I refactor or adapt?
- The `tests/test_main_cli.py` file structure doesn't match the pattern in this plan — where should the new CLI tests go?

Do NOT ask the user about:
- Whether to skip tests (always write tests)
- Whether to keep using `privileged()` helper (yes, always)
- Whether to add LLM/MCP to Layer B (no, never — see `docs/AI-ARCHITECTURE.md`)
- Whether to expand scope (no — stay in Phase A)

---

## End of plan

Sonnet: start with Commit 1. Work through each commit in order. Run tests after each. Push only when all three commits are in.

Opus signed off on this plan on 2026-05-15. SRE validation in `ai_sre_audit_v2.md` "Two-Layer AI Architecture Validation" section.
