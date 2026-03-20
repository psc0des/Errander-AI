# 02 — Settings Loader & Config System

## What Was Built

A three-layer configuration system that loads and validates:
1. **Inventory YAML** — VM targets grouped by environment with inheritance
2. **Settings YAML** — Agent tuning parameters (timeouts, retries, thresholds)
3. **Environment variables** — Secrets (tokens, keys, URLs)

## Why This Architecture

The spec defines a config inheritance model: `Global defaults → Environment settings → Host overrides`. A production VM fleet has different maintenance policies than dev — and individual hosts may need per-host overrides (e.g., a database server needing a different SSH user).

Secrets (Slack token, LLM API key) must NEVER be in YAML files (they'd end up in git). So secrets come from env vars while operational tuning lives in YAML.

## Key Concepts

### Pydantic for YAML Schema Validation

Instead of hand-writing validation logic, we define Pydantic `BaseModel` classes that mirror the YAML structure. Pydantic does type coercion, required field checks, and custom validation automatically.

```python
class TargetSchema(BaseModel):
    host: str
    name: str
    os_family: str

    @field_validator("os_family")
    @classmethod
    def validate_os_family(cls, v: str) -> str:
        allowed = {"ubuntu", "debian", "rhel"}
        if v.lower() not in allowed:
            raise ValueError(f"os_family must be one of {allowed}")
        return v.lower()
```

The `@field_validator` decorator runs custom logic after Pydantic's built-in type checking. We normalize `os_family` to lowercase here so the rest of the codebase doesn't need to handle case variations.

### Environment → Host Inheritance

The inventory YAML groups targets under environments:

```yaml
environments:
  production:
    ssh_user: automaint          # ← environment-level default
    ssh_key_path: ~/.ssh/prod
    approval_policy: strict
    targets:
      - host: 10.0.1.10
        name: web-01
        os_family: ubuntu        # inherits ssh_user from env
      - host: 10.0.1.20
        name: db-01
        os_family: rhel
        ssh_user: automaint-db   # ← overrides env-level
```

Resolution in `_resolve_single_target()`:

```python
ssh_user = target.ssh_user if target.ssh_user is not None else env.ssh_user
```

Host fields use `None` as the sentinel for "not set" — if `None`, inherit from environment. This is why `TargetSchema` has `ssh_user: str | None = None`.

### Settings Layering

Three sources, in priority order:
1. **Environment variables** (highest — secrets + overrides)
2. **settings.yaml** (operational tuning)
3. **Hardcoded defaults** (lowest — sensible fallbacks)

```python
approval_timeout_seconds=_load_env_int(
    "AUTOMAINT_APPROVAL_TIMEOUT",
    agent.approval_timeout_seconds if agent else 1800,  # YAML or default
)
```

The `_load_env_int` helper returns the default if the env var is unset, but raises `ValueError` if set to a non-integer — fail fast on misconfiguration.

### VM ID Convention

VMs get a composite ID: `{env_name}/{target_name}` (e.g., `"production/web-prod-01"`). This makes IDs globally unique even if two environments have hosts with the same name, and makes it immediately clear which environment a VM belongs to in logs/audit trails.

## Gotchas

1. **YAML `null` vs missing**: In YAML, `maintenance_window: null` is explicit None, while omitting the key means the field's default applies. Pydantic handles both correctly with `str | None = None`.

2. **Frozen dataclass + dict field**: `VMTarget` is `frozen=True` but has a `tags: dict` field. This works because frozen only prevents reassignment of the field itself, not mutation of the dict's contents. The dict is mutable — be careful not to mutate it after creation.

3. **`from __future__ import annotations`**: Every module imports this for PEP 604 union syntax (`str | None`) to work at runtime in Python 3.12. Without it, you'd need `Optional[str]`.

## Testing Patterns

- **`tmp_path` fixture**: pytest provides a temporary directory per test. We write YAML files there to test file loading without touching real files.
- **`monkeypatch`**: Used to set/clear env vars without affecting the real environment. Essential for testing `load_settings()`.
- **Validation error tests**: Use `pytest.raises(ValidationError, match="field_name")` to verify specific fields are rejected.

## Quiz Yourself

1. Why do secrets come from env vars but tuning parameters from YAML?
2. What happens if a host's `ssh_user` is set to `None` vs not set at all in the YAML?
3. Why does `_load_env_int` raise on invalid values instead of falling back to the default?
4. How would you add a new environment-level field that hosts can override?
5. What prevents the same target name in two different environments from colliding?
