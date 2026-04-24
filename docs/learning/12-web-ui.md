# 12 — Web UI (built into aiohttp server)

## What Was Built and Why

The agent already exposed a Prometheus `/metrics` endpoint via aiohttp. The problem: operators needed three separate tools to get a full picture — CLI (`--audit`), Grafana (metrics), and Slack (notifications). The goal was a single URL showing everything in one place.

The solution: extend the *existing* aiohttp server with `/ui` routes. Same process, same port (9090), no new dependencies.

```
http://localhost:9090/ui              → Dashboard (status, recent batches)
http://localhost:9090/ui/batches      → Full batch history
http://localhost:9090/ui/batches/X    → All events for batch X
http://localhost:9090/ui/vms/dev/web-01 → History for that VM
```

---

## Key Concepts

### 1. aiohttp app data store — typed `AppKey`

aiohttp's `web.Application` acts as a dict for storing shared objects (like a DB connection) that handlers need. The naive approach uses a string key:

```python
app["audit_store"] = store  # works, but produces a NotAppKeyWarning
```

The recommended approach uses a typed `web.AppKey`, which both silences the warning and makes the type checker aware of what's stored:

```python
_AUDIT_STORE_KEY: web.AppKey[AuditStore | None] = web.AppKey("audit_store")
app[_AUDIT_STORE_KEY] = store

# In a handler:
store = request.app.get(_AUDIT_STORE_KEY)  # type: AuditStore | None
```

`web.AppKey` is a typed singleton — the string passed to it is just a human-readable label for debugging. The actual key identity is the object itself, so there's no collision risk with other libraries using the same string.

### 2. Matching URL paths that contain slashes

VM IDs like `dev/web-01` contain slashes. Standard aiohttp path segments (`{vm_id}`) don't match slashes. Use a regex tail pattern:

```python
app.router.add_get(r"/ui/vms/{vm_id:.+}", _ui_vm)
```

`{vm_id:.+}` captures everything after `/ui/vms/` — including slashes — as a single match group. In the handler:

```python
vm_id = request.match_info["vm_id"]  # → "dev/web-01"
```

This is cleaner than URL-encoding slashes (which would require `%2F` in every link).

### 3. Serving HTML from aiohttp

Use `web.Response(text=html, content_type="text/html")`. The `text=` param sets the body as a UTF-8 string. The `content_type=` kwarg sets the `Content-Type` header.

**Never** pass both `content_type=` kwarg AND a `Content-Type` key in the `headers` dict simultaneously — aiohttp raises `ValueError` (see lesson in `tasks/lessons.md`).

### 4. Auto-refresh without JavaScript

The dashboard auto-refreshes every 30 seconds using a single HTML meta tag:

```html
<meta http-equiv="refresh" content="30">
```

Only the dashboard uses this — detail pages don't need it. Passed as a parameter to `_page()`:

```python
def _page(title: str, body: str, *, refresh: int = 0) -> web.Response:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    ...
```

### 5. HTML building without a template engine

For this scale (4 pages, simple tables), Python f-strings and list comprehensions are sufficient. No Jinja2, no added dependency:

```python
def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p><em>No records found.</em></p>"
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<figure><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></figure>"
```

`<figure>` wrapping is a Pico.css convention that adds a subtle container style to tables.

### 6. Zero-CSS styling with Pico.css

[Pico.css classless](https://picocss.com/) styles standard semantic HTML elements (`<table>`, `<nav>`, `<article>`, `<header>`) without requiring any class names. One CDN link in the `<head>` is all that's needed:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.classless.min.css">
```

The tradeoff: requires an internet connection to load the stylesheet. For a private VPN environment, you'd self-host the CSS file. For Phase 1 this is acceptable.

### 7. `audit_store=None` — graceful degradation

The `audit_store` parameter to `start_metrics_server()` is optional (`None` by default). This means:
- Existing tests that call `start_metrics_server()` without arguments still work
- If the audit store fails to initialize, the server still starts — UI pages just show "not connected"

```python
store = request.app.get(_AUDIT_STORE_KEY)
if store is None:
    return _page("Dashboard", "<p>Audit store not connected.</p>", refresh=30)
```

---

## Architecture Decision: Same Server, Same Port

The UI runs on the same aiohttp server as `/metrics` and `/health` (port 9090). The alternative was a separate web server on a different port.

**Why same server:**
- No new process to manage or monitor
- No new port to open in firewall rules
- Direct in-process access to `AuditStore` — no HTTP hop, no serialization
- The metrics server is already a long-lived aiohttp app; adding routes is trivial

**When to separate:** If the UI grows to need authentication, session management, or WebSocket connections, it should move to its own server with its own lifecycle.

---

## Page Structure

| Route | Data source | Key feature |
|---|---|---|
| `/ui` | `get_recent_batches(10)` + `count_events()` | Auto-refresh 30s, summary cards |
| `/ui/batches` | `get_recent_batches(100)` | Clickable batch IDs |
| `/ui/batches/{id}` | `get_events(batch_id=id, limit=500)` | Colour-coded event types |
| `/ui/vms/{vm_id:.+}` | `get_events(vm_id=vm_id, limit=500)` | Slash-safe URL matching |

Every page links to the others — batch detail links to VM pages, VM pages link back to batch pages, nav bar is on every page.

---

## Quiz

1. Why use `web.AppKey` instead of a plain string key on the aiohttp app?
2. What regex pattern captures URL segments that contain slashes?
3. Why does only the dashboard have auto-refresh, not the detail pages?
4. What aiohttp `ValueError` must you avoid when setting `Content-Type` on a response?
5. When would you split the UI into a separate server from `/metrics`?
