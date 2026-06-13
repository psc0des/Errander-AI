"""Interactive browser session for the Errander-AI UI.

Starts the aiohttp server in a background thread with realistic seeded data
(including a pending approval), then opens a headed Chromium browser so you
can click around and test the UI interactively.

Usage:
    uv run python scripts/browse_ui.py

The browser opens automatically. Press Enter in the terminal to close.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from errander.models.events import AuditEvent, EventType
from errander.safety.approval import ApprovalManager
from errander.safety.audit import AuditStore
from errander.web.ui import start_web_server


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


_SAMPLE_REPORT = """\
Errander-AI Dry-Run Report — batch-2026-04-13
============================================
Environment : production
Targets     : 3 VMs
Mode        : DRY-RUN (no changes applied)

Actions planned
---------------
prod/web-01   disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: apt-get clean
  [DRY-RUN] Would execute: journalctl --vacuum-time=30d
  Estimated space freed: ~950 MB

prod/db-01    disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: apt-get autoremove -y
  Estimated space freed: ~1.8 GB

staging/app-01  disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: dnf clean all
  Estimated space freed: ~430 MB

Summary
-------
Total estimated space to free: ~3.2 GB across 3 VMs
No high-risk actions planned.
Approve to run live. Reject to cancel.
"""


async def _setup_server() -> tuple[AuditStore, ApprovalManager, object, int]:
    store = AuditStore(":memory:")
    await store.initialize()

    # Seed audit events
    events = [
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-01", detail="Batch started",              timestamp=_ts(2026, 4, 1, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 1, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 2.3 GB",             timestamp=_ts(2026, 4, 1, 2, 5)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-01", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 1, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_FAILED,    batch_id="batch-2026-04-01", vm_id="prod/db-01",     action_type="disk_cleanup", detail="SSH timeout after 300s",  timestamp=_ts(2026, 4, 1, 2, 6)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-01", detail="1 success, 1 failed",        timestamp=_ts(2026, 4, 1, 3)),
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-02", detail="Batch started",              timestamp=_ts(2026, 4, 2, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-02", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 2, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-02", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 1.1 GB",             timestamp=_ts(2026, 4, 2, 2, 4)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-02", vm_id="staging/app-01", action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 2, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-02", vm_id="staging/app-01", action_type="disk_cleanup", detail="Freed 512 MB",             timestamp=_ts(2026, 4, 2, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-02", detail="2 success",                 timestamp=_ts(2026, 4, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-10", detail="Batch started",              timestamp=_ts(2026, 4, 10, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 800 MB",             timestamp=_ts(2026, 4, 10, 2, 4)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Freed 2.1 GB",             timestamp=_ts(2026, 4, 10, 2, 6)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="staging/app-01", action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="staging/app-01", action_type="disk_cleanup", detail="Freed 320 MB",             timestamp=_ts(2026, 4, 10, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-10", detail="3 success",                 timestamp=_ts(2026, 4, 10, 3)),
    ]
    for e in events:
        await store.log_event(e)

    # Seed approval manager
    manager = ApprovalManager()
    manager.register(
        "batch-2026-04-13",
        _SAMPLE_REPORT,
        slack_message_ts="1744500000.000001",
    )
    manager.register("batch-2026-04-12", "Freed 2.1 GB on prod/db-01 (dry-run)")
    manager.decide("batch-2026-04-12", approved=True, user_id="ops-team")

    runner = await start_web_server(port=0, audit_store=store, approval_manager=manager)
    site = list(runner.sites)[0]
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return store, manager, runner, port


def _start_server() -> tuple[str, asyncio.AbstractEventLoop, object, AuditStore]:
    """Start the server in a background thread. Returns (base_url, loop, runner, store)."""
    ready = threading.Event()
    ctx: dict = {}

    async def _run() -> None:
        store, manager, runner, port = await _setup_server()
        stop: asyncio.Event = asyncio.Event()
        ctx["store"] = store
        ctx["runner"] = runner
        ctx["stop"] = stop
        ctx["port"] = port
        ctx["manager"] = manager
        ready.set()
        await stop.wait()
        await runner.cleanup()  # type: ignore[union-attr]
        await store.close()

    loop = asyncio.new_event_loop()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    ready.wait(timeout=10)
    return f"http://localhost:{ctx['port']}", loop, ctx, t


def main() -> None:
    print("Starting Errander-AI server with sample data...")
    base_url, loop, ctx, thread = _start_server()

    print(f"\n  Server running at: {base_url}/ui")
    print(f"  Approvals page  : {base_url}/ui/approvals")
    print(f"  Metrics         : {base_url}/metrics")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # --- Dashboard ---
        print("[1/5] Dashboard — checking summary cards and recent batches...")
        page.goto(f"{base_url}/ui")
        page.wait_for_load_state("networkidle")
        title = page.title()
        print(f"      Title: {title}")
        assert "Dashboard" in title
        print("      PASS: Dashboard loaded")

        # --- Approvals page (has pending + history) ---
        print("[2/5] Approvals page — checking pending approval and history...")
        page.goto(f"{base_url}/ui/approvals")
        page.wait_for_load_state("networkidle")
        assert "batch-2026-04-13" in page.content()
        assert "Approve" in page.content()
        assert "Reject" in page.content()
        assert "batch-2026-04-12" in page.content()  # history entry
        print("      PASS: Pending approval visible with Approve/Reject buttons")
        print("      PASS: Previous decision visible in history")

        # --- Batch list ---
        print("[3/5] Batch list — checking all batches are listed...")
        page.goto(f"{base_url}/ui/batches")
        page.wait_for_load_state("networkidle")
        assert "batch-2026-04-01" in page.content()
        assert "batch-2026-04-10" in page.content()
        print("      PASS: All batches listed")

        # --- Batch detail ---
        print("[4/5] Batch detail — checking events for batch-2026-04-01...")
        page.goto(f"{base_url}/ui/batches/batch-2026-04-01")
        page.wait_for_load_state("networkidle")
        assert "SSH timeout after 300s" in page.content()
        assert "Freed 2.3 GB" in page.content()
        print("      PASS: Failure and success events visible")

        # --- Approve button click ---
        print("[5/5] Approvals — clicking Approve for batch-2026-04-13...")
        page.goto(f"{base_url}/ui/approvals")
        page.wait_for_load_state("networkidle")
        approve_btn = page.get_by_role("button", name="Approve").first
        approve_btn.click()
        page.wait_for_load_state("networkidle")
        # After approval the page redirects back — batch should be in history now
        assert "batch-2026-04-13" in page.content()
        assert "Approved" in page.content()
        print("      PASS: Approve button worked — decision in history")

        print("\n  All checks passed!")
        print(f"\n  Browse freely at {base_url}/ui")
        print("  Press Enter to close the browser and stop the server.\n")
        input()

        context.close()
        browser.close()

    # Shutdown server
    loop.call_soon_threadsafe(ctx["stop"].set)
    thread.join(timeout=5)
    print("Server stopped.")


if __name__ == "__main__":
    main()
