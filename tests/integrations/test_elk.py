"""Tests for errander/integrations/elk.py — ElkClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.integrations.elk import ElkClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _elk_agg_response(
    buckets: list[dict],
    total_hits: int = 0,
    status: int = 200,
) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value={
        "hits": {"total": {"value": total_hits}},
        "aggregations": {
            "top_errors": {"buckets": buckets},
        },
    })
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_elk_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.closed = False
    session.post = MagicMock(return_value=response)
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_vm_errors_returns_patterns() -> None:
    buckets = [
        {"key": "Cannot connect to database", "doc_count": 47},
        {"key": "Disk write error on /var", "doc_count": 12},
    ]
    resp = _elk_agg_response(buckets, total_hits=59)
    client = ElkClient("http://elk:9200")

    with patch.object(client, "_get_session", return_value=_mock_elk_session(resp)):
        result = await client.fetch_vm_errors("web-01")

    assert len(result) == 2
    assert "47x" in result[0]
    assert "Cannot connect to database" in result[0]
    assert "[ERROR]" in result[0]


@pytest.mark.asyncio
async def test_fetch_vm_errors_empty_buckets() -> None:
    resp = _elk_agg_response([], total_hits=0)
    client = ElkClient("http://elk:9200")

    with patch.object(client, "_get_session", return_value=_mock_elk_session(resp)):
        result = await client.fetch_vm_errors("web-01")

    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_errors_fallback_no_keyword() -> None:
    """When aggregation key is missing, fall back to total hits message."""
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={
        "hits": {"total": {"value": 83}},
        # No 'aggregations' key → triggers fallback
    })
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    client = ElkClient("http://elk:9200")
    with patch.object(client, "_get_session", return_value=_mock_elk_session(resp)):
        result = await client.fetch_vm_errors("web-01")

    assert len(result) == 1
    assert "83" in result[0]
    assert "message.keyword" in result[0]


@pytest.mark.asyncio
async def test_fetch_vm_errors_http_error() -> None:
    resp = _elk_agg_response([], status=400)
    client = ElkClient("http://elk:9200")

    with patch.object(client, "_get_session", return_value=_mock_elk_session(resp)):
        result = await client.fetch_vm_errors("web-01")

    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_errors_unreachable() -> None:
    import aiohttp

    session = MagicMock()
    session.closed = False
    session.post = MagicMock(side_effect=aiohttp.ClientConnectorError(
        connection_key=MagicMock(), os_error=OSError("refused")
    ))
    client = ElkClient("http://elk:9200")

    with patch.object(client, "_get_session", return_value=session):
        result = await client.fetch_vm_errors("web-01")

    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_errors_timeout() -> None:
    import asyncio

    session = MagicMock()
    session.closed = False
    session.post = MagicMock(side_effect=asyncio.TimeoutError())
    client = ElkClient("http://elk:9200")

    with patch.object(client, "_get_session", return_value=session):
        result = await client.fetch_vm_errors("web-01")

    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_errors_malformed_json() -> None:
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(side_effect=ValueError("JSON decode error"))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    client = ElkClient("http://elk:9200")
    with patch.object(client, "_get_session", return_value=_mock_elk_session(resp)):
        result = await client.fetch_vm_errors("web-01")

    assert result == []


@pytest.mark.asyncio
async def test_elk_client_with_api_key() -> None:
    """When api_key is set, Authorization header must be included."""
    import aiohttp

    captured_headers: dict[str, str] = {}

    original_init = aiohttp.ClientSession.__init__

    def _mock_init(self: aiohttp.ClientSession, *args: object, **kwargs: object) -> None:
        nonlocal captured_headers
        captured_headers = dict(kwargs.get("headers") or {})
        original_init(self, *args, **kwargs)

    client = ElkClient("http://elk:9200", api_key="my-api-key")
    with patch.object(aiohttp, "ClientSession") as mock_cls:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_cls.return_value = mock_session
        client._get_session()
        call_kwargs = mock_cls.call_args.kwargs
        headers = call_kwargs.get("headers", {})

    assert "Authorization" in headers
    assert "my-api-key" in headers["Authorization"]


@pytest.mark.asyncio
async def test_elk_client_no_api_key() -> None:
    """When api_key is empty, no Authorization header."""
    import aiohttp

    client = ElkClient("http://elk:9200", api_key="")
    with patch.object(aiohttp, "ClientSession") as mock_cls:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_cls.return_value = mock_session
        client._get_session()
        call_kwargs = mock_cls.call_args.kwargs
        headers = call_kwargs.get("headers", {})

    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_elk_client_close() -> None:
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()

    client = ElkClient("http://elk:9200")
    client._session = session  # inject

    await client.close()

    session.close.assert_awaited_once()
    assert client._session is None


@pytest.mark.asyncio
async def test_operator_assistant_with_elk() -> None:
    """_build_context() must call elk_client.fetch_vm_errors per VM."""
    from unittest.mock import AsyncMock, MagicMock

    from errander.agent.operator_assistant import OperatorAssistant

    elk_client = MagicMock()
    elk_client.fetch_vm_errors = AsyncMock(return_value=["[ERROR] 5x connection refused"])

    audit_store = MagicMock()
    audit_store.get_recent_batches = AsyncMock(return_value=[])
    audit_store.get_events = AsyncMock(return_value=[])

    target = MagicMock()
    target.name = "web-01"
    target.host = "10.0.0.1"

    env = MagicMock()
    env.targets = [target]

    inventory = MagicMock()
    inventory.environments = {"dev": env}

    assistant = OperatorAssistant()
    context = await assistant._build_context(
        audit_store=audit_store,
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inventory,
        env_name="dev",
        elk_client=elk_client,
    )

    elk_client.fetch_vm_errors.assert_awaited_once_with("10.0.0.1")
    assert context.vm_summaries[0].elk_errors == ["[ERROR] 5x connection refused"]


@pytest.mark.asyncio
async def test_operator_assistant_elk_none() -> None:
    """elk_client=None → no errors raised, summary.elk_errors empty."""
    from errander.agent.operator_assistant import OperatorAssistant

    audit_store = MagicMock()
    audit_store.get_recent_batches = AsyncMock(return_value=[])
    audit_store.get_events = AsyncMock(return_value=[])

    target = MagicMock()
    target.name = "web-01"
    target.host = "10.0.0.1"

    env = MagicMock()
    env.targets = [target]

    inventory = MagicMock()
    inventory.environments = {"dev": env}

    assistant = OperatorAssistant()
    context = await assistant._build_context(
        audit_store=audit_store,
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inventory,
        env_name="dev",
        elk_client=None,
    )

    assert context.vm_summaries[0].elk_errors == []
