"""Prometheus metrics and /metrics + /health HTTP endpoints.

All metrics are module-level singletons registered in a shared CollectorRegistry.
The HTTP server is a lightweight aiohttp app — no framework overhead.

Metrics exposed:
- automaint_actions_total (counter): Actions executed, labeled by type/status/vm
- automaint_action_duration_seconds (histogram): Action execution wall time
- automaint_batch_duration_seconds (histogram): Full batch run time
- automaint_ssh_errors_total (counter): SSH connection failures by vm/reason
- automaint_llm_requests_total (counter): LLM calls labeled by outcome
- automaint_approval_wait_seconds (histogram): Time waiting for human approval
- automaint_vm_lock_held_seconds (histogram): How long VM locks are held
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from automaint.models.events import EventType
from automaint.safety.approval import ApprovalManager
from automaint.safety.audit import AuditStore

#: Typed app keys for storing shared objects on the aiohttp Application.
_AUDIT_STORE_KEY: web.AppKey[AuditStore | None] = web.AppKey("audit_store")
_APPROVAL_MANAGER_KEY: web.AppKey[ApprovalManager | None] = web.AppKey("approval_manager")

logger = logging.getLogger(__name__)

#: Shared registry — all metrics in one place, easy to pass to tests.
REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

ACTIONS_TOTAL = Counter(
    "automaint_actions_total",
    "Total maintenance actions executed",
    ["action_type", "status", "vm_id"],
    registry=REGISTRY,
)

ACTION_DURATION = Histogram(
    "automaint_action_duration_seconds",
    "Time spent executing a single maintenance action",
    ["action_type"],
    buckets=(5, 15, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

BATCH_DURATION = Histogram(
    "automaint_batch_duration_seconds",
    "Time for a full batch maintenance run",
    buckets=(30, 60, 120, 300, 600, 1200, 1800),
    registry=REGISTRY,
)

SSH_ERRORS_TOTAL = Counter(
    "automaint_ssh_errors_total",
    "SSH connection or command failures",
    ["vm_id", "reason"],
    registry=REGISTRY,
)

LLM_REQUESTS_TOTAL = Counter(
    "automaint_llm_requests_total",
    "LLM completion calls",
    ["outcome"],  # "success" | "fallback" | "timeout" | "error"
    registry=REGISTRY,
)

APPROVAL_WAIT = Histogram(
    "automaint_approval_wait_seconds",
    "Seconds waiting for human approval reaction",
    buckets=(30, 60, 120, 300, 600, 900, 1800),
    registry=REGISTRY,
)

VM_LOCK_HELD = Histogram(
    "automaint_vm_lock_held_seconds",
    "Duration VM lock was held",
    ["vm_id"],
    buckets=(10, 30, 60, 120, 300, 600, 1200),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# UI — HTML helpers
# ---------------------------------------------------------------------------

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.classless.min.css"

_EVENT_COLOURS: dict[str, str] = {
    EventType.ACTION_COMPLETED.value: "#2d9e2d",
    EventType.BATCH_COMPLETED.value: "#2d9e2d",
    EventType.ACTION_FAILED.value: "#c0392b",
    EventType.BATCH_STARTED.value: "#2980b9",
    EventType.ACTION_STARTED.value: "#2980b9",
    EventType.BATCH_STARTED.value: "#2980b9",
}


def _coloured(text: str, colour: str) -> str:
    return f'<span style="color:{colour};font-weight:bold">{text}</span>' if colour else text


def _event_cell(event_type: str) -> str:
    return _coloured(event_type, _EVENT_COLOURS.get(event_type, ""))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p><em>No records found.</em></p>"
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        f'<figure><table>'
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{trs}</tbody>"
        f"</table></figure>"
    )


def _page(title: str, body: str, *, refresh: int = 0, pending_count: int = 0) -> web.Response:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    approval_badge = (
        f' <span style="background:#c0392b;color:white;border-radius:10px;'
        f'padding:1px 7px;font-size:0.75rem;vertical-align:middle">'
        f'{pending_count}</span>'
    ) if pending_count > 0 else ""
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  {refresh_tag}
  <title>AutoMaint \u2014 {title}</title>
  <link rel="stylesheet" href="{_PICO_CSS}">
  <style>
    body {{ max-width: 1100px; margin: 0 auto; padding: 1rem 1.5rem; }}
    nav {{ display: flex; gap: 1.5rem; align-items: center;
           border-bottom: 1px solid var(--pico-muted-border-color);
           margin-bottom: 1.5rem; padding-bottom: 0.75rem; }}
    nav strong {{ font-size: 1.1rem; }}
    nav a {{ text-decoration: none; }}
    figure {{ overflow-x: auto; }}
    .btn-approve {{ background:#2d9e2d;color:white;border:none;
                   padding:0.4rem 0.9rem;border-radius:4px;cursor:pointer;
                   font-size:0.9rem; }}
    .btn-reject  {{ background:#c0392b;color:white;border:none;
                   padding:0.4rem 0.9rem;border-radius:4px;cursor:pointer;
                   font-size:0.9rem; }}
  </style>
</head>
<body>
  <nav>
    <strong><a href="/ui">AutoMaint</a></strong>
    <a href="/ui">Dashboard</a>
    <a href="/ui/batches">Batches</a>
    <a href="/ui/approvals">Approvals{approval_badge}</a>
    <a href="/metrics" target="_blank">Metrics</a>
    <a href="/health" target="_blank">Health</a>
  </nav>
  <main>
    <h2>{title}</h2>
    {body}
  </main>
  <footer style="margin-top:2rem;color:var(--pico-muted-color);font-size:0.85rem">
    AutoMaint &mdash; {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
  </footer>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


# ---------------------------------------------------------------------------
# UI — route handlers
# ---------------------------------------------------------------------------

async def _ui_dashboard(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    if store is None:
        return _page(
            "Dashboard",
            "<p>Audit store not connected.</p>",
            refresh=30,
        )

    batches = await store.get_recent_batches(limit=10)
    total = await store.count_events()
    pending_count = len(manager.get_pending()) if manager is not None else 0

    # Summary cards
    batch_count = len(batches)
    approval_card = (
        f'<article style="flex:1;text-align:center">'
        f'<h3 style="color:#c0392b">{pending_count}</h3>'
        f'<p><a href="/ui/approvals">Pending approvals</a></p>'
        f"</article>"
        if pending_count > 0
        else (
            f'<article style="flex:1;text-align:center">'
            f'<h3>{pending_count}</h3>'
            f'<p><a href="/ui/approvals">Pending approvals</a></p>'
            f"</article>"
        )
    )
    cards = (
        f'<div style="display:flex;gap:2rem;margin-bottom:1.5rem">'
        f'<article style="flex:1;text-align:center"><h3>{total}</h3><p>Total events</p></article>'
        f'<article style="flex:1;text-align:center"><h3>{batch_count}</h3><p>Recent batches</p></article>'
        f'<article style="flex:1;text-align:center">'
        f'<h3 style="color:#2d9e2d">\u2714 Running</h3><p>Agent status</p>'
        f"</article>"
        + approval_card
        + f"</div>"
    )

    rows = [
        [
            f'<a href="/ui/batches/{b["batch_id"]}">{b["batch_id"]}</a>',
            str(b["started_at"])[:19],
            str(b["event_count"]),
            ", ".join(
                f'<a href="/ui/vms/{v}">{v}</a>'
                for v in b["vm_ids"]  # type: ignore[union-attr]
            ) or "<em>none</em>",
        ]
        for b in batches
    ]
    table = _table(["Batch ID", "Started at (UTC)", "Events", "VMs"], rows)

    body = (
        cards
        + "<h3>Recent batches</h3>"
        + table
        + "<p style='margin-top:0.5rem;font-size:0.85rem'>"
        + "Auto-refreshes every 30&thinsp;s. "
        + '<a href="/ui/batches">See all batches \u2192</a></p>'
    )
    return _page("Dashboard", body, refresh=30, pending_count=pending_count)


async def _ui_batches(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    if store is None:
        return _page("Batches", "<p>Audit store not connected.</p>")

    batches = await store.get_recent_batches(limit=100)

    rows = [
        [
            f'<a href="/ui/batches/{b["batch_id"]}">{b["batch_id"]}</a>',
            str(b["started_at"])[:19],
            str(b["event_count"]),
            ", ".join(
                f'<a href="/ui/vms/{v}">{v}</a>'
                for v in b["vm_ids"]  # type: ignore[union-attr]
            ) or "<em>none</em>",
        ]
        for b in batches
    ]
    table = _table(["Batch ID", "Started at (UTC)", "Events", "VMs"], rows)
    return _page("Batches", table)


async def _ui_batch_detail(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    batch_id = request.match_info["batch_id"]

    if store is None:
        return _page(f"Batch: {batch_id}", "<p>Audit store not connected.</p>")

    events = await store.get_events(batch_id=batch_id, limit=500)

    back = '<p><a href="/ui/batches">\u2190 All batches</a></p>'

    if not events:
        return _page(f"Batch: {batch_id}", back + "<p><em>No events found.</em></p>")

    rows = [
        [
            e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            _event_cell(e.event_type.value),
            f'<a href="/ui/vms/{e.vm_id}">{e.vm_id}</a>' if e.vm_id else "",
            e.action_type or "",
            e.detail,
        ]
        for e in events
    ]
    table = _table(
        ["Timestamp (UTC)", "Event type", "VM", "Action", "Detail"],
        rows,
    )
    return _page(
        f"Batch: {batch_id}",
        back + f"<p>{len(events)} event(s)</p>" + table,
    )


async def _ui_vm(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    vm_id = request.match_info["vm_id"]

    if store is None:
        return _page(f"VM: {vm_id}", "<p>Audit store not connected.</p>")

    events = await store.get_events(vm_id=vm_id, limit=500)

    back = '<p><a href="/ui">\u2190 Dashboard</a></p>'

    if not events:
        return _page(f"VM: {vm_id}", back + "<p><em>No events found.</em></p>")

    rows = [
        [
            e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            _event_cell(e.event_type.value),
            f'<a href="/ui/batches/{e.batch_id}">{e.batch_id}</a>',
            e.action_type or "",
            e.detail,
        ]
        for e in events
    ]
    table = _table(
        ["Timestamp (UTC)", "Event type", "Batch", "Action", "Detail"],
        rows,
    )
    return _page(
        f"VM: {vm_id}",
        back + f"<p>{len(events)} event(s)</p>" + table,
    )


# ---------------------------------------------------------------------------
# Approval UI — list pending + decide
# ---------------------------------------------------------------------------

async def _ui_approvals(request: web.Request) -> web.Response:
    """GET /ui/approvals — list pending approvals and recent decisions."""
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    if manager is None:
        return _page("Approvals", "<p>Approval manager not connected.</p>")

    pending = manager.get_pending()
    history = manager.get_history()
    pending_count = len(pending)

    # --- Pending section ---
    if pending:
        cards: list[str] = []
        for p in pending:
            elapsed_s = int(
                (datetime.now(tz=timezone.utc) - p.posted_at).total_seconds()
            )
            elapsed_min = elapsed_s // 60
            channel_note = (
                "also posted to Slack" if p.slack_message_ts else "UI only"
            )
            report_escaped = (
                p.report[:600]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            cards.append(
                f'<article style="border:1px solid var(--pico-muted-border-color);'
                f'padding:1rem;border-radius:6px;margin-bottom:1rem">'
                f'<h4 style="margin:0 0 0.5rem">Batch: <code>{p.batch_id}</code></h4>'
                f'<p style="margin:0 0 0.5rem;font-size:0.85rem;color:var(--pico-muted-color)">'
                f'Requested {elapsed_min}m ago &bull; {channel_note}</p>'
                f'<details style="margin-bottom:0.75rem">'
                f'<summary style="cursor:pointer;font-size:0.85rem">View report</summary>'
                f'<pre style="font-size:0.75rem;overflow:auto;max-height:200px;'
                f'background:var(--pico-card-background-color);padding:0.5rem;'
                f'border-radius:4px;margin-top:0.5rem">{report_escaped}</pre>'
                f'</details>'
                f'<div style="display:flex;gap:0.75rem">'
                f'<form method="POST" action="/ui/approvals/{p.batch_id}/approve">'
                f'<button type="submit" class="btn-approve">&#x2705; Approve</button>'
                f'</form>'
                f'<form method="POST" action="/ui/approvals/{p.batch_id}/reject">'
                f'<button type="submit" class="btn-reject">&#x274C; Reject</button>'
                f'</form>'
                f'</div>'
                f'</article>'
            )
        pending_section = (
            f'<h3>{pending_count} Pending</h3>' + "".join(cards)
        )
    else:
        pending_section = (
            "<h3>Pending</h3><p><em>No pending approvals. "
            "Approvals appear here when a dry-run batch completes.</em></p>"
        )

    # --- History section ---
    if history:
        rows = [
            [
                f'<a href="/ui/batches/{h.batch_id}">{h.batch_id}</a>',
                h.posted_at.strftime("%Y-%m-%d %H:%M:%S"),
                (
                    '<span style="color:#2d9e2d;font-weight:bold">&#x2705; Approved</span>'
                    if h.approved
                    else '<span style="color:#c0392b;font-weight:bold">&#x274C; Rejected</span>'
                ),
                h.decided_by or "timeout",
            ]
            for h in history
        ]
        history_section = "<h3>Recent Decisions</h3>" + _table(
            ["Batch ID", "Requested at (UTC)", "Decision", "Decided by"],
            rows,
        )
    else:
        history_section = (
            "<h3>Recent Decisions</h3><p><em>No decisions yet.</em></p>"
        )

    return _page(
        "Approvals",
        pending_section + history_section,
        refresh=15,
        pending_count=pending_count,
    )


async def _ui_approval_decide(request: web.Request) -> web.Response:
    """POST /ui/approvals/{batch_id}/{action} — approve or reject via web form."""
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    if manager is None:
        return web.Response(status=503, text="Approval manager not connected")

    batch_id = request.match_info["batch_id"]
    action = request.match_info["action"]  # "approve" | "reject"
    approved = action == "approve"

    manager.decide(batch_id, approved=approved, user_id="ui")
    logger.info(
        "UI %s for batch %s", "approved" if approved else "rejected", batch_id,
    )
    raise web.HTTPFound("/ui/approvals")


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def _metrics_handler(request: web.Request) -> web.Response:
    """Serve Prometheus /metrics in text exposition format."""
    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def _health_handler(request: web.Request) -> web.Response:
    """Serve /health liveness check.

    Returns 200 OK with a JSON body. No dependency checks — if the
    process is alive enough to serve HTTP, it's alive.
    """
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def start_metrics_server(
    port: int = 9090,
    audit_store: AuditStore | None = None,
    approval_manager: ApprovalManager | None = None,
) -> web.AppRunner:
    """Start the Prometheus metrics, health, and web UI HTTP server.

    Serves:
    - GET  /metrics                              — Prometheus text format
    - GET  /health                               — {"status": "ok"}
    - GET  /ui                                   — Dashboard
    - GET  /ui/batches                           — All recent batches
    - GET  /ui/batches/{id}                      — Events for one batch
    - GET  /ui/vms/{vm_id}                       — History for one VM (vm_id may contain /)
    - GET  /ui/approvals                         — Pending + decided approvals
    - POST /ui/approvals/{batch_id}/approve      — Approve via UI button
    - POST /ui/approvals/{batch_id}/reject       — Reject via UI button

    Args:
        port: Port to listen on (default 9090).
        audit_store: Connected AuditStore for UI queries. When None, UI
            pages render with a "not connected" message.
        approval_manager: ApprovalManager for dual-channel approval UI. When
            None, the approvals page renders with a "not connected" message.

    Returns:
        Running AppRunner — call runner.cleanup() on shutdown.
    """
    app = web.Application()
    app[_AUDIT_STORE_KEY] = audit_store
    app[_APPROVAL_MANAGER_KEY] = approval_manager

    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ui", _ui_dashboard)
    app.router.add_get("/ui/batches", _ui_batches)
    app.router.add_get("/ui/batches/{batch_id}", _ui_batch_detail)
    app.router.add_get(r"/ui/vms/{vm_id:.+}", _ui_vm)
    app.router.add_get("/ui/approvals", _ui_approvals)
    app.router.add_post(
        r"/ui/approvals/{batch_id:[^/]+}/{action:(approve|reject)}",
        _ui_approval_decide,
    )

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(
        "Server listening on :%d (/metrics, /health, /ui)",
        port,
    )
    return runner
