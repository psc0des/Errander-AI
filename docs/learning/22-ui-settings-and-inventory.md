# 22 — UI Settings & Inventory Management (Phase 4B)

## What was built and why

Phase 4B adds two capabilities on top of the existing aiohttp web UI:

1. **Settings UI** (`/ui/settings`) — operators can change LLM model, temperature, timeout, and approval timeout at runtime without restarting the agent or editing YAML/env files. Changes are persisted in SQLite and take precedence over YAML (but env vars still win).

2. **Inventory UI** (`/ui/inventory`) — operators can disable YAML-defined VMs or add ad-hoc VMs entirely through the browser. No need to redeploy to exclude a flaky box from the next maintenance run.

Both features sit behind **HTTP Basic Auth** on all `/ui/*` routes.

## Key concepts

### Precedence chain: env > DB > YAML > default

```
ERRANDER_LLM_MODEL (env var)     ← highest priority, always wins
settings_overrides table (DB)    ← UI writes here
settings.yaml llm.model          ← lowest static config
"" (empty string default)        ← Safety net
```

`load_settings()` was extended to accept a pre-fetched `db_overrides: dict[str, str]` argument. Why pre-fetched and not `OverridesStore` directly? Because `load_settings()` is synchronous — it runs before the event loop in `_build_components()`. Accepting the already-fetched dict keeps the function sync and makes testing simple.

### DB schema — two tables

```sql
-- settings_overrides: runtime LLM/approval settings
CREATE TABLE settings_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'ui',
    note TEXT DEFAULT ''
)

-- inventory_overrides: disable YAML VMs, or add ad-hoc VMs
CREATE TABLE inventory_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env_name TEXT NOT NULL,
    vm_name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('yaml_override', 'db_addition')),
    disabled INTEGER NOT NULL DEFAULT 0,
    ...
    UNIQUE(env_name, vm_name)
)
```

The `source CHECK` constraint enforces the two-value enum at DB level — no application-layer validation needed.

### Inventory merge in `run_env_batch()`

```
YAML targets
    │
    ├─ filter out names in {yaml_override rows where disabled=True}
    │
    └─ append db_addition rows where disabled=False
    │
    ▼
effective targets → batch graph
```

The `_name` field (VM name string) is a temporary enrichment added to each YAML target dict so the filter can work by name without re-parsing. It's `del`-ed before the list is passed to the graph.

### HTTP Basic Auth middleware

```python
@web.middleware
async def _basic_auth_middleware(request, handler):
    if not request.path.startswith("/ui/"):
        return await handler(request)
    if not ui_user or not ui_password:
        return await handler(request)  # auth disabled
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="errander"'})
    decoded = base64.b64decode(auth_header[6:]).decode()
    supplied_user, _, supplied_pass = decoded.partition(":")
    ok = (
        secrets.compare_digest(supplied_user, ui_user) and
        secrets.compare_digest(supplied_pass, ui_password)
    )
    if not ok:
        raise web.HTTPUnauthorized(...)
    return await handler(request)
```

`secrets.compare_digest()` is critical: it runs in constant time regardless of where the strings differ, preventing timing-oracle attacks.

### Source tracking in Settings

Every field has a source label: `"env"`, `"db"`, `"yaml"`, or `"default"`. The UI uses these to colour-code fields:

- Red/locked = env var (operator cannot override — env vars win)
- Blue = DB override (UI-managed, can be Reset)
- Green = YAML config (read-only in UI)
- Grey = default

### LLM "Test Connection" button

`_ui_settings_test_llm()` instantiates a temporary `LLMClient` with the *current effective* settings (after applying any just-POSTed DB overrides) and calls `check_endpoint()`. This lets operators verify a new model is reachable before committing.

## Code walkthrough

### `errander/safety/overrides.py`

`OverridesStore` is an async SQLite wrapper using `aiosqlite`. It supports the context manager protocol (`async with OverridesStore(path) as store`) for safe resource cleanup. All writes are upserts (INSERT … ON CONFLICT DO UPDATE) so every operation is idempotent.

Secret values are encrypted via `SecretsManager.encrypt()` before storage and decrypted transparently in `get_settings_overrides()`.

### `errander/config/settings.py` — inner helpers

```python
def _str(env_key: str, yaml_val: str | None, default: str = "") -> str:
    if os.environ.get(env_key) is not None:
        return _load_env_str(env_key, default)   # env wins
    if env_key in _db:
        return _db[env_key]                       # DB second
    return yaml_val if yaml_val is not None else default  # YAML/default last
```

The pattern repeats for `_int_field`, `_float_field`, `_bool_field`. Each checks env → DB → YAML → default in that order.

### `errander/main.py` — inventory merge

```python
yaml_targets = [{"vm_id": ..., "hostname": ..., "_name": t.name, ...} for t in env_schema.targets]

db_overrides = await overrides_store.get_inventory_overrides(env_name) if overrides_store else []

disabled_names = {str(row["vm_name"]) for row in db_overrides
                  if row["source"] == "yaml_override" and bool(row["disabled"])}

db_additions = [row for row in db_overrides
                if row["source"] == "db_addition" and not bool(row["disabled"])]

targets = [t for t in yaml_targets if t["_name"] not in disabled_names]
for t in targets:
    del t["_name"]  # strip temp field before handing to graph

for row in db_additions:
    targets.append({
        "vm_id": f"{env_name}/{row['vm_name']}",
        "hostname": str(row["host"] or ""),
        "ssh_user": str(row["ssh_user"] or env_schema.ssh_user),
        ...
    })
```

## Gotchas

- **`load_settings()` must stay synchronous.** The temptation is to make it `async` so it can query the DB itself. Don't — it's called before the event loop in component setup. Pre-fetch the DB overrides in `async_main()` and pass them in.

- **`_name` field pollution.** The YAML target dict gets a temporary `_name` key for filtering. If you forget to `del t["_name"]` before passing to the graph, the graph state will contain unknown fields that may cause validation errors downstream.

- **Auth disabled when credentials are empty.** If `ERRANDER_UI_USER` / `ERRANDER_UI_PASSWORD` are not set, the middleware skips auth entirely and the UI is open. This is intentional for local development — but document it clearly.

- **`secrets.compare_digest()` requires equal-length strings** in Python < 3.9 — actually it works on any strings in 3.9+, but the constant-time property only holds when both strings have the same length. For usernames that differ in length, the comparison still short-circuits early in the underlying C implementation. The practical risk is low for a DevOps internal tool, but worth knowing.

- **SQLite `CHECK` constraint on `source`.** The DB enforces `source IN ('yaml_override', 'db_addition')`. Passing any other value raises `aiosqlite.IntegrityError`. This is better than an application-layer enum because it protects against bugs in any future DB-direct access.

## Quiz yourself

1. Why is `load_settings()` synchronous when `OverridesStore` is async?
2. What happens if `ERRANDER_UI_USER` is set but `ERRANDER_UI_PASSWORD` is empty?
3. Why use `secrets.compare_digest()` instead of `==` for password comparison?
4. A VM is in `inventory.yaml` AND has a `yaml_override` row with `disabled=False`. Is it in the effective target list?
5. An operator adds a VM via the UI (`db_addition`), then sets `disabled=True` for it in the same UI. Does it appear in the next batch?
6. What colour does the Settings UI show for a field whose value comes from `ERRANDER_LLM_MODEL` env var?
7. If the DB has `ERRANDER_LLM_MODEL=gpt-4o` and the env has `ERRANDER_LLM_MODEL=qwen3`, which wins?
