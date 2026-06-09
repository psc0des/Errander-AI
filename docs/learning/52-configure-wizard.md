# 52 — Enterprise Inventory Wizard + Comment-Preserving YAML Updates

## What was built and why

`scripts/configure.sh` generated a bare 9-line `inventory.yaml` stub with no comments,
no actions block, and no optional fields. Operators had no guidance on valid values,
available options, or how to enable actions like `docker_hygiene` or `service_restart`.

This feature replaces the bash stub with a full Python interactive wizard
(`errander/config/inventory_wizard.py`) that generates a richly annotated inventory file —
enterprise-grade out of the box with every field documented inline.

A second fix: `errander/config/configure.py` (Node Exporter setup) was using
`yaml.safe_load + yaml.dump` to update `node_exporter:` values, which stripped every
comment from the file on re-run. Replaced with `ruamel.yaml` for comment-preserving
round-trips.

## Key concepts used

### Python string building vs. yaml.dump for generation

When generating a YAML file that must have rich inline comments, `yaml.dump` is the
wrong tool — it produces semantically correct output but strips all comments and may
reorder keys. For initial generation, build the string directly:

```python
def _render_env(env: EnvData) -> str:
    lines: list[str] = []
    lines.append(f"  {env.name}:")
    lines.append("")
    lines.append("    # ── SSH ─────────────────────────────────────────────")
    lines.append(f"    ssh_user: {env.ssh_user}           # SSH user for all targets")
    lines.append(f"    ssh_key_path: {env.ssh_key_path}")
    ...
    return "\n".join(lines)
```

This gives complete control over comment placement, indentation, and section headers.
The trade-off: the renderer must stay in sync with the schema.

### ruamel.yaml for round-trip updates

When updating an existing YAML file (e.g., setting `node_exporter: true` after probing),
`yaml.dump` destroys comments. `ruamel.yaml` preserves them:

```python
from ruamel.yaml import YAML

def _update_inventory_yaml(inventory_path, results):
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # prevent unwanted line wrapping

    with open(inventory_path, encoding="utf-8") as fh:
        data = ryaml.load(fh)          # loads into CommentedMap (not plain dict)

    for env_name, env_data in (data.get("environments") or {}).items():
        ...
        target["node_exporter"] = result  # mutate in place

    with open(inventory_path, "w", encoding="utf-8") as fh:
        ryaml.dump(data, fh)              # writes back preserving comments
```

`ryaml.load` returns a `CommentedMap` (a dict subclass that carries comment metadata).
Mutations to this object preserve the surrounding comments when dumped back.

`ryaml.width = 4096` is important — without it ruamel.yaml wraps long inline comments
at 80 characters, producing broken formatting.

### Dataclasses for wizard-internal state

The wizard collects data through interactive prompts and needs to hold it in memory
before rendering. Plain dataclasses (not Pydantic) are the right tool here — the
wizard is an input layer, not a validation layer:

```python
@dataclass
class TargetData:
    host: str
    name: str
    os_family: str
    tags: list[str] = field(default_factory=list)
    critical_services: list[str] = field(default_factory=list)
    disable_docker_hygiene: bool = False
    service_restart_units: list[str] = field(default_factory=list)
```

Pydantic validation happens *after* rendering — the wizard calls
`InventoryConfig.model_validate(yaml.safe_load(rendered))` before writing. This
catches any contradiction between wizard logic and the schema (e.g., trying to
set `service_restart.enabled: true` with empty `restartable_units`).

### Bash-Python handoff via temp file

`configure.sh` is a 700+ line bash wizard. The inventory step is now a Python
subprocess, but subsequent steps in bash need `$ENV_NAME`, `$SSH_KEY_PATH`, and
`$VM_COUNT`. The handoff: Python writes a temp file at `~/.errander_wizard_result`,
bash sources it:

```bash
# Python writes:
# ERRANDER_RESULT_ENV_NAME=production
# ERRANDER_RESULT_SSH_KEY_PATH=~/.ssh/errander_prod
# ERRANDER_RESULT_VM_COUNT=3

_wiz_result="${HOME}/.errander_wizard_result"
if [ -f "$_wiz_result" ]; then
    _env_line=$(grep "^ERRANDER_RESULT_ENV_NAME=" "$_wiz_result" | cut -d'=' -f2-)
    [ -n "$_env_line" ] && ENV_NAME="$_env_line"
    ...
fi
```

One subtlety: `$VM_COUNT` from the result file is 0 when the user keeps their
existing inventory ("keep existing" path writes `VM_COUNT=0`). The bash SSH bootstrap
check uses `_inv_count=$(grep -c "^\s*- host:" inventory.yaml)` instead — this
reflects the actual file state regardless of which wizard path was taken.

### service_restart always env-level false

The schema rejects `service_restart.enabled: true` with an empty `restartable_units`
list. The wizard always generates `enabled: false` at the env level; when the user
configures service_restart units for a specific VM, those appear as active per-target
overrides. This is correct by design: CLAUDE.md mandates that `restartable_units` be
set per-target (different VMs run different services).

### docker_hygiene command_mode

The wizard maps the user's yes/no answer to the correct YAML output:

| User answer | `enabled` | `command_mode` |
|---|---|---|
| No (default) | `false` | `disabled` |
| Yes | `true` | `wrapper` |

`direct_sudo` was removed in v1.1 — the wrapper is the only supported mode.

## Code walkthrough

### Entry point

```python
def main() -> None:
    inv_path = Path("inventory.yaml")
    if inv_path.exists():
        # Show summary → keep [1] or replace [2]
        summary = _summarise_existing(inv_path)
        print(summary)
        choice = _prompt_val("Keep existing [1] or replace [2]?", default="1")
        if choice != "2":
            _write_result(...)  # write result vars for bash to source
            return

    envs: list[EnvData] = []
    n = 1
    while True:
        env = _wizard_env(n)     # prompts for one environment
        # collect VMs
        t = 1
        while True:
            target = _wizard_target(env, t)
            env.targets.append(target)
            if not _prompt_yn("Add another VM?", default=True):
                break
            t += 1
        envs.append(env)
        if not _prompt_yn("Add another environment?", default=False):
            break
        n += 1

    rendered = _render_inventory_yaml(envs, datetime.now(UTC).date().isoformat())
    InventoryConfig.model_validate(yaml.safe_load(rendered))  # validate before write
    Path("inventory.yaml").write_text(rendered, encoding="utf-8")
    _write_result(envs[0].name, envs[0].ssh_key_path, sum(len(e.targets) for e in envs))
```

### Render pipeline

`_render_inventory_yaml` → `_render_env` × N → `_render_target` × M per env.

`_render_target` handles three cases:
1. No overrides at all → renders commented-out template block
2. `disable_docker_hygiene=True` → renders active `docker_hygiene: {enabled: false}` override
3. `service_restart_units` non-empty → renders full active `service_restart:` block

## Gotchas encountered

### date.today() doesn't accept tz=

`date.today(tz=timezone.utc)` raises `TypeError: today() does not accept any arguments`.
The correct form: `datetime.now(UTC).date().isoformat()`.

### _count_vms ordering — helper must precede callers

The "keep existing" path called `_count_vms(path)` to write `VM_COUNT` to the result
file. The function was defined below the call site — Python raises `NameError` at
runtime (not at parse time) for functions inside a function, but at module level a
forward reference to a not-yet-defined function fails on call. Fixed by defining
`_count_vms` before `main`.

### _inv_count must be set before SSH bootstrap

In `configure.sh`, the SSH bootstrap block at step 2 uses `${_inv_count:-0}`. The
`_inv_count=$(grep -c ...)` assignment was initially placed in the Done banner section
(much later in the script) — so the bootstrap check always saw 0 and never ran.
Fixed: moved `_inv_count` computation to immediately after the wizard call in step 2.

### ruff: bare f-string with no placeholder

`f"        # actions:"` triggered `F541` (f-string without any placeholders). Fix:
remove the `f` prefix.

### ruff: ruamel import style

`from ruamel.yaml import YAML as RuamelYAML` triggered `N811` (constant imported as
non-constant). Importing as `YAML` directly (`from ruamel.yaml import YAML`) resolves
it.

## Quiz yourself

1. Why is `yaml.dump` wrong for initial inventory generation but `ruamel.yaml` right
   for subsequent updates?

2. What does `ryaml.width = 4096` do and why is the default a problem?

3. Why does the wizard always generate `service_restart.enabled: false` at env level,
   even when the user says they want service_restart enabled?

4. What does `_count_vms` use to count VMs, and why does `$VM_COUNT` from the result
   file not work for the SSH bootstrap check?

5. The schema validates `service_restart.enabled: true` requires non-empty
   `restartable_units`. How does the wizard satisfy this constraint while still
   letting users configure service restart per-VM?
