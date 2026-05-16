"""Playwright tests for the Errander-AI web UI.

Architecture:
- The aiohttp server runs in a background thread with its own event loop
  so it can handle HTTP requests while Playwright tests run synchronously.
- Tests use the sync Playwright API (playwright.sync_api) — no async needed.
- The server fixture is module-scoped: one server starts, all 25 tests run
  against it, then it shuts down.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from playwright.sync_api import Page, expect

from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import start_metrics_server
from errander.safety.audit import AuditStore

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


async def _setup_server() -> tuple[AuditStore, object, int]:
    """Initialise store, seed data, start server. Returns (store, runner, port)."""
    store = AuditStore(":memory:")
    await store.initialize()

    b1, b2 = "batch-2026-04-01", "batch-2026-04-03"
    events = [
        # Batch 1 — two VMs, mixed outcome
        AuditEvent(EventType.BATCH_STARTED, b1,
                   detail="Batch started", timestamp=_ts(2026, 4, 1, 2)),
        AuditEvent(EventType.ACTION_STARTED, b1, "prod/web-01", "disk_cleanup",
                   "Starting disk cleanup", _ts(2026, 4, 1, 2, 1)),
        AuditEvent(EventType.ACTION_COMPLETED, b1, "prod/web-01", "disk_cleanup",
                   "Freed 2.3 GB", _ts(2026, 4, 1, 2, 5)),
        AuditEvent(EventType.ACTION_STARTED, b1, "prod/db-01", "disk_cleanup",
                   "Starting disk cleanup", _ts(2026, 4, 1, 2, 1)),
        AuditEvent(EventType.ACTION_FAILED, b1, "prod/db-01", "disk_cleanup",
                   "SSH timeout after 300s", _ts(2026, 4, 1, 2, 6)),
        AuditEvent(EventType.BATCH_COMPLETED, b1,
                   detail="1 success, 1 failed", timestamp=_ts(2026, 4, 1, 3)),
        # Batch 2 — single VM, clean run
        AuditEvent(EventType.BATCH_STARTED, b2,
                   detail="Batch started", timestamp=_ts(2026, 4, 3, 2)),
        AuditEvent(EventType.ACTION_STARTED, b2, "staging/app-01", "disk_cleanup",
                   "Starting disk cleanup", _ts(2026, 4, 3, 2, 1)),
        AuditEvent(EventType.ACTION_COMPLETED, b2, "staging/app-01", "disk_cleanup",
                   "Freed 512 MB", _ts(2026, 4, 3, 2, 4)),
        AuditEvent(EventType.BATCH_COMPLETED, b2,
                   detail="1 success", timestamp=_ts(2026, 4, 3, 3)),
    ]
    for e in events:
        await store.log_event(e)

    runner = await start_metrics_server(port=0, audit_store=store)
    site = list(runner.sites)[0]
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return store, runner, port


# ---------------------------------------------------------------------------
# Server fixture — runs in a background thread
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ui_base_url() -> str:  # type: ignore[return]
    """Start the aiohttp server in a background thread. Yield base URL."""
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        store, runner, port = await _setup_server()
        stop: asyncio.Event = asyncio.Event()
        ctx["store"] = store
        ctx["runner"] = runner
        ctx["stop"] = stop
        ctx["port"] = port
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

    yield f"http://localhost:{ctx['port']}"

    # Signal shutdown — call_soon_threadsafe is safe across threads
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_page_loads(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        expect(page).to_have_title("Errander-AI — Dashboard")

    def test_shows_running_status(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        expect(page.get_by_text("Running")).to_be_visible()

    def test_shows_total_event_count(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        expect(page.get_by_role("heading", name="10", exact=True)).to_be_visible()

    def test_shows_both_batches(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        expect(page.get_by_text("batch-2026-04-01")).to_be_visible()
        expect(page.get_by_text("batch-2026-04-03")).to_be_visible()

    def test_batch_link_navigates_to_detail(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        page.get_by_role("link", name="batch-2026-04-01").first.click()
        expect(page).to_have_url(f"{ui_base_url}/ui/batches/batch-2026-04-01")

    def test_nav_links_all_present(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui")
        expect(page.get_by_role("link", name="Dashboard")).to_be_visible()
        expect(page.get_by_role("link", name="Batches", exact=True).first).to_be_visible()
        expect(page.get_by_role("link", name="Metrics")).to_be_visible()
        expect(page.get_by_role("link", name="Health")).to_be_visible()


# ---------------------------------------------------------------------------
# Batch list
# ---------------------------------------------------------------------------

class TestBatchList:
    def test_page_loads(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches")
        expect(page).to_have_title("Errander-AI — Batches")

    def test_both_batches_listed(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches")
        expect(page.get_by_text("batch-2026-04-01")).to_be_visible()
        expect(page.get_by_text("batch-2026-04-03")).to_be_visible()

    def test_batch_link_navigates_to_detail(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches")
        page.get_by_role("link", name="batch-2026-04-03").click()
        expect(page).to_have_url(f"{ui_base_url}/ui/batches/batch-2026-04-03")


# ---------------------------------------------------------------------------
# Batch detail
# ---------------------------------------------------------------------------

class TestBatchDetail:
    def test_page_loads(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        expect(page).to_have_title("Errander-AI — Batch: batch-2026-04-01")

    def test_shows_event_count(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        expect(page.get_by_text("6 event(s)")).to_be_visible()

    def test_completed_event_shown(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        expect(page.get_by_text("action_completed")).to_be_visible()

    def test_failed_event_shown(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        expect(page.get_by_text("action_failed")).to_be_visible()

    def test_detail_text_shown(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        expect(page.get_by_text("Freed 2.3 GB")).to_be_visible()
        expect(page.get_by_text("SSH timeout after 300s")).to_be_visible()

    def test_vm_link_navigates_to_vm_page(self, page: Page, ui_base_url: str) -> None:
        from urllib.parse import quote
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        page.get_by_role("link", name="prod/web-01").first.click()
        # vm_ids containing "/" are URL-encoded in the href; browser preserves %2F
        encoded = quote("prod/web-01", safe="")
        expect(page).to_have_url(f"{ui_base_url}/ui/vms/{encoded}")

    def test_back_link_returns_to_list(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/batch-2026-04-01")
        page.get_by_role("link", name="← All batches").click()
        expect(page).to_have_url(f"{ui_base_url}/ui/batches")

    def test_nonexistent_batch_shows_empty(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/batches/no-such-batch")
        expect(page.get_by_text("No events found")).to_be_visible()


# ---------------------------------------------------------------------------
# VM history
# ---------------------------------------------------------------------------

class TestVMHistory:
    def test_page_loads(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/vms/prod/web-01")
        expect(page).to_have_title("Errander-AI — VM: prod/web-01")

    def test_shows_event_count(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/vms/prod/web-01")
        expect(page.get_by_text("2 event(s)")).to_be_visible()

    def test_shows_action_detail(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/vms/prod/web-01")
        expect(page.get_by_text("Freed 2.3 GB")).to_be_visible()

    def test_links_back_to_batch(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/vms/prod/web-01")
        page.get_by_role("link", name="batch-2026-04-01").first.click()
        expect(page).to_have_url(f"{ui_base_url}/ui/batches/batch-2026-04-01")

    def test_vm_id_with_slash_in_url(self, page: Page, ui_base_url: str) -> None:
        """staging/app-01 — slash must be part of the URL path, not encoded."""
        page.goto(f"{ui_base_url}/ui/vms/staging/app-01")
        expect(page).to_have_title("Errander-AI — VM: staging/app-01")
        expect(page.get_by_text("Freed 512 MB")).to_be_visible()

    def test_nonexistent_vm_shows_empty(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/ui/vms/no/such/vm")
        expect(page.get_by_text("No events found")).to_be_visible()


# ---------------------------------------------------------------------------
# Endpoints smoke tests
# ---------------------------------------------------------------------------

class TestEndpoints:
    def test_health_returns_ok(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/health")
        expect(page.get_by_text("ok")).to_be_visible()

    def test_metrics_serves_prometheus_format(self, page: Page, ui_base_url: str) -> None:
        page.goto(f"{ui_base_url}/metrics")
        expect(page.get_by_text("errander_actions_total")).to_be_visible()
