# AI SRE UI Wiring Audit

Date: 2026-05-14
Role: Senior SRE auditor

## Current Revalidation - 2026-05-14, Second Pass

Current verdict: substantially fixed and acceptable for pre-production, pending CI browser proof.

I rechecked the UI after the latest dev fix claim. The previous blockers are now closed in the current codebase. The UI is no longer just an override shell with broken POST wiring; settings, inventory, CSRF, and SRE visibility wiring are now connected well enough to accept for pre-production validation.

### Closed Findings

1. DB-backed settings are now loaded on restart before components are built.

   Evidence: `errander/main.py:829-866` does a first settings load, initializes `OverridesStore`, fetches `get_settings_overrides()`, and reloads settings with `db_overrides`. Components are built only after this at `errander/main.py:868-869`. The initialized overrides store is reused at `errander/main.py:907-908` and closed at `errander/main.py:1067-1073`.

2. Inventory UI now shows the base YAML fleet plus DB overrides.

   Evidence: `start_metrics_server()` accepts `base_inventory` at `errander/observability/metrics.py:1503-1509`, stores it at `errander/observability/metrics.py:1570`, and `_ui_inventory_get()` merges that base inventory with override rows at `errander/observability/metrics.py:953-1008`. `main.py` passes the loaded flat inventory into the UI server at `errander/main.py:853-855` and `errander/main.py:929-934`.

3. CSRF wiring is now real, not decorative.

   Evidence: `_csrf_middleware` has `@web.middleware` at `errander/observability/metrics.py:628-638`. `_page()` injects CSRF hidden fields and sets the cookie when given a request at `errander/observability/metrics.py:498-585`. Settings and inventory pages call `_page(..., request=request)` at `errander/observability/metrics.py:854` and `errander/observability/metrics.py:1087`.

4. Previous XSS findings are materially fixed.

   Evidence: `_esc = html.escape` is defined at `errander/observability/metrics.py:18-23`. Page titles are escaped at `errander/observability/metrics.py:532` and `errander/observability/metrics.py:572`. Settings flash/input values are escaped at `errander/observability/metrics.py:780-831`. Inventory values are escaped at `errander/observability/metrics.py:1016-1058`. Batch and VM detail values are escaped at `errander/observability/metrics.py:1240-1353`.

5. LLM test endpoint and OS-family mismatch remain fixed.

   Evidence: the LLM test endpoint is POST-only at `errander/observability/metrics.py:1528`, and the inventory UI allow-list matches the core enum with `{"ubuntu", "debian", "rhel"}` at `errander/observability/metrics.py:950`.

### Validation Performed

Focused test command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\observability\test_ui_security.py tests\ui\test_web_ui.py tests\ui\test_approval_ui.py tests\agent\test_sre_wiring.py -q -rs -p no:cacheprovider --basetemp .pytest-tmp-ui-recheck
```

Result:

- 18 passed.
- 44 skipped because Chromium is not installed locally.
- No assertion failures.

Direct aiohttp smoke test result:

- `GET /ui/inventory` returned 200.
- YAML-backed VMs were visible with the `YAML` badge.
- CSRF hidden field and cookie were present.
- `POST /ui/inventory/toggle` returned 302 and persisted the disabled override.
- `GET /ui/settings` returned 200 with CSRF.
- `POST /ui/settings` returned 302 and persisted `ERRANDER_LLM_MODEL` in the overrides store.

### Remaining Non-Blocking Items

1. Browser-level Playwright proof is still missing on this machine because Chromium is not installed. CI should run the skipped UI suites and produce a transcript before production approval.

2. The UI uses HTML escaping inside path segments, for example batch and VM links. That blocks common HTML injection, but a stricter implementation should URL-quote path segments as defense in depth.

3. One browser test still appears stale: `tests/ui/test_inventory_playwright.py:121` expects the old empty-state text, `No inventory overrides yet`. Since the UI now shows YAML inventory, the browser test should be updated to match the new behavior before CI is trusted.

### Current Acceptance Decision

I accept the UI fixes as substantially implemented. I would not block pre-production/staging on the previous UI findings anymore.

Production approval still requires a real CI/browser run and at least one staging operator walkthrough covering approval, settings save/reset, inventory add/toggle/delete, and a read-only dashboard/batch/VM review.

## Historical Revalidation - 2026-05-14, Superseded

The dev team fixed the main UI release blockers, but not every UI finding is fully closed.

### Revalidated As Fixed

- CSRF middleware registration is fixed. `_csrf_middleware` now has `@web.middleware` at `errander/observability/metrics.py:626`.
- `_page()` now accepts `request`, injects CSRF hidden fields, and sets the CSRF cookie at `errander/observability/metrics.py:496-584`.
- Settings and approvals POSTs worked in a real aiohttp smoke test:
  - `GET /ui/settings` returned 200 with `_csrf_token` and cookie.
  - `POST /ui/settings` returned 302 to `/ui/settings?flash=Settings+saved`.
  - `GET /ui/approvals` returned 200 with `_csrf_token`.
  - `POST /ui/approvals/batch-audit-01/approve` returned 302 and removed the pending approval.
- LLM test endpoint is now POST, not GET, at `errander/observability/metrics.py:918-929`.
- UI OS family allow-list now matches core OS families: `{"ubuntu", "debian", "rhel"}` at `errander/observability/metrics.py:948`.
- Many previously unescaped values are now escaped with `_esc`, including settings flash/input values, inventory values, and batch/VM detail rows.

### Still Open

1. Settings UI still does not appear to affect the running agent, nor restart-loaded settings.

   Evidence: `main.py` still calls `load_settings()` at `errander/main.py:831-833` before `OverridesStore` is initialized at `errander/main.py:895-896`, and does not pass `db_overrides`. The UI note says settings take effect after restart, but restart still will not load DB overrides unless this startup path changes.

2. XSS hardening is improved but incomplete.

   Evidence: `_page()` still injects raw `title` into `<title>` and `.tb-title` at `errander/observability/metrics.py:530` and `570`. Dashboard/batches still render raw batch IDs and VM IDs in links at `errander/observability/metrics.py:1208-1213` and `1241-1246`.

3. Inventory page is still primarily an override manager.

   Evidence: it still uses `store.get_all_inventory_overrides()` at `errander/observability/metrics.py:969`. It does not show the full YAML fleet unless there are override rows.

### Revalidation Test Result

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\observability\test_ui_security.py tests\ui\test_web_ui.py tests\agent\test_sre_wiring.py -q -p no:cacheprovider
```

Result:

- 18 passed.
- 25 skipped, mostly browser/Playwright-dependent UI tests.

### Updated UI Verdict

The UI is much better and the original hard blocker is fixed. I would still not call it fully production-ready until DB-backed settings are actually consumed on restart and the remaining raw title/list rendering is escaped.

## Original Verdict - Superseded

I would not approve the UI as production-ready.

There are useful UI routes, but the current implementation has a critical middleware bug and multiple state-changing forms are not proven by tests. The UI is not merely missing polish; parts of it can fail before an operator can approve, change settings, or manage inventory.

## Critical Findings

### 1. UI middleware is incorrectly registered and can return 500 for UI requests

Evidence:

- `_basic_auth_middleware` is decorated with `@web.middleware` at `errander/observability/metrics.py:576`.
- `_csrf_middleware` starts at `errander/observability/metrics.py:611`, but is not decorated with `@web.middleware`.
- The app registers both at `errander/observability/metrics.py:1511`.

Observed during audit:

- Starting `start_metrics_server()` and calling `/ui/settings` returned HTTP 500.
- Trace: `AttributeError: 'Application' object has no attribute 'method'` inside `_csrf_middleware`.

Impact:

The production metrics/UI aiohttp server can fail before rendering the UI. This is a release blocker.

Required fix:

Decorate `_csrf_middleware` with `@web.middleware` and add an integration test that starts the real server and asserts `/ui`, `/ui/settings`, `/ui/inventory`, and `/ui/approvals` return 200.

### 2. CSRF helpers are implemented but not wired into page rendering

Evidence:

- `_csrf_middleware` rejects POST `/ui/*` without a valid token at `errander/observability/metrics.py:611-620`.
- `_inject_csrf` exists at `errander/observability/metrics.py:700`.
- `_set_csrf_cookie` exists at `errander/observability/metrics.py:689`.
- `_page` returns HTML directly at `errander/observability/metrics.py:493-569`.
- Search shows `_inject_csrf` is only defined, not used by real page handlers.

Impact:

After the middleware decorator is fixed, normal browser POSTs for approvals, settings, and inventory will likely be blocked unless forms receive hidden CSRF tokens and the response sets the CSRF cookie.

Required fix:

Make `_page` request-aware or inject/set CSRF in each UI GET handler. Then add browser or aiohttp tests that GET a page, extract the token/cookie, and POST successfully.

## High Findings

### 3. Stored XSS risk in audit, settings, and inventory pages

Evidence:

- Batch detail prints `e.vm_id`, `e.action_type`, and `e.detail` directly at `errander/observability/metrics.py:1258-1260`.
- VM detail prints `e.batch_id`, `e.action_type`, and `e.detail` directly at `errander/observability/metrics.py:1299-1301`.
- Inventory values are interpolated directly at `errander/observability/metrics.py:987-994`.
- Settings flash/error and values are interpolated directly at `errander/observability/metrics.py:761-767` and `816`.

Impact:

An unsafe hostname, VM id, audit detail, command output, or query parameter can inject HTML/script into an operator page. In an SRE approval UI, that is not cosmetic; it can become approval/settings tampering.

Required fix:

Escape all untrusted fields with a central helper. Only allow explicitly marked safe HTML for known components.

### 4. Settings UI saves LLM overrides but does not affect the running agent

Evidence:

- Runtime settings are loaded before overrides store initialization at `errander/main.py:809-811`.
- Components, including LLM client, are built at `errander/main.py:833-834`.
- Overrides store is initialized later at `errander/main.py:872-874`.
- Settings UI writes override rows at `errander/observability/metrics.py:869-874`.

Impact:

The UI can show "Settings saved", but the running LLM client was already built from old settings. Operators can believe they changed the AI provider/model when they did not.

Required fix:

Either reload/rebuild the LLM client after settings change or make the UI clearly say "takes effect after restart" and prove startup loads DB overrides.

## Medium Findings

### 5. LLM test endpoint leaks API keys through GET query parameters

Evidence:

- `/ui/settings/test-llm` is a GET endpoint at `errander/observability/metrics.py:903`.
- It reads `api_key` from query parameters at `errander/observability/metrics.py:910`.

Impact:

API keys can land in browser history, access logs, proxy logs, and referrers.

Required fix:

Use POST with CSRF and never place secrets in URLs.

### 6. Inventory UI is an override manager, not a real inventory UI

Evidence:

- Inventory page loads only `store.get_all_inventory_overrides()` at `errander/observability/metrics.py:950`.
- Empty state says VMs appear after ad-hoc add or YAML toggle at `errander/observability/metrics.py:1028-1031`.
- Real batch target merging consumes overrides at `errander/main.py:624-662`.

Impact:

Operators cannot see the actual YAML fleet unless a VM has an override. This is confusing and risky for live operations.

Required fix:

Show base YAML inventory plus override status, or rename the page to "Inventory Overrides".

### 7. Inventory UI accepts unsupported OS families

Evidence:

- UI accepts `centos` and `amazon` at `errander/observability/metrics.py:929`.
- Core `OSFamily` only supports `ubuntu`, `debian`, and `rhel` at `errander/models/vm.py:13-19`.
- Inventory schema validates only those three at `errander/config/schema.py:70-77`.

Impact:

Ad-hoc inventory can create VM targets that downstream code may not support consistently.

Required fix:

Use the same enum/schema validation in the UI as the core inventory loader.

## Test Coverage Concern

Command run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ui\test_approval_ui.py tests\observability\test_ui_security.py -q -rs -p no:cacheprovider
```

Observed:

- 8 passed.
- 19 skipped because Chromium is not installed.

Interpretation:

The tests that would prove real UI behavior are skipped locally. The passing tests cover helper/security logic, not the full browser/server wiring.

## Acceptance Decision

Do not accept the UI as wired properly.

Minimum approval gate:

1. Fix aiohttp CSRF middleware registration.
2. Prove GET UI routes return 200 from the real server.
3. Prove approve/reject, settings save/reset, inventory add/toggle/delete work through real HTTP POSTs with CSRF.
4. Escape all untrusted UI output.
5. Decide whether settings changes are live or restart-only, then make code and UI behavior match.
