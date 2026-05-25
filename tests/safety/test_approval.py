"""Tests for the dual-channel approval gate (Slack + UI)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from errander.integrations.slack import APPROVE_REACTION, REJECT_REACTION, SlackClient, SlackError
from errander.safety.approval import (
    ApprovalManager,
    PendingApproval,
    await_dual_approval,
    poll_approval,
    request_approval,
)


def _make_slack_client() -> SlackClient:
    return SlackClient(bot_token="xoxb-test", channel_id="C0123456789")


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------

class TestRequestApproval:
    @pytest.mark.asyncio
    async def test_posts_message_and_returns_ts(self) -> None:
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="1700000000.000001")

        ts = await request_approval(client, batch_id="b-001", report="Dry-run OK")

        assert ts == "1700000000.000001"
        client.post_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_contains_batch_id(self) -> None:
        client = _make_slack_client()
        posted_texts: list[str] = []

        async def _capture(text: str, **kwargs: object) -> str:
            posted_texts.append(text)
            return "1700000000.000001"

        client.post_message = _capture  # type: ignore[method-assign]

        await request_approval(client, batch_id="b-xyz-999", report="report")

        assert "b-xyz-999" in posted_texts[0]

    @pytest.mark.asyncio
    async def test_message_contains_approval_instructions(self) -> None:
        client = _make_slack_client()
        posted_texts: list[str] = []

        async def _capture(text: str, **kwargs: object) -> str:
            posted_texts.append(text)
            return "ts"

        client.post_message = _capture  # type: ignore[method-assign]

        await request_approval(client, batch_id="b-001", report="report")

        assert "white_check_mark" in posted_texts[0]
        assert ":x:" in posted_texts[0]

    @pytest.mark.asyncio
    async def test_truncates_long_report(self) -> None:
        client = _make_slack_client()
        posted_texts: list[str] = []

        async def _capture(text: str, **kwargs: object) -> str:
            posted_texts.append(text)
            return "ts"

        client.post_message = _capture  # type: ignore[method-assign]

        long_report = "x" * 5000
        await request_approval(client, batch_id="b-001", report=long_report)

        # Report is truncated to 2800 chars in the message
        assert len(posted_texts[0]) < 5000 + 200  # header overhead

    @pytest.mark.asyncio
    async def test_propagates_slack_error(self) -> None:
        client = _make_slack_client()
        client.post_message = AsyncMock(side_effect=SlackError("channel_not_found"))

        with pytest.raises(SlackError, match="channel_not_found"):
            await request_approval(client, batch_id="b-001", report="report")


# ---------------------------------------------------------------------------
# poll_approval
# ---------------------------------------------------------------------------

class TestPollApproval:
    @pytest.mark.asyncio
    async def test_approved_on_checkmark_reaction(self) -> None:
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[
            {"name": APPROVE_REACTION, "users": ["U_APPROVER"], "count": 1},
        ])

        with patch("asyncio.sleep", AsyncMock()):
            approved, user = await poll_approval(
                client, "ts-001", timeout_seconds=60, poll_interval_seconds=1,
            )

        assert approved is True
        assert user == "U_APPROVER"

    @pytest.mark.asyncio
    async def test_rejected_on_x_reaction(self) -> None:
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[
            {"name": REJECT_REACTION, "users": ["U_REJECTER"], "count": 1},
        ])

        with patch("asyncio.sleep", AsyncMock()):
            approved, user = await poll_approval(
                client, "ts-002", timeout_seconds=60, poll_interval_seconds=1,
            )

        assert approved is False
        assert user == "U_REJECTER"

    @pytest.mark.asyncio
    async def test_reject_takes_priority_over_approve(self) -> None:
        """If both ✅ and ❌ are present, rejection wins."""
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[
            {"name": APPROVE_REACTION, "users": ["U_A"], "count": 1},
            {"name": REJECT_REACTION, "users": ["U_R"], "count": 1},
        ])

        with patch("asyncio.sleep", AsyncMock()):
            approved, user = await poll_approval(
                client, "ts-003", timeout_seconds=60, poll_interval_seconds=1,
            )

        assert approved is False
        assert user == "U_R"

    @pytest.mark.asyncio
    async def test_auto_rejects_on_timeout(self) -> None:
        client = _make_slack_client()
        # Always return empty reactions
        client.get_reactions = AsyncMock(return_value=[])

        with patch("asyncio.sleep", AsyncMock()):
            # Very short timeout with 0-second poll so it expires after one check
            approved, user = await poll_approval(
                client, "ts-004", timeout_seconds=0, poll_interval_seconds=1,
            )

        assert approved is False
        assert user is None

    @pytest.mark.asyncio
    async def test_continues_polling_on_slack_error(self) -> None:
        """A transient Slack error should not abort polling — just skip that poll."""
        client = _make_slack_client()
        call_count = 0

        async def _flaky_reactions(ts: str, **kwargs: object) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SlackError("temporarily_unavailable")
            return [{"name": APPROVE_REACTION, "users": ["U_OK"], "count": 1}]

        client.get_reactions = _flaky_reactions  # type: ignore[method-assign]

        with patch("asyncio.sleep", AsyncMock()):
            approved, user = await poll_approval(
                client, "ts-005", timeout_seconds=60, poll_interval_seconds=1,
            )

        assert approved is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_polls_until_reaction_appears(self) -> None:
        """First two polls return empty; third returns ✅."""
        client = _make_slack_client()
        call_count = 0

        async def _delayed_approval(ts: str, **kwargs: object) -> list:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return []
            return [{"name": APPROVE_REACTION, "users": ["U_LATE"], "count": 1}]

        client.get_reactions = _delayed_approval  # type: ignore[method-assign]

        with patch("asyncio.sleep", AsyncMock()):
            approved, user = await poll_approval(
                client, "ts-006", timeout_seconds=300, poll_interval_seconds=1,
            )

        assert approved is True
        assert call_count == 3


# ---------------------------------------------------------------------------
# ApprovalManager
# ---------------------------------------------------------------------------

class TestApprovalManager:
    def test_register_creates_pending_entry(self) -> None:
        manager = ApprovalManager()
        p = manager.register("batch-01", "Report text")

        assert isinstance(p, PendingApproval)
        assert p.batch_id == "batch-01"
        assert p.report == "Report text"
        assert p.is_decided() is False
        assert len(manager.get_pending()) == 1

    def test_register_stores_slack_ts(self) -> None:
        manager = ApprovalManager()
        p = manager.register("b-02", "report", slack_message_ts="1700.001")

        assert p.slack_message_ts == "1700.001"

    def test_decide_approve_moves_to_history(self) -> None:
        manager = ApprovalManager()
        manager.register("b-03", "report")
        manager.decide("b-03", approved=True, user_id="U_OP")

        assert len(manager.get_pending()) == 0
        history = manager.get_history()
        assert len(history) == 1
        assert history[0].approved is True
        assert history[0].decided_by == "U_OP"

    def test_decide_reject_moves_to_history(self) -> None:
        manager = ApprovalManager()
        manager.register("b-04", "report")
        manager.decide("b-04", approved=False, user_id="U_NO")

        history = manager.get_history()
        assert history[0].approved is False
        assert history[0].decided_by == "U_NO"

    def test_decide_is_idempotent(self) -> None:
        """Calling decide twice for the same batch_id is safe."""
        manager = ApprovalManager()
        manager.register("b-05", "report")
        manager.decide("b-05", approved=True, user_id="first")
        # Second call should not raise
        manager.decide("b-05", approved=False, user_id="second")

        # Only one entry in history — first decision wins
        assert len(manager.get_history()) == 1
        assert manager.get_history()[0].decided_by == "first"

    def test_decide_unknown_batch_is_noop(self) -> None:
        """decide() on an unknown batch_id must not raise."""
        manager = ApprovalManager()
        manager.decide("no-such-batch", approved=True)  # should not raise

    @pytest.mark.asyncio
    async def test_wait_for_decision_returns_when_decided(self) -> None:
        manager = ApprovalManager()
        manager.register("b-06", "report")

        async def _decide_soon() -> None:
            await asyncio.sleep(0.01)
            manager.decide("b-06", approved=True, user_id="U_Q")

        asyncio.create_task(_decide_soon())
        approved, user = await manager.wait_for_decision("b-06", timeout_seconds=5)

        assert approved is True
        assert user == "U_Q"

    @pytest.mark.asyncio
    async def test_wait_for_decision_auto_rejects_on_timeout(self) -> None:
        manager = ApprovalManager()
        manager.register("b-07", "report")

        approved, user = await manager.wait_for_decision("b-07", timeout_seconds=0)

        assert approved is False
        assert user is None
        # Should be in history as auto-rejected
        assert len(manager.get_history()) == 1

    def test_wait_for_decision_raises_for_unknown_batch(self) -> None:
        manager = ApprovalManager()
        with pytest.raises(KeyError, match="no-such"):
            asyncio.get_event_loop().run_until_complete(
                manager.wait_for_decision("no-such-batch")
            )

    def test_get_pending_returns_copy(self) -> None:
        manager = ApprovalManager()
        manager.register("b-08", "r1")
        manager.register("b-09", "r2")

        pending = manager.get_pending()
        assert len(pending) == 2
        # Mutating the returned list should not affect internal state
        pending.clear()
        assert len(manager.get_pending()) == 2

    def test_get_history_limit(self) -> None:
        manager = ApprovalManager()
        for i in range(30):
            manager.register(f"b-{i:02d}", "r")
            manager.decide(f"b-{i:02d}", approved=True)

        # Default limit is 20 — get_history returns newest first
        history = manager.get_history(limit=5)
        assert len(history) == 5
        # Newest first
        assert history[0].batch_id == "b-29"

    def test_result_property_reflects_decision(self) -> None:
        manager = ApprovalManager()
        p = manager.register("b-10", "report")
        manager.decide("b-10", approved=True, user_id="ops")

        # p is now in history and reflects the decision
        assert p.approved is True
        assert p.result == (True, "ops")


# ---------------------------------------------------------------------------
# await_dual_approval
# ---------------------------------------------------------------------------

class TestAwaitDualApproval:
    @pytest.mark.asyncio
    async def test_ui_approval_wins_race(self) -> None:
        """UI decides before Slack — UI result is returned."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="slack-ts-001")

        # Slack poller blocks indefinitely — only UI can decide
        async def _blocking_reactions(ts: str) -> list:
            await asyncio.Event().wait()  # cancelled when slack_task is cancelled
            return []

        client.get_reactions = _blocking_reactions  # type: ignore[method-assign]

        async def _ui_decides() -> None:
            await asyncio.sleep(0)  # yield once to let tasks start
            manager.decide("b-ui", approved=True, user_id="ui")

        asyncio.create_task(_ui_decides())

        approved, user, _ = await await_dual_approval(
            manager, client, "b-ui", "report",
            timeout_seconds=5,
            poll_interval_seconds=0,
        )

        assert approved is True
        assert user == "ui"

    @pytest.mark.asyncio
    async def test_slack_approval_wins_race(self) -> None:
        """Slack reacts immediately — Slack result is returned before UI decides."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="slack-ts-002")
        # Slack returns an approval reaction immediately
        client.get_reactions = AsyncMock(return_value=[
            {"name": APPROVE_REACTION, "users": ["U_SLACK"], "count": 1},
        ])

        approved, user, _ = await await_dual_approval(
            manager, client, "b-slack", "report",
            timeout_seconds=5,
            poll_interval_seconds=0,
        )

        assert approved is True
        assert user == "U_SLACK"
        # Decision should be recorded in history
        assert len(manager.get_history()) == 1

    @pytest.mark.asyncio
    async def test_timeout_auto_rejects(self) -> None:
        """Neither channel decides in time — auto-reject on timeout=0."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="slack-ts-003")
        # Empty reactions — no decision
        client.get_reactions = AsyncMock(return_value=[])

        approved, user, _ = await await_dual_approval(
            manager, client, "b-timeout", "report",
            timeout_seconds=0,
            poll_interval_seconds=0,
        )

        assert approved is False
        assert user is None

    @pytest.mark.asyncio
    async def test_slack_post_failure_falls_back_to_ui_only(self) -> None:
        """Slack post fails → only UI can approve, no exception raised."""
        manager = ApprovalManager()
        # Client raises on post — but we pass None so it's skipped entirely
        # (Slack failure is logged, UI path remains open)

        async def _ui_decides() -> None:
            await asyncio.sleep(0)
            manager.decide("b-no-slack", approved=True, user_id="ui")

        asyncio.create_task(_ui_decides())

        approved, user, _ = await await_dual_approval(
            manager, None, "b-no-slack", "report",
            timeout_seconds=5,
            poll_interval_seconds=0,
        )

        assert approved is True
        assert user == "ui"

    @pytest.mark.asyncio
    async def test_slack_error_on_post_does_not_raise(self) -> None:
        """SlackError on post_message is caught — falls back to UI-only mode."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(side_effect=SlackError("channel_not_found"))

        async def _ui_decides() -> None:
            await asyncio.sleep(0)
            manager.decide("b-slack-err", approved=True, user_id="ui")

        asyncio.create_task(_ui_decides())

        # Should NOT raise — Slack error is logged, UI approval proceeds
        approved, user, _ = await await_dual_approval(
            manager, client, "b-slack-err", "report",
            timeout_seconds=5,
            poll_interval_seconds=0,
        )

        assert approved is True
        assert user == "ui"

    @pytest.mark.asyncio
    async def test_works_without_slack_client(self) -> None:
        """slack_client=None — UI-only approval works correctly."""
        manager = ApprovalManager()

        async def _ui_decides() -> None:
            await asyncio.sleep(0)
            manager.decide("b-ui-only", approved=False, user_id="ui")

        asyncio.create_task(_ui_decides())

        approved, user, _ = await await_dual_approval(
            manager, None, "b-ui-only", "report",
            timeout_seconds=5,
            poll_interval_seconds=0,
        )

        assert approved is False
        assert user == "ui"

    @pytest.mark.asyncio
    async def test_decision_recorded_in_manager_history(self) -> None:
        """After completion the batch appears in history, not pending."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="ts")
        client.get_reactions = AsyncMock(return_value=[
            {"name": APPROVE_REACTION, "users": ["U_OK"], "count": 1},
        ])

        await await_dual_approval(
            manager, client, "b-hist", "report",
            timeout_seconds=5, poll_interval_seconds=0,
        )

        assert len(manager.get_pending()) == 0
        assert len(manager.get_history()) == 1
        assert manager.get_history()[0].batch_id == "b-hist"

    @pytest.mark.asyncio
    async def test_slack_rejection_wins_over_pending_ui(self) -> None:
        """Slack ❌ reaction arrives immediately; UI never clicks — rejection returned."""
        manager = ApprovalManager()
        client = _make_slack_client()
        client.post_message = AsyncMock(return_value="ts-rej")
        client.get_reactions = AsyncMock(return_value=[
            {"name": REJECT_REACTION, "users": ["U_NO"], "count": 1},
        ])

        approved, user, _ = await await_dual_approval(
            manager, client, "b-rej", "report",
            timeout_seconds=5, poll_interval_seconds=0,
        )

        assert approved is False
        assert user == "U_NO"
