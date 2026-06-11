"""Tests for the Slack approval channel (posting, polling, reaction watcher)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.integrations.slack import APPROVE_REACTION, REJECT_REACTION, SlackClient, SlackError
from errander.safety.approval import (
    poll_approval,
    request_approval,
    watch_slack_reactions,
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
# watch_slack_reactions — transitional R3 channel writing into the store
# ---------------------------------------------------------------------------

class TestWatchSlackReactions:
    @pytest.mark.asyncio
    async def test_approve_reaction_written_with_slack_prefix(self) -> None:
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[
            {"name": APPROVE_REACTION, "users": ["U_OK"], "count": 1},
        ])
        store = AsyncMock()

        with patch("asyncio.sleep", AsyncMock()):
            await watch_slack_reactions(
                client, "ts-w1", store, "b-watch-1",
                timeout_seconds=60, poll_interval_seconds=1,
            )

        store.decide.assert_awaited_once_with(
            "b-watch-1", approved=True, decided_by="slack:U_OK",
        )

    @pytest.mark.asyncio
    async def test_reject_reaction_written(self) -> None:
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[
            {"name": REJECT_REACTION, "users": ["U_NO"], "count": 1},
        ])
        store = AsyncMock()

        with patch("asyncio.sleep", AsyncMock()):
            await watch_slack_reactions(
                client, "ts-w2", store, "b-watch-2",
                timeout_seconds=60, poll_interval_seconds=1,
            )

        store.decide.assert_awaited_once_with(
            "b-watch-2", approved=False, decided_by="slack:U_NO",
        )

    @pytest.mark.asyncio
    async def test_poll_timeout_writes_nothing(self) -> None:
        """Poll timeout is NOT a decision — wait_for_decision owns timeouts."""
        client = _make_slack_client()
        client.get_reactions = AsyncMock(return_value=[])
        store = AsyncMock()

        with patch("asyncio.sleep", AsyncMock()):
            await watch_slack_reactions(
                client, "ts-w3", store, "b-watch-3",
                timeout_seconds=0, poll_interval_seconds=1,
            )

        store.decide.assert_not_awaited()
