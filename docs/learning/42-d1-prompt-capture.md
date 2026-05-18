# 42 — D1 Full Prompt + Context Capture

## What was built and why

Every `prioritize_actions()` LLM call now records three additional fields in `ai_decisions`:

- `prompt_full TEXT` — the complete rendered prompt sent to the LLM
- `context_snapshot TEXT` — JSON of `vm_info` (as a dict) and `available_actions` (list of strings)
- `model_params TEXT` — JSON of model parameters, currently `{"temperature": <float or null>}`

This is Phase D1 from the post-review plan. The goal: once these fields are populated in production, Phase D2 can replay past LLM calls against new models or prompts, and Phase D3 can write assertions about AI decision quality.

The `no_llm` path (hardcoded fallback, no LLM client) records `context_snapshot` only — no prompt was rendered, so `prompt_full=None` and `model_params=None`.

## Key concepts

### Idempotent schema migration without the shared migrations system

`ai_decisions` is created by `AIDecisionStore._CREATE_TABLE_SQL` — it was never part of the `run_migrations()` / `_MIGRATIONS` list that `AuditStore` uses. Adding a migration 4 (`ALTER TABLE ai_decisions ADD COLUMN ...`) to `_MIGRATIONS` would cause `AuditStore.initialize()` (which calls `run_migrations()`) to try altering `ai_decisions` before that table exists — crash on first startup.

Instead, `AIDecisionStore.initialize()` runs the `ALTER TABLE` statements itself after table creation, suppressing `aiosqlite.OperationalError` if the column already exists:

```python
_D1_COLUMNS = (
    "prompt_full TEXT",
    "context_snapshot TEXT",
    "model_params TEXT",
)

async def initialize(self) -> None:
    self._db = await aiosqlite.connect(self._db_path)
    await self._db.execute(_CREATE_TABLE_SQL)   # includes new cols — fresh installs
    for idx in _CREATE_INDEX_SQL:
        await self._db.execute(idx)
    await self._db.commit()
    await run_migrations(self._db)             # migrations 0–3 (other tables)
    for col_def in self._D1_COLUMNS:
        with contextlib.suppress(aiosqlite.OperationalError):
            await self._db.execute(
                f"ALTER TABLE ai_decisions ADD COLUMN {col_def}"
            )
    await self._db.commit()
```

**Three scenarios, all handled correctly:**

| Scenario | `_CREATE_TABLE_SQL` | ALTER TABLE |
|---|---|---|
| Fresh install | creates table with all 17 cols | OperationalError, suppressed |
| Existing DB (pre-D1) | no-op (IF NOT EXISTS) | adds the 3 columns |
| Existing DB (post-D1) | no-op | OperationalError, suppressed |

### `_as_float()` — normalizing `getattr` results before JSON

```python
def _as_float(val: object) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    return None
```

`getattr(mock_obj, "_temperature", None)` in tests returns a `MagicMock`, not `None`. The default `None` only fires when the attribute is genuinely absent. Passing a `MagicMock` to `json.dumps` raises `TypeError`. Normalizing via `_as_float` converts real floats and returns `None` for anything else — JSON-safe in all cases.

### Context snapshot with `dataclasses.asdict`

`VMInfo` is a `@dataclass` (not a Pydantic model), so `.model_dump()` doesn't exist. Use `dataclasses.asdict()`:

```python
from dataclasses import asdict
# ...
context_snapshot=json.dumps({
    "vm_info": asdict(vm_info),
    "available_actions": [str(a) for a in (available_actions or [])],
})
```

`VMInfo.os_family` is `OSFamily` (a `StrEnum` — a `str` subclass), so `json.dumps` serializes it as a plain string without any custom encoder.

## Gotchas

1. **ALTER TABLE vs migrations system**: Never add a migration that ALTERs a table owned by a different store. Each store manages its own schema. If two stores share a DB, only the owning store's ALTER TABLE calls are safe.

2. **`getattr` mock trap**: `getattr(mock, "attr", default)` only returns `default` when `mock` has no such attribute. Mocks auto-create attributes on access, so the default is never used — you get a MagicMock instead. Always normalize with a type guard before serializing.

3. **`no_llm` path needs no `prompt_full`**: When there's no LLM client, no prompt is rendered. Don't pass `prompt_full` (or pass `None` explicitly) — storing an empty string would be misleading; NULL is the correct value for "not applicable."

## Quiz

1. Why can't migration 4 (`ALTER TABLE ai_decisions`) be added to `_MIGRATIONS` in `migrations.py`?
2. What error does `json.dumps({"temperature": MagicMock()})` raise, and why?
3. Why is `dataclasses.asdict(vm_info)` preferred over `str(vm_info)` for `context_snapshot`?
4. What does `contextlib.suppress(aiosqlite.OperationalError)` do in `initialize()`, and why is it safe?
