"""Slack API client — outbound HTTPS only, reaction polling.

All Slack communication is outbound. No webhooks, no inbound traffic.
The agent VM has no public IP — everything goes out via HTTPS to api.slack.com.

Features:
- Post messages to #errander-approvals
- Poll for emoji reactions (approval/rejection)
- Build formatted messages (reports, alerts, summaries)
- Handle rate limiting (HTTP 429) with Retry-After backoff
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

#: Slack API base URL
_SLACK_API = "https://slack.com/api"

#: Reaction names used for approval/rejection
APPROVE_REACTION = "white_check_mark"   # ✅
REJECT_REACTION = "x"                   # ❌


class SlackError(Exception):
    """Raised when a Slack API call fails unrecoverably."""


class SlackClient:
    """Async Slack API client for outbound-only communication.

    Uses bot token for authentication. All calls are outbound HTTPS
    to api.slack.com. Rate limiting is handled automatically.

    Usage:
        client = SlackClient(bot_token="xoxb-...", channel_id="C0123456789")
        ts = await client.post_message("Dry-run complete — review plan")
        reactions = await client.get_reactions(ts)
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        """Initialise Slack client.

        Args:
            bot_token: Slack bot OAuth token (xoxb-...).
            channel_id: Default channel for posting messages.
        """
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        self._session: aiohttp.ClientSession | None = None

    async def post_message(
        self,
        text: str,
        blocks: list[dict[str, object]] | None = None,
        channel_id: str | None = None,
    ) -> str:
        """Post a message to the approvals channel.

        Args:
            text: Message text (fallback for notifications and plain display).
            blocks: Optional Block Kit rich message blocks.
            channel_id: Override default channel.

        Returns:
            Slack message timestamp (ts) — used for reaction polling.

        Raises:
            SlackError: If the API call fails.
        """
        payload: dict[str, object] = {
            "channel": channel_id or self._channel_id,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        data = await self._call("chat.postMessage", payload)
        ts = data.get("ts")
        if not ts:
            msg = "Slack postMessage response missing 'ts'"
            raise SlackError(msg)
        logger.info("Posted Slack message ts=%s", ts)
        return str(ts)

    async def get_reactions(self, message_ts: str, channel_id: str | None = None) -> list[dict[str, object]]:
        """Get reactions on a message.

        Args:
            message_ts: Slack message timestamp returned by post_message.
            channel_id: Override default channel.

        Returns:
            List of reaction dicts: [{"name": "white_check_mark", "users": [...], "count": N}]
        """
        data = await self._call("reactions.get", {
            "channel": channel_id or self._channel_id,
            "timestamp": message_ts,
            "full": True,
        }, http_method="GET")

        message: dict[str, object] = data.get("message") or {}  # type: ignore[assignment]
        reactions: list[dict[str, object]] = message.get("reactions") or []  # type: ignore[assignment]
        return reactions

    async def conversations_replies(
        self,
        thread_ts: str,
        channel_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Fetch replies in a thread. Used by docker_hygiene reply polling.

        Returns the message list including the original thread parent at
        index 0. Each message dict has at minimum ``ts``, ``user``, ``text``,
        and optionally ``bot_id`` / ``subtype``. Callers should filter out
        the bot's own messages by checking for ``bot_id``.

        Args:
            thread_ts: Slack timestamp of the thread parent.
            channel_id: Override default channel.
            limit: Maximum replies to return (Slack defaults to 1000).
        """
        data = await self._call("conversations.replies", {
            "channel": channel_id or self._channel_id,
            "ts": thread_ts,
            "limit": limit,
        }, http_method="GET")
        messages: list[dict[str, object]] = data.get("messages") or []  # type: ignore[assignment]
        return messages

    async def post_alert(self, text: str, channel_id: str | None = None) -> None:
        """Post a critical alert message.

        Args:
            text: Alert text.
            channel_id: Override default channel.
        """
        await self.post_message(f":rotating_light: *ALERT* :rotating_light:\n{text}", channel_id=channel_id)

    async def post_digest(self, text: str, channel_id: str | None = None) -> None:
        """Post a daily probe digest to Slack.

        Args:
            text: Rendered digest text from render_digest_report().
            channel_id: Override default channel.
        """
        await self.post_message(text, channel_id=channel_id)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a reusable aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _call(
        self,
        method: str,
        payload: dict[str, object],
        http_method: str = "POST",
    ) -> dict[str, object]:
        """Make a Slack API call.

        Handles:
        - JSON response parsing
        - Slack `ok: false` error responses
        - HTTP 429 rate limiting (respects Retry-After header, one retry)

        Args:
            method: Slack API method name (e.g., "chat.postMessage").
            payload: Request payload (POST body or GET params).
            http_method: "POST" or "GET".

        Returns:
            Parsed response dict (with `ok` removed).

        Raises:
            SlackError: On API errors or HTTP failures.
        """
        url = f"{_SLACK_API}/{method}"
        session = await self._get_session()
        return await self._make_request(session, http_method, url, payload)

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        http_method: str,
        url: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Execute request with one rate-limit retry."""

        for attempt in range(2):
            ctx = (
                session.get(url, params=payload) if http_method == "GET"  # type: ignore[arg-type]  # aiohttp params stubs
                else session.post(url, json=payload)
            )

            async with ctx as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(
                        "Slack rate limited — retrying after %ds", retry_after,
                    )
                    if attempt == 0:
                        await asyncio.sleep(retry_after)
                        continue  # retry the request
                    raise SlackError(
                        f"Slack rate limit exceeded after retry (Retry-After={retry_after}s)"
                    )

                if resp.status != 200:
                    raise SlackError(f"Slack API HTTP {resp.status} for {url}")

                data: dict[str, object] = await resp.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown_error")
                    raise SlackError(f"Slack API error: {error}")

                return data

        raise SlackError("Slack request failed after retries")
