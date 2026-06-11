"""Tests for the approval UI routes.

Uses aiohttp.test_utils.TestClient — no real TCP port needed.
Tests cover:
- GET  /ui/approvals             — empty state + pending state
- POST /ui/approvals/{id}/approve
- POST /ui/approvals/{id}/reject
- POST with no pending approval (idempotent)
- Dashboard shows pending count card
"""

from __future__ import annotations

import re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from errander.observability.metrics import start_metrics_server
from errander.safety.approval import ApprovalManager
from errander.safety.audit import AuditStore
from tests.conftest import make_test_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_client(
    manager: ApprovalManager | None = None,
) -> tuple[TestClient, AuditStore]:
    """Build a test aiohttp app with an in-memory store + approval manager."""
    store = AuditStore(make_test_db())
    await store.initialize()
    runner = await start_metrics_server(
        port=0, audit_store=store, approval_manager=manager,
    )
    # Extract the underlying app from the runner
    app = runner._app  # type: ignore[attr-defined]
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, store


async def _csrf_post(
    client: TestClient,
    url: str,
    *,
    allow_redirects: bool = True,
    data: dict[str, str] | None = None,
) -> object:
    """GET /ui/approvals to receive the CSRF cookie, then POST with the token.

    The CSRF middleware uses a double-submit cookie pattern: a GET to any /ui/*
    page sets the cookie and injects the HMAC token into forms. This helper
    replicates that flow so unit tests don't need to be aware of the internals.
    """
    # /ui/settings always renders a form → reliable CSRF token source
    # (approvals page has no form when there are no pending approvals)
    get_resp = await client.get("/ui/settings")
    html = await get_resp.text()
    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    token = m.group(1) if m else ""
    post_data = {"_csrf_token": token, **(data or {})}
    return await client.post(url, data=post_data, allow_redirects=allow_redirects)


# ---------------------------------------------------------------------------
# GET /ui/approvals — no manager
# ---------------------------------------------------------------------------

class TestApprovalPageNoManager:
    @pytest.mark.asyncio
    async def test_page_loads_without_manager(self) -> None:
        client, store = await _make_client(manager=None)
        try:
            resp = await client.get("/ui/approvals")
            assert resp.status == 200
            text = await resp.text()
            assert "not connected" in text
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# GET /ui/approvals — empty pending
# ---------------------------------------------------------------------------

class TestApprovalPageEmpty:
    @pytest.mark.asyncio
    async def test_page_loads_with_empty_manager(self) -> None:
        manager = ApprovalManager()
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            assert resp.status == 200
            text = await resp.text()
            assert "Approvals" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_page_shows_no_pending_message(self) -> None:
        manager = ApprovalManager()
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "No pending approvals" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_page_shows_nav_link(self) -> None:
        manager = ApprovalManager()
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "/ui/approvals" in text
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# GET /ui/approvals — with pending approval
# ---------------------------------------------------------------------------

class TestApprovalPageWithPending:
    @pytest.mark.asyncio
    async def test_pending_approval_shown(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-test-01", "Freed 1.5 GB on prod/web-01")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "batch-test-01" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_report_excerpt_shown(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-test-02", "Freed 1.5 GB on prod/web-01")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "Freed 1.5 GB" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_approve_button_present(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-test-03", "report")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "Approve" in text
            assert "/ui/approvals/batch-test-03/approve" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_reject_button_present(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-test-04", "report")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "Reject" in text
            assert "/ui/approvals/batch-test-04/reject" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_slack_channel_note_shown(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-test-05", "report", slack_message_ts="1700.1")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "Slack" in text
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# POST /ui/approvals/{batch_id}/approve
# ---------------------------------------------------------------------------

class TestApprovalDecideApprove:
    @pytest.mark.asyncio
    async def test_approve_redirects_to_approvals_page(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-app-01", "report")
        client, store = await _make_client(manager=manager)
        try:
            resp = await _csrf_post(
                client, "/ui/approvals/batch-app-01/approve", allow_redirects=False,
            )
            assert resp.status == 302
            assert resp.headers["Location"] == "/ui/approvals"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_approve_records_decision_in_manager(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-app-02", "report")
        client, store = await _make_client(manager=manager)
        try:
            await _csrf_post(client, "/ui/approvals/batch-app-02/approve")

            assert len(manager.get_pending()) == 0
            history = manager.get_history()
            assert len(history) == 1
            assert history[0].approved is True
            assert history[0].decided_by == "ui"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_approve_signals_waiting_coroutine(self) -> None:
        """POSTing approve should unblock a coroutine waiting on the event."""
        manager = ApprovalManager()
        manager.register("batch-app-03", "report")
        client, store = await _make_client(manager=manager)
        try:
            import asyncio
            result: list[tuple[bool, str | None]] = []

            async def _waiter() -> None:
                approved, user = await manager.wait_for_decision(
                    "batch-app-03", timeout_seconds=5,
                )
                result.append((approved, user))

            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0)  # let the waiter start

            await _csrf_post(client, "/ui/approvals/batch-app-03/approve")
            await asyncio.wait_for(task, timeout=2)

            assert result == [(True, "ui")]
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# POST /ui/approvals/{batch_id}/reject
# ---------------------------------------------------------------------------

class TestApprovalDecideReject:
    @pytest.mark.asyncio
    async def test_reject_redirects_to_approvals_page(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-rej-01", "report")
        client, store = await _make_client(manager=manager)
        try:
            resp = await _csrf_post(
                client, "/ui/approvals/batch-rej-01/reject", allow_redirects=False,
            )
            assert resp.status == 302
            assert resp.headers["Location"] == "/ui/approvals"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_reject_records_decision_in_manager(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-rej-02", "report")
        client, store = await _make_client(manager=manager)
        try:
            await _csrf_post(client, "/ui/approvals/batch-rej-02/reject")

            history = manager.get_history()
            assert len(history) == 1
            assert history[0].approved is False
            assert history[0].decided_by == "ui"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_reject_signals_waiting_coroutine(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-rej-03", "report")
        client, store = await _make_client(manager=manager)
        try:
            import asyncio
            result: list[tuple[bool, str | None]] = []

            async def _waiter() -> None:
                approved, user = await manager.wait_for_decision(
                    "batch-rej-03", timeout_seconds=5,
                )
                result.append((approved, user))

            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0)

            await _csrf_post(client, "/ui/approvals/batch-rej-03/reject")
            await asyncio.wait_for(task, timeout=2)

            assert result == [(False, "ui")]
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_decide_unknown_batch_is_noop(self) -> None:
        """Posting decide for a batch that was never registered should not error."""
        manager = ApprovalManager()
        client, store = await _make_client(manager=manager)
        try:
            resp = await _csrf_post(
                client, "/ui/approvals/no-such-batch/approve", allow_redirects=False,
            )
            # Should still redirect — idempotent, not an error
            assert resp.status == 302
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# POST /ui/approvals — no manager (503)
# ---------------------------------------------------------------------------

class TestApprovalDecideNoManager:
    @pytest.mark.asyncio
    async def test_approve_returns_503_without_manager(self) -> None:
        client, store = await _make_client(manager=None)
        try:
            resp = await _csrf_post(
                client, "/ui/approvals/b-any/approve", allow_redirects=False,
            )
            assert resp.status == 503
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# Dashboard — pending count card
# ---------------------------------------------------------------------------

class TestDashboardPendingCard:
    @pytest.mark.asyncio
    async def test_dashboard_shows_pending_count(self) -> None:
        manager = ApprovalManager()
        manager.register("batch-dash-01", "report")
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui")
            text = await resp.text()
            assert "Pending approvals" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_dashboard_zero_pending_no_badge(self) -> None:
        manager = ApprovalManager()
        client, store = await _make_client(manager=manager)
        try:
            resp = await client.get("/ui")
            text = await resp.text()
            # Should still show the card, just with 0
            assert "Pending approvals" in text
        finally:
            await client.close()
            await store.close()
