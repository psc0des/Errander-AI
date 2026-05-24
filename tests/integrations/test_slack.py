"""Tests for Slack API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.integrations.slack import (
    APPROVE_REACTION,
    SlackClient,
    SlackError,
)

# --- Helpers ---

def _make_client() -> SlackClient:
    return SlackClient(bot_token="xoxb-test-token", channel_id="C0123456789")


def _ctx(resp: MagicMock) -> MagicMock:
    """Wrap a response mock as an async context manager (for session.post/get)."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_response(
    ok: bool = True,
    ts: str = "1234567890.123456",
    extra: dict[str, object] | None = None,
) -> MagicMock:
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = 200
    data: dict[str, object] = {"ok": ok, "ts": ts}
    if not ok:
        data["error"] = "invalid_token"
    if extra:
        data.update(extra)
    resp.json = AsyncMock(return_value=data)
    resp.headers = {}
    # async context manager support
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_reactions_response(reactions: list[dict[str, object]]) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={
        "ok": True,
        "message": {"reactions": reactions},
    })
    resp.headers = {}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


# --- SlackClient.post_message ---

class TestPostMessage:
    @pytest.mark.asyncio
    async def test_returns_message_ts(self) -> None:
        client = _make_client()
        mock_resp = _make_response(ts="1700000000.000001")

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(return_value=_ctx(mock_resp))
            mock_session_cls.return_value = mock_session

            ts = await client.post_message("Hello from Errander-AI")

        assert ts == "1700000000.000001"

    @pytest.mark.asyncio
    async def test_posts_to_default_channel(self) -> None:
        client = _make_client()
        mock_resp = _make_response()
        captured_payloads: list[dict] = []

        def _fake_post(url: str, json: dict, **kwargs: object) -> MagicMock:
            captured_payloads.append(json)
            return _ctx(mock_resp)

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = _fake_post
            mock_session_cls.return_value = mock_session

            await client.post_message("test")

        assert captured_payloads[0]["channel"] == "C0123456789"

    @pytest.mark.asyncio
    async def test_posts_to_override_channel(self) -> None:
        client = _make_client()
        mock_resp = _make_response()
        captured_payloads: list[dict] = []

        def _fake_post(url: str, json: dict, **kwargs: object) -> MagicMock:
            captured_payloads.append(json)
            return _ctx(mock_resp)

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = _fake_post
            mock_session_cls.return_value = mock_session

            await client.post_message("test", channel_id="C9999999999")

        assert captured_payloads[0]["channel"] == "C9999999999"

    @pytest.mark.asyncio
    async def test_raises_on_slack_error(self) -> None:
        client = _make_client()
        mock_resp = _make_response(ok=False)

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(return_value=_ctx(mock_resp))
            mock_session_cls.return_value = mock_session

            with pytest.raises(SlackError, match="invalid_token"):
                await client.post_message("test")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self) -> None:
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(return_value={})

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(return_value=_ctx(mock_resp))
            mock_session_cls.return_value = mock_session

            with pytest.raises(SlackError, match="HTTP 500"):
                await client.post_message("test")

    @pytest.mark.asyncio
    async def test_includes_blocks_when_provided(self) -> None:
        client = _make_client()
        mock_resp = _make_response()
        captured_payloads: list[dict] = []

        def _fake_post(url: str, json: dict, **kwargs: object) -> MagicMock:
            captured_payloads.append(json)
            return _ctx(mock_resp)

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = _fake_post
            mock_session_cls.return_value = mock_session

            await client.post_message("fallback", blocks=blocks)

        assert captured_payloads[0]["blocks"] == blocks


# --- SlackClient.get_reactions ---

class TestGetReactions:
    @pytest.mark.asyncio
    async def test_returns_reactions_list(self) -> None:
        client = _make_client()
        reactions = [
            {"name": APPROVE_REACTION, "users": ["U111"], "count": 1},
        ]
        mock_resp = _make_reactions_response(reactions)

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=_ctx(mock_resp))
            mock_session_cls.return_value = mock_session

            result = await client.get_reactions("1700000000.000001")

        assert result == reactions

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_reactions(self) -> None:
        client = _make_client()
        mock_resp = _make_reactions_response([])

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=_ctx(mock_resp))
            mock_session_cls.return_value = mock_session

            result = await client.get_reactions("1700000000.000001")

        assert result == []


# --- Rate limiting ---

class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_retries_after_429(self) -> None:
        client = _make_client()

        rate_limit_resp = MagicMock()
        rate_limit_resp.status = 429
        rate_limit_resp.headers = {"Retry-After": "1"}

        success_resp = _make_response()
        call_count = 0

        def _post_side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _ctx(rate_limit_resp) if call_count == 1 else _ctx(success_resp)

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = _post_side_effect
            mock_session_cls.return_value = mock_session

            with patch("errander.integrations.slack.asyncio.sleep", AsyncMock()):
                ts = await client.post_message("test")

        assert call_count == 2
        assert ts is not None

    @pytest.mark.asyncio
    async def test_raises_after_second_429(self) -> None:
        client = _make_client()

        rate_limit_resp = MagicMock()
        rate_limit_resp.status = 429
        rate_limit_resp.headers = {"Retry-After": "1"}

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(return_value=_ctx(rate_limit_resp))
            mock_session_cls.return_value = mock_session

            with (
                patch("errander.integrations.slack.asyncio.sleep", AsyncMock()),
                pytest.raises(SlackError, match="rate limit"),
            ):
                await client.post_message("test")
