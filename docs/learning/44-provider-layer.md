# 44 — Operations Hub Provider Layer

## What was built and why

The Operations Hub UI (`errander/web/server.py`) was rendering all pages from static fixture data in `errander/web/data.py`. In demo/CI mode that is fine, but in a live deployment the pages would never reflect real infrastructure state. A QA/SRE review flagged this as a critical gap: the UI must either show real data or make it unmistakeably clear that it is showing a demo.

The fix: a **provider layer** (`errander/web/providers.py`) that sits between page functions and data sources. All page functions now call `get_provider().get_vms()` etc. — they never import data constants directly.

## Key concepts

### DataProvider Protocol

```python
@runtime_checkable
class DataProvider(Protocol):
    def data_mode(self) -> str: ...
    def get_vms(self) -> list[dict[str, Any]]: ...
    def get_approvals(self) -> list[dict[str, Any]]: ...
    # ... 14 methods total
```

`@runtime_checkable` means `isinstance(p, DataProvider)` works at runtime — useful for tests that verify the singleton type. Protocol means no inheritance required; any class that implements the right methods satisfies it.

### FixtureProvider

Delegates every method to a local import from `data.py`:

```python
def get_vms(self) -> list[dict[str, Any]]:
    from errander.web.data import VMS
    return VMS
```

Local imports (inside the method body) avoid a module-level circular import between `providers.py` and `data.py` when server.py imports both.

### LiveProvider — cache-then-read pattern

Page functions are sync (they build HTML strings). Real store reads are async. The reconciliation:

- All state is cached in `self._vms`, `self._approvals`, etc.
- `async def refresh(...)` populates the cache from real stores.
- Page functions read from the cache synchronously — zero awaits in the hot path.
- `_on_startup` in server.py calls `await provider.refresh(...)` once, then schedules periodic re-fetches.

### Sentinel dicts for unavailable state

When a store is missing, LiveProvider returns sentinel values — not fixture data:

```python
_UNAVAIL_SCHEDULER: dict[str, Any] = {
    "cron": "—", "next_runs": [], ...
}
```

The rule: **LiveProvider must never silently serve fixture data**. If the approval manager is not wired, `get_approvals()` returns `[]`, not the fixture approval cards.

### Singleton pattern

```python
_singleton: DataProvider | None = None

def get_provider() -> DataProvider:
    global _singleton
    if _singleton is None:
        _singleton = _make_provider()
    return _singleton
```

One provider per process. Tests reset it with `providers._singleton = None` (or via the `reset_singleton` autouse fixture).

## Empty-state guards — the hard part

Fixture data is always non-empty. Live mode with no stores returns empty lists and `{}` dicts. Page functions that were written assuming non-empty data crashed with `KeyError`, `IndexError`, or `ValueError` in live mode. Three patterns fixed:

**1. List `[0]` access with empty default:**
```python
# Before (crashes when next_runs=[])
nextrun = sch.get("next_runs", ["—"])[0]

# After
nextrun = (sch.get("next_runs") or ["—"])[0]
```
`or` replaces the empty list before the index — the default `["—"]` only triggers when the list is falsy.

**2. `max()` over an empty generator:**
```python
# Before (ValueError: max() arg is an empty sequence)
max_log = math.log10(max(n["duration_s"] for n in nodes) + 1)

# After
max_log = math.log10(max((n["duration_s"] for n in nodes), default=0) + 1)
```
`default=` on `max()` avoids the `ValueError` and produces `log10(1) = 0` — a safe scale factor.

**3. Guard a whole rendering block:**
```python
if not probe:
    probe_card = """<div class="card">...No probe history available...</div>"""
else:
    if probe["escalated"]:   # safe — probe is non-empty
        ...
```
When multiple keys from the same dict are used throughout a block, one top-level guard is cleaner than scattering `.get()` calls everywhere.

## Testing without event loops (Windows / anyio constraint)

`anyio-4.13.0` + `asyncio_mode=AUTO` makes any event loop creation take ~250 seconds per test on Windows. That includes `asyncio.new_event_loop()` in a sync test — anyio intercepts the loop factory globally.

Solution: test async contracts and cache behaviour without running any coroutine.

**Contract test:**
```python
def test_live_provider_refresh_is_coroutine(live_provider) -> None:
    assert inspect.iscoroutinefunction(live_provider.refresh)
```

**Cache injection (tests getter, not refresh integration):**
```python
def test_live_provider_refresh_with_approval_manager(live_provider) -> None:
    live_provider._approvals = [{"id": "batch-xyz", ...}]
    assert live_provider.get_approvals()[0]["id"] == "batch-xyz"
```

**AST contract test (no imports needed at all):**
```python
def test_server_does_not_import_data_constants() -> None:
    source = _SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in ("errander.web.data", ".data"):
                imported = {alias.name for alias in node.names}
                assert not (imported & _DATA_NAMES)
```

This walks the AST rather than importing server.py, catching violations at the source level.

## Evidence gating — the second pass

After wiring the provider layer, a second SRE QA review found that fixture-only operational facts still leaked into live mode via two paths:

**Path 1 — Fixture overlays (VM_EVIDENCE, BATCH_EVIDENCE, etc.)**

Evidence modules enrich fixture data with realistic operational details (lock holders, SSH fingerprints, batch hashes). They must never reach live mode. Fix: gate helpers at the call site:

```python
_NULL_AUDIT_EV: dict[str, Any] = {"event_id": "—", "approver": "(n/a)", ...}

def _ev_vm(hostname: str) -> dict[str, Any]:
    return VM_EVIDENCE.get(hostname, {}) if get_provider().data_mode() == "FIXTURE" else {}

def _ev_audit(idx: int) -> dict[str, Any]:
    return audit_evidence_for(idx) if get_provider().data_mode() == "FIXTURE" else _NULL_AUDIT_EV
```

**Path 2 — Hardcoded inline strings**

Even after evidence gating, f-strings still contained hardcoded fixture dates/counts:

```python
# Before — leaks in live mode
f'last batch 02:00 UTC'
f'Completed 2026-04-23 02:14 UTC'
f'data as of 2026-04-23 02:14 UTC'
# KPIs: 34, 8, 3 (hardcoded in page_vm)
# "RESOLVED TODAY — 14 actions" (always rendered in page_approvals)
```

Fix: compute `_is_fixture = get_provider().data_mode() == "FIXTURE"` at the top of each page function and use it as a guard:

```python
def page_fleet() -> str:
    _is_fixture = get_provider().data_mode() == "FIXTURE"
    ...
    # subtitle only shows demo date in fixture mode
    f'last batch 02:00 UTC' if _is_fixture else ''
    # KPI completion timestamp
    'Completed 2026-04-23 02:14 UTC' if _is_fixture else b.get('completed_at', '—')
```

**Path 3 — Default values in `.get()` calls**

```python
# Before — hardcoded default leaks when ev is {} in live mode
ev_window = ev.get("window", "Tue/Thu 02:00–04:00 UTC")
ev_ssh_fp = ev.get("ssh_key_fp", f"/keys/{hostname}.pem")

# After — safe defaults
ev_window = ev.get("window", "—")
ev_ssh_fp = ev.get("ssh_key_fp", "—")
```

**Path 4 — Documentation/example text**

Reference tables that show example values can also leak fixture model names:

```python
# Before
("ERRANDER_LLM_MODEL", "Model identifier (e.g. Qwen3-8B-AWQ)", "set"),

# After
("ERRANDER_LLM_MODEL", "Model identifier from your LLM provider", "set"),
```

## Live-mode regression test pattern

The right way to catch all four paths is a parametrized regression test:

```python
_FIXTURE_ONLY_STRINGS = [
    "2026-04",            # April 2026 fixture dates
    "prod-0423",          # fixture batch IDs
    "prod-web-01",        # fixture VM hostnames
    "Qwen3-8B-AWQ",       # fixture LLM model
    "10.0.0.100",         # fixture LLM endpoint IP
    "14 actions approved",# fixture resolved-today count
    "28 batches",         # fixture batch KPI
    "96.4%",              # fixture completion rate
]

@pytest.mark.parametrize("page_fn", [
    "page_fleet", "page_approvals", "page_batches", "page_audit",
    "page_agent", "page_inventory", "page_settings", "page_admin",
])
def test_live_mode_no_fixture_facts(live_provider, server_module, page_fn) -> None:
    _install(live_provider)
    html = getattr(server_module, page_fn)()
    for marker in _FIXTURE_ONLY_STRINGS:
        assert marker not in html, f"{page_fn}() leaks {marker!r} in live mode"
```

Run this after every evidence-gating session. Any failure reveals a new leak path. Extend `_FIXTURE_ONLY_STRINGS` whenever a QA round finds a new fixture value.

## Gotchas

- Python AST represents `from .providers import get_provider` as `ImportFrom(module='providers', level=1)` — not `module='.providers'`. The test must check `node.level > 0 and mod == "providers"` or `"providers" in mod`.
- `@runtime_checkable` Protocols do NOT check method signatures at runtime — only that the attribute exists. The test suite is the enforcement mechanism for signature correctness.
- LiveProvider stores `list` copies in getters (`return list(self._vms)`) so callers cannot mutate the cache. Without this, a test that modifies the returned list would corrupt the provider state for the next caller.
- The "not found" page for an unknown VM legitimately echoes the hostname back — exclude the queried hostname from `_FIXTURE_ONLY_STRINGS` checks in VM page tests.
- Metrics API: guard `if known and hostname not in known` (not just `if hostname not in known`) so an empty-inventory live mode still serves data gracefully rather than returning 404 for every hostname.

## Quiz

1. Why does FixtureProvider use local imports inside each method rather than module-level imports?
2. What does `(sch.get("next_runs") or ["—"])[0]` do differently from `sch.get("next_runs", ["—"])[0]`?
3. Why is `max((n["d"] for n in nodes), default=0)` different from `max([n["d"] for n in nodes], default=0)` in terms of behaviour? (Hint: both work, but one creates the full list first.)
4. If `anyio` intercepts event loop creation globally, how could you run refresh integration tests without the 250 s penalty?
