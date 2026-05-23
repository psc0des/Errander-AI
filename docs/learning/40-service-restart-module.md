# 40 — Service Restart Module

## What Was Built and Why

The service restart module adds a sixth action to Errander-AI's capability set.
Unlike the five background actions (patching, disk cleanup, log rotation, Docker prune,
backup verify), service restart is **operator-triggered** — the agent never decides on
its own to restart a service.

The design answers a concrete operational need: "I deployed a config change; now restart
nginx on prod-web-01 without shelling in." The agent provides the safety gate (allowlist
check, Slack approval, audit log) while the operator retains intent.

---

## Key Design Decisions

### Why Operator-Triggered, Not Detect-and-Propose

Every other action in Errander-AI follows a detect → propose → approve → execute loop.
Service restart intentionally breaks this pattern:

- **No safe auto-detection signal exists.** A service in `active` state might still need
  a reload (e.g., new config file). A service in `failed` state might need investigation
  before restart. The agent cannot distinguish "restart is safe" from "restart will
  hide a real incident."
- **Unexpected restarts are high-impact events.** Even a brief nginx restart drops in-flight
  connections. Operators need to decide when the moment is right.
- **The CLI is the interface.** `--restart-service prod --unit nginx.service --vm prod-web-01`
  is explicit, auditable, and integrates with CI/CD pipelines.

### Risk Tier: HIGH

Service restart sits at the HIGH risk tier alongside human-approval-required actions.
Even under the `relaxed` approval policy (which auto-approves LOW and MEDIUM tiers),
HIGH tier still routes through Slack. The HITL guardrail (`autonomous_live_apply_enabled=False`
by default, `require_live_approval=True`) adds a second layer: even if someone tweaks
policy to "relaxed," live HIGH-tier executions still demand approval.

This is enforced in two places:
1. `ACTION_RISK_TIERS[ActionType.SERVICE_RESTART] = RiskTier.HIGH` (declarative)
2. `tests/agent/test_approval.py` — seven tests that prove HIGH always requires approval

### Two-Layer Allowlist

The allowlist exists at two levels to handle the reality that config drift happens:

| Layer | Where | Enforced By |
|---|---|---|
| Inventory side | `actions.service_restart.restartable_units` in `inventory.yaml` | CLI validation before SSH |
| Target side | `/etc/errander/restart-allowlist` on each VM | Wrapper script before `systemctl restart` |

If the inventory says "nginx is allowed" but the wrapper file does not, the wrapper
refuses. If the wrapper allows "redis-server" but inventory does not, the CLI refuses
before any SSH connection is made. Both must agree.

`--check-targets` detects drift between these two layers and prints `ALLOWLIST DRIFT`
lines for missing or extra units. This is the standard pre-flight check before enabling
the feature in a new environment.

---

## Architecture: Sub-graph Node Responsibilities

Service restart does **not** have a LangGraph sub-graph. It is a pure CLI path — the
operator explicitly requests it, so there is no "decide whether to act" loop.

```
Operator CLI
    │
    ▼
async_main (main.py)
    │  validates: --restart-service, --unit, --vm/--vms present
    │
    ▼
run_restart_service()
    │  Layer B: deterministic validation only, no LLM
    │  1. Load inventory → locate env
    │  2. Check service_restart.enabled
    │  3. Check unit in restartable_units
    │  4. Check each vm-id exists in env.targets
    │  5. Load settings → open AuditStore
    │  6. Emit SERVICE_RESTART_REQUESTED audit event (before any SSH)
    │  7. Print plan (dry-run: stop here; live: route to approval)
    │
    ▼  (live mode, future)
Slack approval gate
    │
    ▼
SSH: sudo /usr/local/sbin/errander-systemctl-restart <unit>
    │  wrapper checks /etc/errander/restart-allowlist
    │  wrapper logs to /var/log/errander-restart.log
    │
    ▼
AuditStore: SERVICE_RESTART_EXECUTED or SERVICE_RESTART_FAILED
```

The decision not to use a LangGraph sub-graph here is intentional: there is no branching
logic based on observed state. The operator has already decided. The agent's job is
purely to validate, gate, and audit.

---

## The Config Validation Invariant

```python
# errander/config/schema.py — _apply_action_defaults_and_validate()
service_restart_cfg = full_actions.get("service_restart")
if service_restart_cfg and service_restart_cfg.enabled and not service_restart_cfg.restartable_units:
    raise ConfigError(
        "service_restart.enabled is true for this environment, but restartable_units is empty. "
        "Add restartable_units: [unit1, unit2, ...] under actions.service_restart, "
        "or set enabled: false."
    )
```

This runs at settings load time, not at restart time. You cannot start the agent with
a misconfigured environment — it fails fast at startup with a clear error message.
The same validation fires in tests via `ConfigError`, not at runtime on a live VM.

---

## The Drift Check

`run_check_targets()` gains a new phase after the main target connectivity loop:

```python
service_restart_cfg = env.actions.get("service_restart")
if service_restart_cfg and service_restart_cfg.enabled:
    inventory_units = set(service_restart_cfg.restartable_units)
    for target in env.targets:
        cmd = "cat /etc/errander/restart-allowlist 2>/dev/null || echo '__not_found__'"
        ssh_result = await ssh_manager.execute(target.name, target.host, username, key_path, cmd)
        if ssh_result.success and "__not_found__" not in ssh_result.stdout:
            on_target_units = {line.strip() for line in ssh_result.stdout.splitlines() if line.strip()}
            for unit in sorted(inventory_units - on_target_units):
                print(f"  ALLOWLIST DRIFT {target.name}: '{unit}' in inventory but missing ...")
            for unit in sorted(on_target_units - inventory_units):
                print(f"  ALLOWLIST DRIFT {target.name}: '{unit}' in /etc/... but not in inventory")
        else:
            print(f"  WARN {target.name}: /etc/errander/restart-allowlist not readable ...")
```

Key properties of this design:

- **No SSH for disabled envs.** The entire block is guarded by `if service_restart_cfg and service_restart_cfg.enabled`. Existing tests pass without mocking `execute` because their inventories don't enable service_restart.
- **Symmetric diff.** Both `inventory − target` and `target − inventory` directions are checked. Missing-from-target is a safety gap; extra-on-target is a stale-allowlist signal.
- **Graceful on missing file.** `cat ... || echo '__not_found__'` means the SSH command always succeeds; the missing-file case is reported as a WARN rather than a crash.

---

## Testing Strategy

### Config validation tests (`tests/config/test_schema_actions.py`)

Six tests cover the `restartable_units` invariant:
- enabled + empty → `ConfigError`
- enabled + non-empty → accepted
- disabled + empty → accepted (most common case)
- default (no block) → disabled + empty, no error
- YAML load with units → accepted
- YAML load with enabled + empty → `ConfigError`

### CLI tests (`tests/test_main.py`)

`TestRestartServiceCLI` (7 tests) proves each validation branch of `run_restart_service`:
- dry-run returns 0
- unknown env returns 1
- unit not in allowlist returns 1
- unknown VM returns 1
- service_restart disabled returns 1
- missing `--vm`/`--vms` returns 1
- missing `--unit` returns 1

`TestCheckTargetsAllowlistDrift` (3 tests) patches `SSHConnectionManager.execute` with
`AsyncMock` to inject fabricated allowlist content:
- drift case: prints the correct ALLOWLIST DRIFT lines
- no-drift case: no drift lines printed
- disabled case: `side_effect=AssertionError` proves execute is never called

### Approval guarantee tests (`tests/agent/test_approval.py`)

Seven tests prove the HITL invariant without needing a running Slack instance:
- `ACTION_RISK_TIERS[ActionType.SERVICE_RESTART] is RiskTier.HIGH`
- HIGH is in strict and moderate approval sets
- HITL guardrail (`require_live_approval=True`) covers all tiers including HIGH under relaxed policy
- `autonomous_live_apply_enabled=False` alone does not bypass HITL
- relaxed policy alone does not cover HIGH (motivates why HITL is needed)
- service_restart is not LOW or MEDIUM

---

## Gotchas

**The wrapper, not the agent, is the last line of defense.** The inventory allowlist
prevents the CLI from even attempting a disallowed restart. But the wrapper's
`/etc/errander/restart-allowlist` enforces the same constraint at the shell level —
even if someone bypasses the CLI entirely. Do not skip the wrapper install in production.

**`restartable_units: []` is the safe default.** When `enabled: false` (the default),
an empty list is fine. When `enabled: true`, an empty list raises `ConfigError` at
startup. This is intentional: an enabled-but-empty config is most likely a copy-paste
mistake.

**Dry-run is the default.** `--restart-service` without `--live` prints the plan and
exits. You must pass `--live` to attempt real SSH. This matches Errander-AI's general
principle: live execution always requires explicit opt-in.

---

## Quiz Yourself

1. Why is `service_restart` operator-triggered rather than detect-and-propose?
2. What happens when `enabled: true` but `restartable_units: []`? When does the error fire?
3. Describe the two-layer allowlist. What gap does each layer close?
4. How does `--check-targets` detect drift? What is the `__not_found__` sentinel for?
5. Why does the drift-check SSH path only trigger when `service_restart.enabled: true`?
6. Under the `relaxed` approval policy, does service_restart still require Slack approval? Why?
7. Why is there no LangGraph sub-graph for service restart?
