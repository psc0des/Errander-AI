"""Tests for errander/agent/investigation_agent.py — the read-only tool handlers.

Each tool: read-only (never calls anything but an existing read method),
arg validation rejects malformed/injection-shaped input, and size/limit
caps are enforced regardless of what the LLM requests. Redaction and the
embedded source_id are applied in the agentic loop, not inside individual
tool handlers — see test_investigation_agent.py for loop-level coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.investigation_agent import (
    _MAX_AUDIT_EVENTS_LIMIT,
    _MAX_DISK_WINDOW_DAYS,
    _tool_get_audit_events,
    _tool_get_disk_trend,
    _tool_get_vm_facts,
    _tool_list_inventory,
    _tool_query_prometheus,
    _tool_search_logs,
    _ToolContext,
)
from errander.models.events import EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inventory(env_names: list[str], vms_per_env: int = 1) -> MagicMock:
    inv = MagicMock()
    envs = {}
    for env in env_names:
        env_mock = MagicMock()
        targets = []
        for i in range(vms_per_env):
            # `name=` is a reserved MagicMock constructor kwarg (sets repr,
            # not the .name attribute) — must be assigned after construction.
            target = MagicMock(host=f"10.0.{i}.1")
            target.name = f"{env}-vm-{i}"
            targets.append(target)
        env_mock.targets = targets
        envs[env] = env_mock
    inv.environments = envs
    return inv


def _make_ctx(
    *,
    inventory: MagicMock | None = None,
    env_name: str | None = None,
    prometheus_client: MagicMock | None = None,
    elk_client: MagicMock | None = None,
    audit_store: MagicMock | None = None,
    disk_history_store: MagicMock | None = None,
    vm_facts_store: MagicMock | None = None,
) -> _ToolContext:
    return _ToolContext(
        audit_store=audit_store or MagicMock(),
        disk_history_store=disk_history_store or MagicMock(),
        inventory=inventory or _make_inventory(["dev"]),
        env_name=env_name,
        prometheus_client=prometheus_client,
        elk_client=elk_client,
        vm_facts_store=vm_facts_store,
    )


# ---------------------------------------------------------------------------
# query_prometheus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_prometheus_not_configured() -> None:
    ctx = _make_ctx(prometheus_client=None)
    result = await _tool_query_prometheus({"promql": "up"}, ctx)
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_query_prometheus_calls_client_with_validated_query() -> None:
    prom = MagicMock()
    prom.query = AsyncMock(return_value=["instance=10.0.0.1: 42"])
    ctx = _make_ctx(prometheus_client=prom)

    result = await _tool_query_prometheus({"promql": "node_load5"}, ctx)

    prom.query.assert_awaited_once_with("node_load5", range_seconds=None)
    assert "42" in result


@pytest.mark.asyncio
async def test_query_prometheus_no_data_message() -> None:
    prom = MagicMock()
    prom.query = AsyncMock(return_value=[])
    ctx = _make_ctx(prometheus_client=prom)

    result = await _tool_query_prometheus({"promql": "up"}, ctx)
    assert "no data" in result.lower()
    prom.query.assert_awaited_once()


@pytest.mark.parametrize(
    "bad_promql",
    [
        "http://evil.example/api/v1/admin",
        "/api/v1/admin/shutdown",
        "https://attacker.example/",
    ],
)
@pytest.mark.asyncio
async def test_query_prometheus_rejects_path_like_input(bad_promql: str) -> None:
    prom = MagicMock()
    prom.query = AsyncMock(return_value=["should never be called"])
    ctx = _make_ctx(prometheus_client=prom)

    result = await _tool_query_prometheus({"promql": bad_promql}, ctx)

    assert "error" in result.lower()
    prom.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_prometheus_rejects_empty_query() -> None:
    prom = MagicMock()
    prom.query = AsyncMock()
    ctx = _make_ctx(prometheus_client=prom)

    result = await _tool_query_prometheus({"promql": ""}, ctx)

    assert "error" in result.lower()
    prom.query.assert_not_awaited()


# ---------------------------------------------------------------------------
# search_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_logs_not_configured() -> None:
    ctx = _make_ctx(elk_client=None)
    result = await _tool_search_logs({"host": "10.0.0.1", "query_terms": ["error"]}, ctx)
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_search_logs_rejects_unknown_host() -> None:
    elk = MagicMock()
    elk.search = AsyncMock(return_value=["should never be called"])
    ctx = _make_ctx(elk_client=elk, inventory=_make_inventory(["dev"]))

    result = await _tool_search_logs(
        {"host": "totally-unknown-host", "query_terms": ["error"]}, ctx,
    )

    assert "not a known fleet target" in result.lower()
    elk.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_logs_accepts_known_host() -> None:
    elk = MagicMock()
    elk.search = AsyncMock(return_value=["[2026-06-22T00:00:00Z] connection refused"])
    ctx = _make_ctx(elk_client=elk, inventory=_make_inventory(["dev"]))

    result = await _tool_search_logs(
        {"host": "10.0.0.1", "query_terms": ["connection"]}, ctx,
    )

    elk.search.assert_awaited_once()
    assert "connection refused" in result


# ---------------------------------------------------------------------------
# get_audit_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_events_caps_limit_regardless_of_request() -> None:
    audit = MagicMock()
    audit.get_events = AsyncMock(return_value=[])
    ctx = _make_ctx(audit_store=audit)

    await _tool_get_audit_events({"limit": _MAX_AUDIT_EVENTS_LIMIT * 10}, ctx)

    _, kwargs = audit.get_events.call_args
    assert kwargs["limit"] == _MAX_AUDIT_EVENTS_LIMIT


@pytest.mark.asyncio
async def test_get_audit_events_rejects_invalid_event_type() -> None:
    audit = MagicMock()
    audit.get_events = AsyncMock(return_value=[])
    ctx = _make_ctx(audit_store=audit)

    result = await _tool_get_audit_events({"event_type": "not_a_real_event_type"}, ctx)

    assert "error" in result.lower()
    audit.get_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_audit_events_accepts_valid_event_type() -> None:
    audit = MagicMock()
    audit.get_events = AsyncMock(return_value=[])
    ctx = _make_ctx(audit_store=audit)

    await _tool_get_audit_events({"event_type": EventType.ACTION_FAILED.value}, ctx)

    _, kwargs = audit.get_events.call_args
    assert kwargs["event_type"] == EventType.ACTION_FAILED


@pytest.mark.asyncio
async def test_get_audit_events_no_results_message() -> None:
    audit = MagicMock()
    audit.get_events = AsyncMock(return_value=[])
    ctx = _make_ctx(audit_store=audit)

    result = await _tool_get_audit_events({}, ctx)
    assert "no matching" in result.lower()


# ---------------------------------------------------------------------------
# get_disk_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_disk_trend_requires_vm_id() -> None:
    ctx = _make_ctx()
    result = await _tool_get_disk_trend({"vm_id": ""}, ctx)
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_get_disk_trend_caps_window_days() -> None:
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=["/"])
    disk.get_window = AsyncMock(return_value=[])
    ctx = _make_ctx(disk_history_store=disk)

    await _tool_get_disk_trend({"vm_id": "v1", "window_days": 9999}, ctx)

    args, _ = disk.get_window.call_args
    assert args[2] == _MAX_DISK_WINDOW_DAYS


@pytest.mark.asyncio
async def test_get_disk_trend_no_history_message() -> None:
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=[])
    ctx = _make_ctx(disk_history_store=disk)

    result = await _tool_get_disk_trend({"vm_id": "v1"}, ctx)
    assert "no disk history" in result.lower()


@pytest.mark.asyncio
async def test_get_disk_trend_insufficient_history_per_mountpoint() -> None:
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=["/"])
    disk.get_window = AsyncMock(return_value=[MagicMock(used_pct=50.0)])  # only 1 point
    ctx = _make_ctx(disk_history_store=disk)

    result = await _tool_get_disk_trend({"vm_id": "v1"}, ctx)
    assert "insufficient history" in result.lower()


@pytest.mark.asyncio
async def test_get_disk_trend_reports_delta() -> None:
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=["/"])
    disk.get_window = AsyncMock(return_value=[
        MagicMock(used_pct=50.0), MagicMock(used_pct=80.0),
    ])
    ctx = _make_ctx(disk_history_store=disk)

    result = await _tool_get_disk_trend({"vm_id": "v1", "window_days": 7}, ctx)
    assert "50%" in result and "80%" in result


# ---------------------------------------------------------------------------
# get_vm_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vm_facts_not_available() -> None:
    ctx = _make_ctx(vm_facts_store=None)
    result = await _tool_get_vm_facts({"vm_id": "v1"}, ctx)
    assert "not available" in result.lower()


@pytest.mark.asyncio
async def test_get_vm_facts_requires_vm_id() -> None:
    facts = MagicMock()
    ctx = _make_ctx(vm_facts_store=facts)
    result = await _tool_get_vm_facts({"vm_id": ""}, ctx)
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_get_vm_facts_reports_outcomes_and_reboot_pattern() -> None:
    facts = MagicMock()
    outcome = MagicMock(action_type="patching", success_rate=0.9, sample_size=10, last_failure_reason=None)
    facts.action_outcomes = AsyncMock(return_value=[outcome])
    facts.reboot_pattern = AsyncMock(
        return_value=MagicMock(reboots_required_after_patching=3, sample_size=10),
    )
    ctx = _make_ctx(vm_facts_store=facts)

    result = await _tool_get_vm_facts({"vm_id": "v1"}, ctx)
    assert "patching" in result and "90%" in result
    assert "3" in result


# ---------------------------------------------------------------------------
# list_inventory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_inventory_returns_hostnames_only_no_credentials() -> None:
    inv = _make_inventory(["dev", "prod"], vms_per_env=2)
    ctx = _make_ctx(inventory=inv)

    result = await _tool_list_inventory({}, ctx)

    assert "dev" in result and "prod" in result
    # Must never leak ssh_user/ssh_key_path even if present on the mock target.
    assert "ssh_user" not in result.lower()
    assert "ssh_key" not in result.lower()


@pytest.mark.asyncio
async def test_list_inventory_filters_by_env() -> None:
    inv = _make_inventory(["dev", "prod"])
    ctx = _make_ctx(inventory=inv)

    result = await _tool_list_inventory({"env": "dev"}, ctx)

    assert "dev" in result
    assert "prod" not in result
