"""Tests for errander/integrations/prometheus.py — PrometheusClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.integrations.prometheus import PrometheusClient


def _make_prom_response(value: str | None, status: int = 200) -> MagicMock:
    """Build a mock aiohttp response for a Prometheus instant query."""
    resp = MagicMock()
    resp.status = status
    if value is not None:
        resp.json = AsyncMock(return_value={
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {"instance": "10.0.0.1:9100"}, "value": [1700000000, value]}],
            },
        })
    else:
        resp.json = AsyncMock(return_value={
            "status": "success",
            "data": {"resultType": "vector", "result": []},
        })
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(responses: list[MagicMock]) -> MagicMock:
    """Mock aiohttp session with a sequence of GET responses."""
    session = MagicMock()
    session.closed = False
    call_count = 0

    def _get(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        resp = responses[call_count % len(responses)]
        call_count += 1
        return resp

    session.get = _get
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# fetch_vm_metrics — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_vm_metrics_returns_three_formatted_strings() -> None:
    client = PrometheusClient("http://prometheus:9090")
    responses = [
        _make_prom_response("72.3"),   # CPU
        _make_prom_response("84.1"),   # Memory
        _make_prom_response("2.4"),    # Load
    ]
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session(responses))):
        result = await client.fetch_vm_metrics("10.0.0.1")

    assert len(result) == 3
    assert result[0] == "CPU (5m): 72.3%"
    assert result[1] == "Memory: 84.1%"
    assert result[2] == "Load(5m): 2.40"


@pytest.mark.asyncio
async def test_fetch_vm_metrics_partial_success_returns_available() -> None:
    """When 1 of 3 queries returns no data, only 2 strings returned."""
    client = PrometheusClient("http://prometheus:9090")
    responses = [
        _make_prom_response("65.0"),   # CPU ok
        _make_prom_response(None),     # Memory empty → skip
        _make_prom_response("1.5"),    # Load ok
    ]
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session(responses))):
        result = await client.fetch_vm_metrics("10.0.0.1")

    assert len(result) == 2
    assert "CPU" in result[0]
    assert "Load" in result[1]


# ---------------------------------------------------------------------------
# fetch_vm_metrics — failure paths (must return [], never raise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_vm_metrics_empty_when_all_results_empty() -> None:
    client = PrometheusClient("http://prometheus:9090")
    empty = _make_prom_response(None)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([empty]))):
        result = await client.fetch_vm_metrics("10.0.0.1")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_metrics_empty_on_non_200() -> None:
    client = PrometheusClient("http://prometheus:9090")
    not_found = _make_prom_response(None, status=404)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([not_found]))):
        result = await client.fetch_vm_metrics("10.0.0.1")
    assert result == []


# ---------------------------------------------------------------------------
# query() — arbitrary read-only PromQL (investigation-agent tool path)
# ---------------------------------------------------------------------------


def _make_raw_response(rows: list[dict], status: int = 200) -> MagicMock:
    """Build a mock aiohttp response with raw Prometheus result rows."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value={
        "status": "success",
        "data": {"resultType": "vector", "result": rows},
    })
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_query_returns_formatted_rows() -> None:
    client = PrometheusClient("http://prometheus:9090")
    rows = [{"metric": {"instance": "10.0.0.1:9100"}, "value": [1700000000, "42"]}]
    resp = _make_raw_response(rows)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([resp]))):
        result = await client.query("node_load5")
    assert len(result) == 1
    assert "42" in result[0]


@pytest.mark.asyncio
async def test_query_uses_instant_endpoint_by_default() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = _make_raw_response([])
    captured_urls: list[str] = []

    session = MagicMock()
    session.closed = False

    def _get(url: str, **kwargs: object) -> MagicMock:
        captured_urls.append(url)
        return resp

    session.get = _get
    with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
        await client.query("up")
    assert captured_urls[0].endswith("/api/v1/query")


@pytest.mark.asyncio
async def test_query_uses_range_endpoint_when_range_seconds_given() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = _make_raw_response([])
    captured_urls: list[str] = []

    session = MagicMock()
    session.closed = False

    def _get(url: str, **kwargs: object) -> MagicMock:
        captured_urls.append(url)
        return resp

    session.get = _get
    with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
        await client.query("up", range_seconds=3600)
    assert captured_urls[0].endswith("/api/v1/query_range")


@pytest.mark.asyncio
async def test_query_caps_returned_rows() -> None:
    from errander.integrations.prometheus import _MAX_QUERY_ROWS

    client = PrometheusClient("http://prometheus:9090")
    rows = [
        {"metric": {"instance": f"10.0.0.{i}:9100"}, "value": [1700000000, str(i)]}
        for i in range(_MAX_QUERY_ROWS * 3)
    ]
    resp = _make_raw_response(rows)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([resp]))):
        result = await client.query("up")
    assert len(result) == _MAX_QUERY_ROWS


@pytest.mark.asyncio
async def test_query_empty_list_on_non_200() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = _make_raw_response([], status=500)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([resp]))):
        result = await client.query("up")
    assert result == []


@pytest.mark.asyncio
async def test_query_never_raises_on_session_exception() -> None:
    client = PrometheusClient("http://prometheus:9090")
    with patch.object(client, "_get_session", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await client.query("up")
    assert result == []
    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_metrics_empty_on_connection_error() -> None:
    client = PrometheusClient("http://prometheus:9090")
    session = MagicMock()
    session.closed = False

    def _raise(*args: object, **kwargs: object) -> None:
        raise ConnectionError("connection refused")

    session.get = _raise
    with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
        result = await client.fetch_vm_metrics("10.0.0.1")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_vm_metrics_empty_on_json_error() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(side_effect=ValueError("not json"))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = _mock_session([resp])
    with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
        result = await client.fetch_vm_metrics("10.0.0.1")
    assert result == []


# ---------------------------------------------------------------------------
# _query_instant — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_instant_parses_float_value() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = _make_prom_response("72.3")
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([resp]))):
        value = await client._query_instant("some_metric{}")
    assert value == pytest.approx(72.3)


@pytest.mark.asyncio
async def test_query_instant_returns_none_on_empty_result() -> None:
    client = PrometheusClient("http://prometheus:9090")
    resp = _make_prom_response(None)
    with patch.object(client, "_get_session", new=AsyncMock(return_value=_mock_session([resp]))):
        value = await client._query_instant("some_metric{}")
    assert value is None


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_session() -> None:
    client = PrometheusClient("http://prometheus:9090")
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    client._session = mock_session

    await client.close()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_noop_when_no_session() -> None:
    client = PrometheusClient("http://prometheus:9090")
    await client.close()  # should not raise
