"""Tests for the approval UI routes (durable ApprovalRequestStore, R3).

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

from errander.safety.approval_store import ApprovalRequestStore
from errander.safety.audit import AuditStore
from errander.web.ui import start_web_server
from tests.conftest import make_test_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_client(
    approval_store: ApprovalRequestStore | None = None,
) -> tuple[TestClient, AuditStore]:
    """Build a test aiohttp app with an audit store + durable approval store."""
    store = AuditStore(make_test_db())
    await store.initialize()
    runner = await start_web_server(
        port=0, audit_store=store, approval_store=approval_store,
    )
    # Extract the underlying app from the runner
    app = runner._app  # type: ignore[attr-defined]
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, store


def _make_approval_store() -> ApprovalRequestStore:
    return ApprovalRequestStore(make_test_db())


async def _register(
    approval_store: ApprovalRequestStore,
    batch_id: str,
    report: str = "report",
    slack_message_ts: str | None = None,
) -> None:
    await approval_store.create(
        batch_id, env_name="dev", plan_id="plan-ui", plan_hash="a" * 64,
        report=report, slack_message_ts=slack_message_ts,
    )


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
# GET /ui/approvals — no store
# ---------------------------------------------------------------------------

class TestApprovalPageNoStore:
    @pytest.mark.asyncio
    async def test_page_loads_without_store(self) -> None:
        client, store = await _make_client(approval_store=None)
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
    async def test_page_loads_with_empty_store(self) -> None:
        approval_store = _make_approval_store()
        client, store = await _make_client(approval_store=approval_store)
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
        approval_store = _make_approval_store()
        client, store = await _make_client(approval_store=approval_store)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "No pending approvals" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_page_shows_nav_link(self) -> None:
        approval_store = _make_approval_store()
        client, store = await _make_client(approval_store=approval_store)
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
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-test-01", "Freed 1.5 GB on prod/web-01")
        client, store = await _make_client(approval_store=approval_store)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "batch-test-01" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_report_excerpt_shown(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-test-02", "Freed 1.5 GB on prod/web-01")
        client, store = await _make_client(approval_store=approval_store)
        try:
            resp = await client.get("/ui/approvals")
            text = await resp.text()
            assert "Freed 1.5 GB" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_approve_button_present(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-test-03")
        client, store = await _make_client(approval_store=approval_store)
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
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-test-04")
        client, store = await _make_client(approval_store=approval_store)
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
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-test-05", slack_message_ts="1700.1")
        client, store = await _make_client(approval_store=approval_store)
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
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-app-01")
        client, store = await _make_client(approval_store=approval_store)
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
    async def test_approve_records_decision_in_store(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-app-02")
        client, store = await _make_client(approval_store=approval_store)
        try:
            await _csrf_post(client, "/ui/approvals/batch-app-02/approve")

            assert await approval_store.count_pending() == 0
            history = await approval_store.get_history()
            assert len(history) == 1
            assert history[0].approved is True
            assert history[0].decided_by == "ui:ui"  # ui:<username>, default "ui"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_approve_signals_waiting_coroutine(self) -> None:
        """POSTing approve should unblock a coroutine waiting on the store."""
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-app-03")
        client, store = await _make_client(approval_store=approval_store)
        try:
            import asyncio
            result: list[tuple[bool | None, str | None]] = []

            async def _waiter() -> None:
                request = await approval_store.wait_for_decision(
                    "batch-app-03", timeout_seconds=5,
                )
                result.append((request.approved, request.decided_by))

            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0)  # let the waiter start

            await _csrf_post(client, "/ui/approvals/batch-app-03/approve")
            await asyncio.wait_for(task, timeout=5)

            assert result == [(True, "ui:ui")]
        finally:
            await client.close()
            await store.close()


# ---------------------------------------------------------------------------
# POST /ui/approvals/{batch_id}/reject
# ---------------------------------------------------------------------------

class TestApprovalDecideReject:
    @pytest.mark.asyncio
    async def test_reject_redirects_to_approvals_page(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-rej-01")
        client, store = await _make_client(approval_store=approval_store)
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
    async def test_reject_records_decision_in_store(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-rej-02")
        client, store = await _make_client(approval_store=approval_store)
        try:
            await _csrf_post(client, "/ui/approvals/batch-rej-02/reject")

            history = await approval_store.get_history()
            assert len(history) == 1
            assert history[0].approved is False
            assert history[0].decided_by == "ui:ui"
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_reject_signals_waiting_coroutine(self) -> None:
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-rej-03")
        client, store = await _make_client(approval_store=approval_store)
        try:
            import asyncio
            result: list[tuple[bool | None, str | None]] = []

            async def _waiter() -> None:
                request = await approval_store.wait_for_decision(
                    "batch-rej-03", timeout_seconds=5,
                )
                result.append((request.approved, request.decided_by))

            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0)

            await _csrf_post(client, "/ui/approvals/batch-rej-03/reject")
            await asyncio.wait_for(task, timeout=5)

            assert result == [(False, "ui:ui")]
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_decide_unknown_batch_is_noop(self) -> None:
        """Posting decide for a batch that was never registered should not error."""
        approval_store = _make_approval_store()
        client, store = await _make_client(approval_store=approval_store)
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
# POST /ui/approvals — no store (503)
# ---------------------------------------------------------------------------

class TestApprovalDecideNoStore:
    @pytest.mark.asyncio
    async def test_approve_returns_503_without_store(self) -> None:
        client, store = await _make_client(approval_store=None)
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
        approval_store = _make_approval_store()
        await _register(approval_store, "batch-dash-01")
        client, store = await _make_client(approval_store=approval_store)
        try:
            resp = await client.get("/ui")
            text = await resp.text()
            assert "Pending approvals" in text
        finally:
            await client.close()
            await store.close()

    @pytest.mark.asyncio
    async def test_dashboard_zero_pending_no_badge(self) -> None:
        approval_store = _make_approval_store()
        client, store = await _make_client(approval_store=approval_store)
        try:
            resp = await client.get("/ui")
            text = await resp.text()
            # Should still show the card, just with 0
            assert "Pending approvals" in text
        finally:
            await client.close()
            await store.close()
