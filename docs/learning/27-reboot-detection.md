---
title: "27 — Reboot-Required Detection (PR-1.2)"
---

# Reboot-Required Detection

## What was built and why

After a successful non-kernel patching run, certain package updates (libc, PAM, systemd) require a reboot to take effect. Without detection, operators have no signal that a VM is running with mixed old/new libraries — a security gap even when the patch succeeded. PR-1.2 adds a post-patching probe that:

1. Runs the OS-appropriate reboot-required check over SSH.
2. Persists the flag to `VMStateStore` so it survives across agent runs.
3. Emits a `REBOOT_REQUIRED_DETECTED` audit event.
4. Surfaces affected VMs in batch reports via `format_reboot_required_section`.

No auto-reboot is ever performed. Detection only.

## Key concepts

### Two OS probe paths

**Debian/Ubuntu** — flag file written by `dpkg` postinst scripts:

```bash
if [ -f /var/run/reboot-required ]; then
  echo 'REBOOT=1';
  cat /var/run/reboot-required.pkgs 2>/dev/null || true;
else echo 'REBOOT=0';
fi
```

The `.pkgs` file lists one triggering package per line. If it's absent (older Debian), the probe still works — `cat` exits 0 via `|| true`.

**RHEL/CentOS** — `needs-restarting -r` (part of `dnf-utils`):

```bash
if command -v needs-restarting >/dev/null 2>&1; then
  needs-restarting -r >/dev/null 2>&1;
  echo "EXIT=$?";
else echo 'EXIT=unknown';
fi
```

Exit 1 = reboot needed. The binary-absent path (`EXIT=unknown`) is treated as "no reboot required" — the probe never blocks runs on minimal/stripped images where `dnf-utils` isn't installed.

### Both commands always exit 0

The shell wrapping ensures the SSH `result.success` check is never triggered by the probe itself. Only genuine SSH failures (timeout, key error) return `result.success = False`, and those are treated as "no reboot required" (best-effort probe, never blocks runs).

### `parse_reboot_status()` — pure parsing, no SSH

```python
def parse_reboot_status(stdout: str, os_family: str) -> RebootStatus:
```

- Debian: first line must be `REBOOT=1`; remaining non-blank lines are package names.
- RHEL: scans for `EXIT=N` token; `1` → needs reboot; `0`/`unknown` → no reboot.
- Empty stdout → `RebootStatus(needs_reboot=False, ...)` for both OS variants.

The function is pure — no network, no side effects. Unit-tested exhaustively.

### `reboot_check_node` — post-verify node in the patching subgraph

```
execute → verify → reboot_check → END   (live, successful upgrade)
execute → verify → rollback → END       (verify failed)
execute → END                            (dry-run: DRY_RUN_OK exits early)
```

The node only runs when the upgrade succeeded. Dry-run batches exit at `DRY_RUN_OK` before reaching `verify`, so `reboot_check_node` is never invoked for dry runs.

The routing inside `build_patching_subgraph` uses a local `_route_verify` closure when `sre_reboot_check=True`:

```python
def _route_verify(state: PatchingGraphState) -> str:
    if state.get("status") == ActionStatus.FAILED.value or state.get("error"):
        return "rollback"
    return "reboot_check"
```

When `sre_reboot_check=False`, the existing `route_after_verify` module function routes directly to END. This compile-time flag means tests can pass `sre_reboot_check=False` to avoid the extra SSH call.

### VMStateStore persistence

When `needs_reboot=True`, the node calls:

```python
await vm_state_store.set_needs_reboot(vm_id, reason, pkgs)
```

This upserts the `vm_state` table row for the VM. The flag survives across agent restarts and is cleared only by `clear_needs_reboot()` (e.g., after a confirmed reboot). `vm_state_store=None` is valid — the node skips persistence and logs only.

### Report surface

`format_reboot_required_section(vms: list[VMState]) -> str` renders a Slack-ready section:

```
*VMs awaiting reboot after patching:*
  • `dev/web-01` — packages require reboot (libc6, linux-base)
  • `prod/db-01` — system requires reboot
```

Package lists are truncated to 5 with "+N more" for long lists. Empty input returns `""`.

## Gotchas encountered

- **`reboot_check_node` must not run in dry-run mode**: The `DRY_RUN_OK` path exits from `route_after_execute` before reaching `verify` or `reboot_check`. The `sre_reboot_check=True` test for dry-run (`test_dry_run_skips_reboot_check`) verifies `reboot_status_detected` is absent from the final state.
- **TC001 ruff rule on `ActionResult` in reporting.py**: Since `ActionResult` was only used in type annotations for `generate_plan_report`/`generate_execution_report`, it must live in the `TYPE_CHECKING` block with `from __future__ import annotations` in place.
- **`vm_state_store=None` guard**: All callers of `reboot_check_node` in tests use `AsyncMock(spec=VMStateStore)` or `None`. The node must explicitly check `if vm_state_store is not None` before awaiting persistence.

## Quiz yourself

1. Why does `reboot_required_command("rhel")` use `EXIT=unknown` instead of a non-zero exit code when `needs-restarting` is absent?
2. Why does `reboot_check_node` never run during a dry-run patching batch?
3. What happens if SSH to the VM fails during the reboot probe?
4. What does `format_reboot_required_section([])` return, and why is that the right behaviour for report concatenation?
5. Why is `vm_state_store` an optional parameter rather than required?
