"""Slack API client — outbound HTTPS only, reaction polling.

All Slack communication is outbound. No webhooks, no inbound traffic.

Features:
- Post messages to #automaint-approvals
- Poll for emoji reactions (approval/rejection)
- Build formatted messages (reports, alerts, summaries)
- Handle rate limiting gracefully
"""

from __future__ import annotations


class SlackClient:
    """Async Slack API client for outbound-only communication.

    Uses bot token for authentication. All calls are outbound HTTPS
    to api.slack.com.
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        """Initialize Slack client.

        Args:
            bot_token: Slack bot OAuth token.
            channel_id: Default channel for posting.
        """
        self._bot_token = bot_token
        self._channel_id = channel_id

    async def post_message(self, text: str, blocks: list[dict[str, object]] | None = None) -> str:
        """Post a message to the approvals channel.

        Args:
            text: Message text (fallback for notifications).
            blocks: Rich message blocks (optional).

        Returns:
            Message timestamp (ts) for reaction polling.
        """
        raise NotImplementedError("Slack message posting not yet implemented")

    async def get_reactions(self, message_ts: str) -> list[dict[str, object]]:
        """Get reactions on a message.

        Args:
            message_ts: Slack message timestamp.

        Returns:
            List of reaction objects with name and users.
        """
        raise NotImplementedError("Slack reaction polling not yet implemented")

    async def post_alert(self, text: str) -> None:
        """Post a critical alert message.

        Args:
            text: Alert text.
        """
        raise NotImplementedError("Slack alert posting not yet implemented")
