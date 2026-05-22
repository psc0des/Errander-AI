# 45 — docker_hygiene v1.1 Session 3: Hard cutover (removing docker_prune)

## What was built and why

Session 3 is the final step of the docker_hygiene v1.1 migration: **docker_prune is fully removed**
from the active codebase. docker_hygiene (object-level approval, per-object drift gates, wrapper-only)
is now the sole Docker action.

The rationale: docker_prune was a bulk-approval action that violated the Exact-Object Approval
invariant. It approved "Docker cleanup" as a category, not individual objects. Session 3 closes
that gap permanently — there is no path to bulk-approve Docker actions anymore.

## Key concepts

### Hard delete vs. soft deprecation

docker_prune is **deleted**, not deprecated. The subgraph file, three test files, the install-docker-wrappers.sh script, and all BUILTIN_ACTIONS registry entries are gone. This is intentional: soft deprecation ("it still works, just don't use it") leaves the bulk-approval hole open.

The one exception: `ActionType.DOCKER_PRUNE` is **retained in the enum** for audit-log read-back compatibility. Old audit rows that say `action_type="docker_prune"` still deserialize correctly. The `LEGACY_ACTION_TYPES` frozenset marks it so tests and validators know to skip it in "active action" assertions.

```python
# models/actions.py
LEGACY_ACTION_TYPES: frozenset[ActionType] = frozenset({ActionType.DOCKER_PRUNE})
```

### Loud-fail on legacy config

When an operator tries to load an `inventory.yaml` that still has a `docker_prune:` key, the schema
validator raises `ConfigError` immediately with the migration command in the message:

```python
if "docker_prune" in self.actions:
    raise ConfigError(
        "inventory contains legacy 'docker_prune' action key. "
        "Run: uv run python -m errander --migrate-inventory <path> ..."
    )
```

This is intentional noise: the failure happens at config load, before any SSH connections are made.
The operator sees exactly what to run to fix it.

### migrate.py extension

`migrate_inventory()` now handles two cases:
1. **Top-level `docker_command_mode`** → translates to `actions.docker_hygiene` (already existed from the initial schema migration).
2. **`actions.docker_prune`** → renames the key to `docker_hygiene`, drops `direct_sudo` command_mode (not supported by docker_hygiene) with a stderr warning.

`direct_sudo` is deliberately not carried forward: docker_hygiene's per-object validation requires the wrapper. There is no safe equivalent of `direct_sudo` for docker_hygiene.

### Schema contradiction check for docker_hygiene

Following the same pattern that existed for docker_prune, the schema now validates:

```
docker_hygiene.enabled=true + command_mode=disabled → ConfigError
```

This catches inventories where someone enables docker_hygiene but forgets to set the command_mode to "wrapper". The error message explicitly says to set `command_mode: wrapper`.

## Code walkthrough

### What was deleted

| File | Reason |
|---|---|
| `errander/agent/subgraphs/docker_prune.py` | Entire bulk-prune subgraph — gone |
| `tests/agent/subgraphs/test_docker_prune.py` | Tests for deleted subgraph |
| `tests/agent/subgraphs/test_docker_prune_modes.py` | Wrapper/direct_sudo mode tests |
| `tests/agent/subgraphs/test_docker_prune_scope.py` | Scope (aggressive/safe) tests |
| `scripts/install-docker-wrappers.sh` | Installer for old wrappers (v1) — replaced by install-docker-wrappers-v2.sh |
| `tests/scripts/test_install_docker_wrappers.py` | Tests for old installer |

### What was updated in source

| File | Change |
|---|---|
| `errander/agent/subgraphs/__init__.py` | Removed docker_prune from BUILTIN_ACTIONS (now 6 entries) |
| `errander/models/actions.py` | DOCKER_PRUNE kept in enum, not in ACTION_RISK_TIERS; LEGACY_ACTION_TYPES added |
| `errander/config/schema.py` | docker_prune key raises ConfigError; docker_hygiene contradiction check added |
| `errander/config/migrate.py` | actions.docker_prune renamed to docker_hygiene; direct_sudo dropped with warning |
| `errander/agent/vm_graph.py` | docker_prune dispatch branch and _run_docker_prune() function removed |
| `errander/execution/privilege.py` | docker_prune_wrapper and docker_prune_direct entries removed from REQUIRED_BINARIES_BY_ACTION |
| `errander/execution/target_validation.py` | docker_prune probe section removed; only docker_hygiene probe remains |
| `errander/safety/rollback.py` | _rollback_docker_prune() and its dispatch entry removed |

### Test updates

Tests that checked "docker_prune is in BUILTIN_ACTIONS" became "docker_prune is not in BUILTIN_ACTIONS".
Tests that passed `docker_prune` to schema validators now expect `ConfigError`.
Tests that expected migrate.py to produce `actions.docker_prune` now expect `actions.docker_hygiene`.
The `test_all_action_types_have_risk_tiers` test was updated to skip `LEGACY_ACTION_TYPES`.

## Gotchas

**The enum value stays.** `ActionType.DOCKER_PRUNE` is still importable and usable. Tests that
check "docker_prune is not planned" still pass because `decisions.py` / `vm_graph.py` no longer
dispatch it. But tests like `filter_applicable_actions(list(ActionType), ...)` still return
DOCKER_PRUNE in their output when docker is available — this is a minor semantic debt that Session 4
should clean up (`DEFAULT_PRIORITY`, `_is_action_applicable` in decisions.py).

**migrate.py command_mode for disabled:** `docker_command_mode: disabled` maps to
`{enabled: false, command_mode: wrapper}` — not `command_mode: disabled`. This is intentional:
the migrated file represents "docker_hygiene is not active but the wrapper is set up for when
you want to enable it." The operator can still set `command_mode: disabled` manually.

## Quiz

1. Why is `ActionType.DOCKER_PRUNE` kept in the enum if docker_prune is removed?
2. What happens when you load an inventory that has `actions.docker_prune:` in it?
3. Why does `docker_command_mode: disabled` produce `command_mode: wrapper` (not `disabled`) in the migrated output?
4. Why does docker_hygiene not support `direct_sudo`, even as a lab shortcut?
