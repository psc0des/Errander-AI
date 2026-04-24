# 23 — Playwright UI Tests (Settings, Inventory, Auth)

**Phase**: 4 (T4-T6)
**Files**: `tests/ui/test_settings_playwright.py`, `tests/ui/test_inventory_playwright.py`, `tests/ui/test_ui_auth_playwright.py`

---

## What Was Built

End-to-end browser tests for the three web UI features added in Phase 4:

1. **Settings page** — save/persist LLM settings, reset to default, env-var lock display
2. **Inventory page** — display VMs, toggle enable/disable, add ad-hoc VM, delete ad-hoc VM
3. **UI auth** — HTTP Basic Auth on `/ui/*`, `/metrics` and `/health` always open

All tests use a real aiohttp server (started in a background thread) backed by an in-memory SQLite database. Playwright drives a headless Chromium browser.

---

## Key Architecture: Module-Scoped Server

Each test file starts ONE server for the entire module (not one per test). This is critical for performance — starting/stopping aiohttp + SQLite per test would be ~2s overhead per test.

```python
@pytest.fixture(scope="module")
def settings_base_url() -> str:  # type: ignore[return]
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        audit = AuditStore(":memory:")
        await audit.initialize()
        overrides = OverridesStore(":memory:")
        await overrides.initialize()
        runner = await start_metrics_server(port=0, audit_store=audit, overrides_store=overrides)
        site = list(runner.sites)[0]
        port = site._server.sockets[0].getsockname()[1]
        stop = asyncio.Event()
        ctx.update(runner=runner, stop=stop, port=port)
        ready.set()
        await stop.wait()
        await runner.cleanup()

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_until_complete(_run())), daemon=True)
    t.start()
    ready.wait(timeout=10)
    yield f"http://localhost:{ctx['port']}"
    loop.call_soon_threadsafe(ctx["stop"].set)
    t.join(timeout=5)
```

**Key points:**
- `port=0` → OS assigns a free port (no collision between test files running in the same process)
- `daemon=True` → thread dies with the process if teardown fails
- `ready.wait(timeout=10)` → blocks until the server is listening before yielding the URL

---

## Bug: Nested Forms Break HTML5

**The hardest bug in this phase.** The settings page rendered a reset `<form>` *inside* the main settings `<form>`:

```html
<!-- main settings form -->
<form method="POST" action="/ui/settings">
  <div class="form-row">
    <input name="ERRANDER_LLM_MODEL" value="my-model">
    <!-- reset form NESTED inside main form — INVALID HTML5 -->
    <form method="POST" action="/ui/settings/reset" style="display:inline">
      <input type="hidden" name="key" value="ERRANDER_LLM_MODEL">
      <button class="btn-del">Reset</button>
    </form>
  </div>
  <button class="btn-save">Save Changes</button>  <!-- orphaned! -->
</form>
```

HTML5 forbids nested forms. Chromium handles this by **implicitly closing the outer form** when it encounters the inner `<form>` tag. The Save Changes button then ends up outside any form — clicking it does nothing.

**Why it was hard to spot:**
- Tests with an **empty DB** (no reset form rendered) → Save worked fine
- Tests **after a prior save** (reset form rendered) → Save silently did nothing
- `page.expect_navigation()` timed out at 30s with no error message

**Fix:** Use the HTML5 `form="<id>"` attribute to associate the reset button with a form rendered *outside* the main form:

```python
# metrics.py — inside the row builder loop
form_id = f"reset-{env_key}"
reset_btn = (
    f'<button type="submit" form="{form_id}"'
    f' class="btn-del" style="margin-left:.5rem">Reset</button>'
)
reset_forms_html += (
    f'<form id="{form_id}" method="POST" action="/ui/settings/reset">'
    f'<input type="hidden" name="key" value="{env_key}">'
    f'</form>'
)

# Then after the main form closes:
body = (
    ...
    + '<form method="POST" action="/ui/settings">'
    + rows_html          # reset buttons here (with form="..." attr)
    + save_button_html
    + '</form>'
    + reset_forms_html   # out-of-band forms here, outside main form
    ...
)
```

**Test locator change:** The reset button is no longer *inside* its `<form>`, so `reset_form.locator("button")` no longer works. Use:
```python
page.locator('button[form="reset-ERRANDER_LLM_MODEL"]')
```

---

## Lesson: `locator.click()` Does NOT Wait for Navigation

Playwright's `locator.click()` dispatches the click event and returns immediately. It does **not** wait for any resulting navigation. If you call `page.goto()` right after, the browser may abort the in-flight POST request before the server writes to the DB.

```python
# WRONG — goto() can abort the POST
page.locator("button.btn-save").click()
page.goto("/ui/settings")  # POST might be aborted; DB not updated

# CORRECT — wait for the redirect to appear
page.locator("button.btn-save").click()
expect(page.get_by_text("Settings saved")).to_be_visible()  # waits for redirect
page.goto("/ui/settings")  # safe to navigate now
```

The `expect(...).to_be_visible()` call acts as the navigation wait because it times out waiting for the element — which only appears after the 302 redirect to `/ui/settings?flash=Settings+saved` completes.

---

## Auth Tests: Raw HTTP vs Browser Navigation

The auth tests use `page.request.get()` instead of `page.goto()` for status code checks. This avoids the browser's built-in "Enter credentials" dialog that Playwright/Chromium shows on 401 responses:

```python
# WRONG — triggers browser credential dialog on 401
page.goto(f"{auth_base_url}/ui/settings")

# CORRECT — raw HTTP, no dialog
response = page.request.get(f"{auth_base_url}/ui/settings")
assert response.status == 401
```

**Header casing**: aiohttp lowercases all response header names. Check `www-authenticate` not `WWW-Authenticate`:
```python
headers_lower = {k.lower(): v for k, v in response.headers.items()}
assert "www-authenticate" in headers_lower
```

**Trailing slash**: Route is registered as `/ui/settings` (no slash). Requesting `/ui/` triggers a 301 redirect to `/ui`, and Playwright drops the Authorization header on the redirect (standard security behaviour). Always use the exact path without trailing slash.

---

## Strict Mode Violations

Playwright's `expect(page.get_by_text("overridden in UI"))` fails if multiple elements match — it's in "strict mode" by default. When two fields both have DB overrides (temperature from one test, model from another), there are two "overridden in UI" labels. Fix with `.first`:

```python
expect(page.get_by_text("overridden in UI").first).to_be_visible()
```

---

## `ignore_query_string` is Not a Valid kwarg

`expect(page).to_have_url(url, ignore_query_string=True)` fails in pytest-playwright 0.7.2 — `ignore_query_string` is a JavaScript-only parameter. The redirect from a toggle goes to `/ui/inventory?flash=stg-web+disabled`. Just check the page title instead:

```python
stg_row.get_by_role("button", name="Disable").click()
expect(page).to_have_title("Errander-AI — Inventory")
```

---

## Quiz

1. Why does `port=0` work for the test server? What assigns the actual port?
2. If two test files both use `port=0`, can they collide?
3. Why must the reset `<form>` be outside the main `<form>`?
4. What HTML5 attribute links a button to a form it's not nested inside?
5. What's the difference between `page.goto()` and `page.request.get()` for auth testing?
6. Why does `page.locator("button.btn-save").click()` sometimes not trigger a save?
