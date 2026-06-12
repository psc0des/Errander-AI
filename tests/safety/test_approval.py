"""Tests for the Slack approval notification (notify-and-link — R2).

The pre-R2 reaction decision channel (poll_approval / watch_slack_reactions)
was removed: Slack carries no approval authority. These tests lock in the
notify-and-link message contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from errander.integrations.slack import SlackClient, SlackError
from errander.safety.approval import request_approval


def _make_slack_client() -> SlackClient:
    return SlackClient(bot_token="xoxb-test", channel_id="C0123456789")


async def _capture_post(client: SlackClient) -> list[str]:
    posted: list[str] = []

    async def _capture(text: str, **kwargs: object) -> str:
        posted.append(text)
        return "1700000000.000001"

    client.post_message = _capture  # type: ignore[method-assign]
    return posted


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
        posted = await _capture_post(client)

        await request_approval(client, batch_id="b-xyz-999", report="report")

        assert "b-xyz-999" in posted[0]

    @pytest.mark.asyncio
    async def test_message_links_to_web_ui(self) -> None:
        """R2: the message carries a link to the approval page, not a decision mechanism."""
        client = _make_slack_client()
        posted = await _capture_post(client)

        await request_approval(
            client, batch_id="b-001", report="report",
            web_base_url="http://10.0.0.5:9090",
        )

        assert "http://10.0.0.5:9090/ui/approvals" in posted[0]

    @pytest.mark.asyncio
    async def test_message_has_no_reaction_instructions(self) -> None:
        """Acceptance #1 guard: no Slack reaction can ever look authoritative."""
        client = _make_slack_client()
        posted = await _capture_post(client)

        await request_approval(
            client, batch_id="b-001", report="report",
            web_base_url="http://10.0.0.5:9090",
        )

        assert "white_check_mark" not in posted[0]
        assert "React" not in posted[0]
        assert ":x:" not in posted[0]

    @pytest.mark.asyncio
    async def test_message_without_base_url_points_at_ui_path(self) -> None:
        client = _make_slack_client()
        posted = await _capture_post(client)

        await request_approval(client, batch_id="b-001", report="report")

        assert "/ui/approvals" in posted[0]
        assert "ERRANDER_WEB_BASE_URL" in posted[0]

    @pytest.mark.asyncio
    async def test_message_shows_timeout(self) -> None:
        client = _make_slack_client()
        posted = await _capture_post(client)

        await request_approval(
            client, batch_id="b-001", report="report", timeout_seconds=1800,
        )

        assert "30 min" in posted[0]

    @pytest.mark.asyncio
    async def test_truncates_long_report(self) -> None:
        client = _make_slack_client()
        posted = await _capture_post(client)

        await request_approval(client, batch_id="b-001", report="x" * 5000)

        # Report is truncated to 2800 chars in the message
        assert len(posted[0]) < 5000 + 200  # header overhead

    @pytest.mark.asyncio
    async def test_propagates_slack_error(self) -> None:
        client = _make_slack_client()
        client.post_message = AsyncMock(side_effect=SlackError("channel_not_found"))

        with pytest.raises(SlackError, match="channel_not_found"):
            await request_approval(client, batch_id="b-001", report="report")


class TestReactionChannelRemoved:
    def test_no_reaction_decision_code_paths_exist(self) -> None:
        """R2 acceptance #1: the module exposes no Slack decision machinery."""
        import errander.safety.approval as approval_mod

        assert not hasattr(approval_mod, "poll_approval")
        assert not hasattr(approval_mod, "watch_slack_reactions")

    def test_slack_client_has_no_reactions_api(self) -> None:
        assert not hasattr(SlackClient, "get_reactions")
        assert not hasattr(SlackClient, "conversations_replies")
