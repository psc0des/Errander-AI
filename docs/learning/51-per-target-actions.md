# 51 — Per-Target Action Overrides

## What was built and why

`actions:` in `inventory.yaml` was previously an environment-level-only block. Every VM
in an environment got the same `docker_hygiene.enabled`, `restartable_units`, etc. This
is wrong in practice: a DB server and a web server in the same env run different services,
and only some VMs may have Docker installed.

This feature adds a `actions:` block at the individual target level. Target-level entries
replace the env-level config for that specific action; all other actions inherit from the
environment as before.

## Key concepts used

### Action-level replacement (not field-level merge)

When a target specifies `docker_hygiene: {enabled: false}`, that `ActionConfig` replaces
the env-level `ActionConfig` for `docker_hygiene` entirely. Other fields (`command_mode`,
`volume_deletion_enabled`, etc.) get their Pydantic defaults.

This design trades field-level ergonomics for simplicity and predictability. The user must
specify the complete config they want if they override — no implicit inheritance of individual
fields from the env. In practice this is fine: the two main use cases are disabling an action
(`{enabled: false}`) or setting `restartable_units` (always goes with `enabled: true`).

### resolve_actions() — where the merge happens

```python
class TargetSchema(BaseModel):
    actions: dict[str, ActionConfig] | None = None

    def resolve_actions(self, env_actions: dict[str, ActionConfig]) -> dict[str, ActionConfig]:
        if not self.actions:
            return env_actions          # fast path — no copy needed
        return {**env_actions, **self.actions}   # target overrides per key
```

`env_actions` is always the **fully expanded** env dict (every BUILTIN_ACTION present,
defaults filled). `resolve_actions()` is called at two points:
1. **Batch build time** (`run_maintenance` in `main.py`) — once per target to compute
   `enabled_actions` and `docker_command_mode` for the serialized target dict.
2. **CLI paths** (`run_check_targets`, `run_restart_service`) — once per targeted VM to
   validate per-VM config before executing.

### Embedding resolved values in the target dict

The batch graph passes a serialized `dict[str, object]` per VM through `BatchGraphState →
VMGraphState`. The resolved values are embedded at batch-build time:

```python
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
        if cfg.enabled and not BUILTIN_ACTIONS[name].operator_triggered
    ]
    yaml_targets.append({
        ...
        "enabled_actions": t_enabled,
        "docker_command_mode": t_docker_mode,
    })
```

DB-added VMs (added via UI, no inventory schema) don't have these fields — all three
fan-out paths fall back to batch-level values when the key is absent.

### Three fan-out paths, one pattern

Every path that dispatches per-VM work reads the target dict first:

```python
# validate_targets_node — readiness check
_t_enabled = t.get("enabled_actions")
_enabled = list(_t_enabled) if isinstance(_t_enabled, list) else state.get("enabled_actions")

# route_plan_vms — planning fan-out
_t_enabled = t.get("enabled_actions")
effective_enabled = list(_t_enabled) if isinstance(_t_enabled, list) else _batch_enabled

# dispatch_current_wave — execution fan-out
docker_command_mode=(str(t["docker_command_mode"]) if "docker_command_mode" in t else _batch_docker),
enabled_actions=(list(t["enabled_actions"]) if "enabled_actions" in t and ... else _batch_fallback),
```

The `isinstance(..., list)` guard is needed because `dict[str, object]` returns `object`,
not `list[str]` — mypy requires a narrowing check.

### Per-target validation in the env validator

`EnvironmentSchema._apply_action_defaults_and_validate` already validates env-level action
configs. After computing `full_actions` (env defaults expanded), it now iterates targets:

```python
for target in self.targets:
    if not target.actions:
        continue
    if "docker_prune" in target.actions:
        raise ConfigError(...)         # legacy key guard
    resolved = {**full_actions, **target.actions}
    # docker_hygiene contradiction check on resolved
    # service_restart empty units check on resolved
    # unit name safety validation
```

Validating against the **resolved** config (not just the override keys) catches cases like
a target enabling service_restart when the env already provides units — the merged view
must be valid.

## Gotchas

### Variable shadowing and mypy

The `for t in env_schema.targets:` loop sets `t: TargetSchema` in the outer scope. A
later `for t in targets:` (where `targets: list[dict[str, object]]`) would cause mypy
to flag "incompatible assignment". Fix: use a different loop variable (`entry`) for the
post-yaml_targets loops.

### type: ignore[arg-type] becomes unused

Before this feature, `list(some_list_of_object)` needed `# type: ignore[arg-type]`.
After adding `isinstance(..., list)` guards, mypy narrows the type and the ignore is
redundant — mypy will flag it as `unused-ignore`. Remove the comment.

### service_restart CLI references env-level in original code

`run_restart_service` originally checked `env.actions.get("service_restart")` and used
`restart_cfg.restartable_units` as a single allowlist for all VMs. With per-target, this
must be checked per targeted VM. Each VM's resolved config is checked independently — a
unit must appear in that specific VM's resolved allowlist.

## Quiz

1. If env has `service_restart: {enabled: true, restartable_units: [nginx.service]}` and
   a target overrides `service_restart: {enabled: true, restartable_units: [db.service]}`,
   what does `resolve_actions()` return for that target's service_restart?
   → `{enabled: true, restartable_units: [db.service]}` — target replaces env entirely for
   that key.

2. Why must the validator check the *resolved* (merged) actions rather than just the
   override keys?
   → A target could set `{enabled: true}` with no `restartable_units`, inheriting `[]`
   from the env — the validator must see the merged result to catch the empty-units case.

3. Why are `enabled_actions` and `docker_command_mode` embedded in the target dict at
   batch-build time rather than computed in each fan-out function?
   → The inventory schema object is not available inside the graph's closure. All three
   fan-out functions only see `BatchGraphState`, which holds serialized target dicts.
   Computing at build time also means it happens once per batch, not three times per VM.
