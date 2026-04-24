# Phase 4 — LLM Provider Flexibility, Secrets Encryption, UI Settings & Inventory

**Status:** Complete (2026-04-19) — 799 tests passing, lint clean
**Created:** 2026-04-19
**Scope:** Three phases, ~2 days total

---

## Motivation

1. **LLM provider is hardcoded to vLLM/Qwen3.** Model ID `Qwen/Qwen3-8B-AWQ` is baked into `errander/integrations/llm.py`, and the `/no_think` prompt prefix is Qwen3-specific. Users running OpenAI, Anthropic, Groq, Ollama, or any other OpenAI-compatible API can't use the agent without code changes.
2. **Sensitive values live in plaintext.** Slack tokens, LLM API keys, and any future per-host credentials are stored unencrypted in `.env` files and YAML. Principle: anything sensitive must be encrypted regardless of source (YAML, env var, or DB).
3. **Operators need runtime controls.** Day-to-day tasks — switching LLM provider, disabling a flaky VM, adding an ad-hoc host for a one-off run — currently require SSH + YAML edit + restart. A narrow UI for these operations closes the biggest friction gap.

Everything else (SSH timeouts, wave thresholds, scheduler cron, drift flags) stays in YAML — set once at deployment, not operator day-to-day.

---

## Phase A — LLM Provider Flexibility (~1 hour)

**Goal:** LLMClient works with any OpenAI-compatible endpoint. No hardcoded model. No Qwen3-specific prompt injection.

### Tasks

- [ ] **A1.** In `errander/integrations/llm.py`:
  - Remove the module-level constant `_MODEL_ID = "Qwen/Qwen3-8B-AWQ"`
  - Add `model: str` (required, no default) and `temperature: float = 0.1` parameters to `LLMClient.__init__`
  - Store as `self._model` and `self._temperature`; use in both `complete()` and `check_endpoint()` in place of `_MODEL_ID` / literal `0.1`
  - Remove the `thinking: bool` parameter from `complete()`. Remove the `full_prompt = prompt if thinking else f"/no_think\n\n{prompt}"` line. Always send `prompt` as-is.
  - Update the module docstring: strip vLLM/Qwen3 references; say "OpenAI-compatible endpoint (vLLM, OpenAI, Anthropic, Groq, Ollama, etc.)"

- [ ] **A2.** In `errander/config/settings.py`:
  - Add `llm_model: str = ""` and `llm_temperature: float = 0.1` to the `Settings` dataclass
  - Load from env: `ERRANDER_LLM_MODEL` (via `_load_env_str`), `ERRANDER_LLM_TEMPERATURE` (via a new `_load_env_float` helper)
  - Load from YAML: `llm.model` and `llm.temperature`
  - Precedence stays: env > YAML > default

- [ ] **A3.** In `errander/config/schema.py`:
  - Add `model: str = ""` and `temperature: float = 0.1` to `LLMSettingsSchema`
  - Add a `@field_validator` for `temperature` that enforces `0.0 <= temperature <= 2.0`

- [ ] **A4.** In `errander/main.py`:
  - Update `_build_components()` to pass `model=settings.llm_model, temperature=settings.llm_temperature` to `LLMClient(...)`
  - Update `run_llm_check()` same way
  - If `settings.llm_base_url` is set but `settings.llm_model` is empty, log an error and exit with code 1 — the model is now required

- [ ] **A5.** Update `errander/agent/decisions.py` call sites:
  - Find all `client.complete(...)` calls that pass `thinking=True` or `thinking=False`
  - Remove the `thinking` kwarg from all call sites
  - No other changes — the behavior on cloud APIs is unchanged (they didn't understand `thinking` anyway); on Qwen3 reports become slightly slower but still correct

- [ ] **A6.** Update `config/settings.yaml` and `example/settings.yaml`:
  - Add under the existing `llm:` block:
    ```yaml
    llm:
      # Required: model ID (provider-specific — see docs/LLM-PROVIDERS.md)
      model: "Qwen/Qwen3-8B-AWQ"

      # Sampling temperature. 0.0 = deterministic, 0.1 = low variance (default),
      # up to 2.0 for high variance. Keep low for structured JSON responses.
      temperature: 0.1

      timeout_seconds: 30
      max_retries: 2
    ```

- [ ] **A7.** Update existing tests in `tests/integrations/test_llm.py`:
  - Every `LLMClient(...)` constructor call now needs `model="test-model"` (mechanical fix)
  - Remove any test that asserts `/no_think` is prepended to the prompt
  - Add one new test: `complete()` sends the literal prompt without modification (no `/no_think` injected)
  - Add one new test: `complete()` uses `self._temperature` in the API call (not the literal 0.1)

- [ ] **A8.** Create `docs/LLM-PROVIDERS.md`:
  - Ready-to-paste `.env` blocks for: vLLM/Qwen3, OpenAI, Anthropic (OpenAI-compat endpoint), Groq, Together, Ollama
  - One short paragraph per provider explaining when to pick it (privacy → vLLM/Ollama; cost → Groq/gpt-4o-mini; quality → Claude/GPT-4o)
  - Include the `--check-llm` verification command for each

- [ ] **A9.** Update `docs/SETUP.md`:
  - Split Step 2 into **"Option A: Cloud API"** (skip to Step 3 after setting 3 env vars) vs **"Option B: Self-hosted vLLM"** (current content)
  - Update the `.env` snippet in Step 5 to include `ERRANDER_LLM_MODEL`
  - Add `ERRANDER_LLM_MODEL` and `ERRANDER_LLM_TEMPERATURE` to the Environment Variables Reference table

### Acceptance criteria

- `uv run pytest` passes
- `uv run ruff check .` passes
- `uv run python -m errander --check-llm` works against both a vLLM endpoint and at least one cloud API (OpenAI or equivalent), with the only difference being the `.env` file
- No string `"Qwen/Qwen3-8B-AWQ"` or `"/no_think"` remains in `errander/` (only in docs as examples)

---

## Phase A.5 — Secrets Encryption Foundation (~4 hours)

**Goal:** Any sensitive value in YAML, env vars, or DB can be stored in an `enc:v1:<ciphertext>` format. One class, `SecretsManager`, handles encrypt/decrypt everywhere.

### Tasks

- [ ] **S1.** Add the `cryptography` dependency:
  - In `pyproject.toml`, add `"cryptography>=42.0"` to the main dependencies
  - Run `uv sync`

- [ ] **S2.** Replace `errander/integrations/secrets.py` with the `SecretsManager` class:

  ```python
  class SecretsManager:
      """Encrypt/decrypt sensitive values using Fernet (AES-128-CBC + HMAC-SHA256).

      Values are stored as "enc:v1:<base64-fernet-token>". The v1 prefix allows
      future algorithm rotation without breaking existing ciphertexts.

      The master key comes from ERRANDER_SECRETS_KEY (32-byte url-safe base64).
      Missing key raises MasterKeyMissingError on first encrypt/decrypt — callers
      that don't need secrets can instantiate with require_key=False.
      """

      PREFIX = "enc:v1:"

      def __init__(self, master_key: str | None = None, require_key: bool = True): ...
      def encrypt(self, plaintext: str) -> str: ...
      def decrypt(self, ciphertext: str) -> str: ...
      def is_encrypted(self, value: str) -> bool: ...  # value.startswith(PREFIX)
      def decrypt_if_needed(self, value: str) -> str: ...  # transparent pass-through for plaintext

      @staticmethod
      def generate_key() -> str: ...  # Fernet.generate_key().decode()
  ```

  - Keep the existing `get_secret()` helper as a thin wrapper for backwards compatibility — it should call `SecretsManager().decrypt_if_needed(os.environ[name])`
  - Exceptions: `MasterKeyMissingError`, `DecryptionError` (both inherit `ValueError`)

- [ ] **S3.** YAML loader integration in `errander/config/schema.py`:
  - In `validate_settings()` and `validate_inventory()`, after PyYAML parses the file, walk the resulting dict and call `SecretsManager().decrypt_if_needed(v)` on every string value
  - Fields explicitly known to hold secrets (`llm.api_key`, `slack.bot_token`) must decrypt successfully — if the value starts with `enc:v1:` but decryption fails, raise a clear error with the field path
  - Non-secret string fields that happen to start with `enc:v1:` (unlikely but possible) also decrypt — this is intentional; we don't maintain a field whitelist

- [ ] **S4.** Env var loader integration in `errander/config/settings.py`:
  - In `_load_env_str()`, after reading the env var, call `SecretsManager().decrypt_if_needed(value)`
  - Same behavior as YAML: plaintext passes through, `enc:v1:...` decrypts

- [ ] **S5.** CLI commands in `errander/main.py`:
  - Add `--generate-secrets-key` flag: calls `SecretsManager.generate_key()`, prints the result with instructions:
    ```
    ERRANDER_SECRETS_KEY=<generated-key>

    Save this in a 0600-permissioned EnvironmentFile or your secrets manager.
    Never commit it to git. Losing this key means losing all encrypted values.
    ```
  - Add `--encrypt VALUE` flag: calls `SecretsManager().encrypt(VALUE)`, prints the resulting `enc:v1:...` blob
  - Both flags exit immediately; no scheduler / metrics server startup
  - `--encrypt` requires `ERRANDER_SECRETS_KEY` to be set; `--generate-secrets-key` does not

- [ ] **S6.** Log redaction filter:
  - Create `errander/observability/redaction.py` with a `SecretsRedactingFilter(logging.Filter)` class
  - Redacts strings matching:
    - `sk-[a-zA-Z0-9_-]{20,}` (OpenAI / Anthropic / Groq keys)
    - `xoxb-[a-zA-Z0-9-]{40,}` (Slack bot tokens)
    - `enc:v1:[A-Za-z0-9_=-]+` (encrypted blobs — already safe, but don't leak them either)
    - Fernet key format (just in case)
  - Attach the filter to the root logger in `main.py` `logging.basicConfig` setup
  - Redaction replaces the matched string with `<redacted>` in log messages

- [ ] **S7.** Tests in `tests/integrations/test_secrets.py`:
  - Round-trip: `encrypt(x)` then `decrypt(...)` returns `x`
  - `decrypt_if_needed(plaintext)` returns plaintext unchanged
  - `decrypt_if_needed(enc_value)` returns decrypted
  - `encrypt` output starts with `enc:v1:`
  - Missing master key raises `MasterKeyMissingError` on `encrypt()` but NOT on `decrypt_if_needed(plaintext)` (so non-secret configs work without the key)
  - Corrupted ciphertext raises `DecryptionError`
  - `generate_key()` produces a valid Fernet key that can be used by a new instance

- [ ] **S8.** Tests in `tests/observability/test_redaction.py`:
  - Log message containing `sk-proj-abc123...xyz` is redacted
  - Log message containing `xoxb-1234567890-...` is redacted
  - Log message containing `enc:v1:gAAAAA...` is redacted
  - Normal log messages pass through unchanged

- [ ] **S9.** Tests in `tests/config/test_secrets_loading.py`:
  - YAML with `api_key: enc:v1:...` decrypts correctly when master key is set
  - YAML with `api_key: enc:v1:...` raises clear error when master key is missing
  - Env var `ERRANDER_LLM_API_KEY=enc:v1:...` decrypts correctly
  - Env var `ERRANDER_LLM_API_KEY=sk-plaintext` passes through unchanged

- [ ] **S10.** Documentation:
  - New `docs/SECRETS.md` covering:
    - Why encrypt at rest (threat model)
    - How to generate and store the master key (systemd EnvironmentFile example)
    - How to encrypt a secret (`--encrypt` CLI)
    - YAML example with inline `enc:v1:` values
    - What happens if the master key is lost (all encrypted values are unrecoverable)
  - Update `CLAUDE.md` secrets section: mention the `enc:v1:` format and `ERRANDER_SECRETS_KEY`
  - Update `docs/SETUP.md` Step 5 (.env configuration) to reference `docs/SECRETS.md` for users who want to encrypt secrets in their `.env` file

### Acceptance criteria

- All existing tests pass without changes (backwards compatible — plaintext values still work)
- New tests from S7/S8/S9 all pass
- `uv run python -m errander --generate-secrets-key` and `--encrypt "test"` produce usable output
- An end-to-end test: set `ERRANDER_LLM_API_KEY=enc:v1:...` in `.env`, run `--check-llm`, confirm it decrypts and authenticates against the LLM

---

## Phase B — UI Settings + UI Inventory (~1.5 days)

**Goal:** Two new UI pages for the two most common operator tasks: switching LLM provider, and managing inventory (enable/disable/add ad-hoc VMs). All other settings stay YAML-only.

### Prerequisite: Basic Auth on `/ui/*`

Adding write endpoints makes unauthenticated UI access a security gap. Gate all `/ui/*` routes behind HTTP Basic Auth.

- [ ] **AUTH1.** In `errander/observability/metrics.py`, add an aiohttp middleware:
  - Reads `ERRANDER_UI_USER` and `ERRANDER_UI_PASSWORD` from env
  - If either is unset: log a WARNING on startup (`"UI auth disabled — set ERRANDER_UI_USER and ERRANDER_UI_PASSWORD"`) and allow all requests. This preserves the dev-mode open-access behavior but nudges ops toward auth.
  - If both set: require HTTP Basic Auth on every request whose path starts with `/ui/`. Other paths (`/health`, `/metrics`) remain open.
  - Use `secrets.compare_digest()` for the password comparison to avoid timing attacks
  - On auth failure: return 401 with `WWW-Authenticate: Basic realm="Errander-AI"` header

- [ ] **AUTH2.** Settings:
  - Add `ui_user: str = ""` and `ui_password: str = ""` to the `Settings` dataclass (loaded from env only — never from YAML/DB, since these bootstrap access to the DB editor)

- [ ] **AUTH3.** Tests in `tests/observability/test_ui_auth.py`:
  - Auth disabled (env unset) → `/ui` returns 200 without credentials
  - Auth enabled, no credentials → 401
  - Auth enabled, wrong credentials → 401
  - Auth enabled, correct credentials → 200
  - `/health` and `/metrics` remain open even with auth enabled

- [ ] **AUTH4.** Update `docs/SETUP.md` Monitoring section:
  - Note that setting `ERRANDER_UI_USER` and `ERRANDER_UI_PASSWORD` enables auth on `/ui/*`
  - Strongly recommend setting these in any deployment where the UI is reachable beyond localhost

### Database schema

- [ ] **DB1.** Extend `AuditStore` (or create a sibling `OverridesStore`) to create two new tables on `initialize()`:

  ```sql
  CREATE TABLE IF NOT EXISTS settings_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,                -- may be "enc:v1:..." for secret fields
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,            -- ISO8601 UTC
    updated_by TEXT NOT NULL DEFAULT 'ui',
    note TEXT DEFAULT ''
  );

  CREATE TABLE IF NOT EXISTS inventory_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env_name TEXT NOT NULL,
    vm_name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('yaml_override', 'db_addition')),
    disabled INTEGER NOT NULL DEFAULT 0,
    host TEXT,                           -- NULL for yaml_override (only flags behavior)
    ssh_user TEXT,
    ssh_key_path TEXT,
    os_family TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'ui',
    note TEXT DEFAULT '',
    UNIQUE(env_name, vm_name)
  );
  ```

- [ ] **DB2.** Create `errander/safety/overrides.py` with an `OverridesStore` class (async, same style as `AuditStore`):
  - `async def get_settings_overrides() -> dict[str, str]` — returns `{key: decrypted_value}` for all rows
  - `async def set_setting_override(key, value, is_secret, updated_by, note)` — encrypts value if `is_secret`, upserts
  - `async def delete_setting_override(key)` — removes a row (reverts to YAML/env)
  - `async def get_inventory_overrides(env_name: str) -> list[dict]` — returns all entries for an environment
  - `async def upsert_inventory_override(...)` — insert or update
  - `async def delete_inventory_override(env_name, vm_name)`

### Settings loader rewrite

- [ ] **SL1.** Update `errander/config/settings.py` `load_settings()`:
  - New precedence: **env vars > DB overrides > YAML > defaults** (DB in the middle — env still authoritative)
  - Accept an optional `overrides_store: OverridesStore | None = None` parameter; when provided, overlay DB overrides before env vars are applied
  - Expose which source each field came from via a `Settings.sources: dict[str, str]` side-channel (used by the UI to show "from env / DB / YAML / default")

- [ ] **SL2.** Hot-reload hook:
  - Add `Settings.reload_from_db(overrides_store)` that re-reads DB overrides and updates mutable fields in place
  - `LLMClient` should be re-instantiated from settings at the **start of each batch** (already close to this — `run_env_batch` calls `_build_components`)
  - Document which fields support hot reload (all LLM fields, approval timeout) and which require restart (scheduler cron, metrics port)

### UI: `/ui/settings` page

- [ ] **UI-S1.** New handler `_ui_settings` in `errander/observability/metrics.py`:
  - GET renders a form with the LLM section only:
    - `llm.base_url` (text input)
    - `llm.model` (text input with datalist of common presets: `Qwen/Qwen3-8B-AWQ`, `gpt-4o-mini`, `claude-sonnet-4-6`, `llama-3.3-70b-versatile`, `llama3.2`)
    - `llm.api_key` (password input; current value shown masked as `sk-...abcd` or "not set")
    - `llm.temperature` (number input, step 0.1, min 0, max 2)
    - `llm.timeout_seconds` (number input, min 1, max 600)
  - Each field shows a small source indicator: "from env (locked)" (non-editable) / "overridden in UI" / "from YAML" / "default"
  - Env-var-sourced fields are disabled in the form — ops locks override UI
  - Include a "Reset to default" button per field (clears the DB override)
  - Include a "Test connection" button that hits `/ui/settings/test-llm` (GET) which invokes `LLMClient.check_endpoint()` with the current form values

- [ ] **UI-S2.** POST handler `/ui/settings`:
  - Parses form, validates via existing Pydantic schema (`LLMSettingsSchema`) — returns 400 with field errors if invalid
  - For secret fields (`api_key`): encrypts via `SecretsManager` before storing, sets `is_secret=1`
  - For non-secret fields: stores plaintext, `is_secret=0`
  - Logs every change to the audit store as a new `EventType.SETTINGS_CHANGED` (add to the enum) with `detail = "{key}: <old> → <new>"` — redact old/new values if secret
  - Redirects back to `/ui/settings` with a success flash

- [ ] **UI-S3.** GET `/ui/settings/test-llm` endpoint:
  - Accepts query params for `base_url`, `model`, `api_key`, `temperature`
  - Instantiates a temporary `LLMClient` with those values
  - Calls `check_endpoint()` and returns the result as JSON
  - Used by the "Test connection" button via fetch

- [ ] **UI-S4.** Add `/ui/settings` to the navbar in the existing UI template

### UI: `/ui/inventory` page

- [ ] **UI-I1.** New handler `_ui_inventory` in `errander/observability/metrics.py`:
  - GET renders a grouped table per environment:
    - For each YAML-sourced VM: show name, host, os_family, and a toggle for enabled/disabled (default enabled)
    - For each DB-added VM: same columns plus "Edit" and "Delete" buttons; mark with a "+ ad-hoc" badge
    - Below each environment: an "Add VM" form (env auto-filled, fields: name, host, ssh_user, ssh_key_path, os_family)

- [ ] **UI-I2.** POST handlers:
  - `/ui/inventory/toggle` — body `{env_name, vm_name, disabled: bool}` → upserts a `yaml_override` row
  - `/ui/inventory/add` — body: full VM fields → inserts a `db_addition` row; fails with 400 if `(env_name, vm_name)` already exists
  - `/ui/inventory/edit/{env_name}/{vm_name}` — updates fields on a `db_addition` row (cannot edit `yaml_override` rows — those are read-only from YAML)
  - `/ui/inventory/delete/{env_name}/{vm_name}` — deletes a `db_addition` row (cannot delete `yaml_override` — only toggle disabled)
  - All writes go through audit log as `EventType.INVENTORY_CHANGED`

- [ ] **UI-I3.** Validation for inventory additions:
  - `vm_name`: required, non-empty, no whitespace
  - `host`: required — accept IPv4, IPv6, or DNS name; reject obviously malformed strings
  - `os_family`: must be one of `ubuntu`, `debian`, `rhel`, `centos`, `amazon`
  - `ssh_key_path`: required — verify the file exists on disk at save time and log a warning if not (don't reject — path may be valid at batch time)

- [ ] **UI-I4.** Add `/ui/inventory` to the navbar

### Inventory merge at batch start

- [ ] **MERGE1.** In `errander/main.py` `run_env_batch()`:
  - Before building `targets` from `env_schema.targets`, query `OverridesStore.get_inventory_overrides(env_name)`
  - Apply merges:
    - For each YAML target: if a matching `yaml_override` row has `disabled=1`, exclude
    - For each `db_addition` row (not disabled): build a target dict and append
  - Log the effective target count vs YAML count at INFO level: `"Inventory: 12 YAML targets, 2 disabled via UI, 3 added via UI, effective: 13"`

- [ ] **MERGE2.** Extend batch audit events:
  - When the batch starts, log an `EventType.BATCH_STARTED` event with `detail` containing the effective inventory summary (counts, not values — avoid leaking ad-hoc host details unnecessarily)

### Tests

- [ ] **T1.** Unit tests for `OverridesStore` (`tests/safety/test_overrides.py`):
  - CRUD for settings overrides (plaintext and secret)
  - CRUD for inventory overrides (both sources)
  - Secret settings stored encrypted in DB, returned decrypted by getter
  - Unique constraint on (env_name, vm_name)

- [ ] **T2.** Unit tests for settings loader precedence (`tests/config/test_settings_precedence.py`):
  - env > DB > YAML > default — verify with all four layers set to different values
  - `Settings.sources` correctly labels each field's origin

- [ ] **T3.** Unit tests for inventory merge (`tests/agent/test_inventory_merge.py`):
  - YAML targets pass through unchanged when no overrides
  - Disabled YAML target excluded
  - DB-added target included
  - DB-added target disabled is excluded
  - Combined: 5 YAML (1 disabled) + 2 DB-added (1 disabled) = 5 effective targets

- [ ] **T4.** Playwright tests for `/ui/settings` (`tests/ui/test_settings_playwright.py`):
  - Page loads, shows LLM section
  - Edit `model` field, save, reload → value persisted
  - API key field: enter new value, save → stored encrypted, displayed masked on reload
  - Reset button clears override, reverts to YAML/default
  - Test connection button triggers a check
  - Env-var-locked field is disabled in the form

- [ ] **T5.** Playwright tests for `/ui/inventory` (`tests/ui/test_inventory_playwright.py`):
  - Page loads, shows VMs grouped by environment
  - Toggle disable on a YAML VM → persists, row shows disabled state
  - Add a new ad-hoc VM → appears in the list with "+ ad-hoc" badge
  - Edit an ad-hoc VM → fields update
  - Delete an ad-hoc VM → removed from list
  - Validation: submit empty name → 400 error shown
  - Cannot edit YAML-sourced VM fields (only toggle)

- [ ] **T6.** Playwright tests for UI auth (`tests/ui/test_ui_auth_playwright.py`):
  - With `ERRANDER_UI_USER`/`_PASSWORD` set: 401 without credentials; 200 with correct
  - `/health` and `/metrics` remain open
  - Test fixture supports both auth-enabled and auth-disabled server modes

### Documentation

- [ ] **DOC-B1.** New `docs/learning/22-ui-settings-and-inventory.md`:
  - Why this scope (operator day-to-day vs engineering config)
  - Precedence rules (env > DB > YAML > default)
  - Auth design (Basic Auth, why, how to enable)
  - Inventory merge logic walkthrough
  - Encryption flow for secret settings (UI → SecretsManager → DB → load → decrypt)
  - Hot-reload vs restart-required fields
  - Gotchas (timing attacks on auth, disabled YAML fields, audit trail for changes)

- [ ] **DOC-B2.** Update `docs/SETUP.md`:
  - Add a new Step after the initial config: **"Step 5b — Secure the UI"** covering `ERRANDER_UI_USER/PASSWORD` and `ERRANDER_SECRETS_KEY`
  - Add `/ui/settings` and `/ui/inventory` to the Monitoring table

- [ ] **DOC-B3.** Update `STATUS.md` and `tasks/todo.md` after completion

### Acceptance criteria

- All existing tests pass
- New tests T1–T6 pass
- Full flow works end-to-end:
  1. Start the agent with auth enabled and a master key set
  2. Log into `/ui/settings`, change `llm.model` and `llm.temperature`, save
  3. Run a batch (`--run-now`) — new settings take effect without restart
  4. Go to `/ui/inventory`, disable a VM, run another batch → disabled VM is skipped
  5. Add an ad-hoc VM via UI, run another batch → new VM is included
  6. Query the audit trail → all three UI changes are logged
- `ERRANDER_SECRETS_KEY` set → UI-entered API keys stored as `enc:v1:...` in DB; `.env` with `ERRANDER_LLM_API_KEY=enc:v1:...` decrypts correctly

---

## Implementation order

Strict linear order — each phase depends on the previous:

1. **Phase A** (1 hour) — unblock LLM flexibility, smallest diff
2. **Phase A.5** (4 hours) — secrets foundation, needed before UI can store API keys
3. **Phase B** (1.5 days) — UI settings + inventory, builds on A and A.5

Do not start B before A.5 lands. Do not start A.5 before A lands. Each phase should ship with passing tests and lint.

---

## Out of scope (explicitly deferred)

- Encrypting the full SQLite DB at rest (ops concern — use LUKS / dm-crypt / encrypted EBS)
- Key rotation tooling (the `v1:` prefix supports it; tooling deferred)
- HSM / cloud KMS integration (aligns with existing v2 Vault upgrade path)
- Encrypting non-secret inventory fields (hostnames, ssh_user) — explicit decision: operational data stays plaintext for debuggability
- A full RBAC system for the UI (Basic Auth is sufficient for v1; roles deferred)
- Editing SSH timeouts, wave thresholds, scheduler cron, drift flags via UI — stays YAML-only per the design discussion
- UI-editable disk cleanup whitelist, kernel exclusion, risk tiers — these are safety boundaries; hardcoding is the control

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Users lose `ERRANDER_SECRETS_KEY` → all encrypted values unrecoverable | `docs/SECRETS.md` warns explicitly; recommend backing up the key alongside other infrastructure secrets |
| UI auth bypass due to middleware bug | Unit tests in AUTH3 cover the main cases; belt-and-braces: the settings POST handler also checks auth explicitly |
| Config drift between YAML and DB | UI shows source indicator per field; document recommended practice: commit baseline to YAML, use UI only for runtime tuning |
| Secret leaks via log messages | Redaction filter (S6) catches known patterns; test coverage in S8 |
| Hot reload race during a live batch | Settings are captured at `run_env_batch()` start into a local `Settings` instance; mid-batch DB changes don't affect the running batch |
| Ad-hoc inventory VMs can target machines outside the operator's authority | Same auth boundary as the UI itself — if you can log in, you can manage inventory. Future: per-environment permissions (v2) |

---

## How to use this plan

Hand the whole file to a fresh Sonnet session. Ask it to:

1. Start with Phase A, mark each task `[x]` as completed
2. Run tests and lint after every sub-phase
3. Commit at phase boundaries (after A, after A.5, after B)
4. Update `STATUS.md` and `tasks/todo.md` at the end

Each task includes file paths and enough spec detail that the implementer can proceed without re-deriving decisions. Where a task says "in the style of existing X", the implementer should `Read` X first to match conventions.
