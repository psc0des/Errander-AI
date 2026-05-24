"""Tests for PrometheusClient integration in OperatorAssistant."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.operator_assistant import OperatorAssistant, _format_prompt
from errander.models.analysis import FleetContext, VMSignalSummary


def _make_inventory(vms: list[tuple[str, str]]) -> MagicMock:
    """Build fake InventoryConfig with (vm_id, host) pairs in 'dev' env."""
    inv = MagicMock()
    env_mock = MagicMock()
    env_mock.targets = [
        MagicMock(name=vm_id, host=host)
        for vm_id, host in vms
    ]
    inv.environments = {"dev": env_mock}
    return inv


def _make_audit_store() -> MagicMock:
    store = MagicMock()
    store.get_events = AsyncMock(return_value=[])
    store.get_recent_batches = AsyncMock(return_value=[])
    return store


def _empty_stores() -> tuple[MagicMock, MagicMock]:
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=[])
    disk.get_window = AsyncMock(return_value=[])
    base = MagicMock()
    base.latest = AsyncMock(return_value=None)
    return disk, base


def _make_prom_client(metrics: list[str]) -> MagicMock:
    client = MagicMock()
    client.fetch_vm_metrics = AsyncMock(return_value=metrics)
    return client


# ---------------------------------------------------------------------------
# _build_context + PrometheusClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_fetches_prometheus_metrics() -> None:
    prom = _make_prom_client(["CPU (5m): 65.0%", "Memory: 70.0%"])
    inv = _make_inventory([("v1", "10.0.0.1")])
    disk, base = _empty_stores()

    ctx = await OperatorAssistant()._build_context(
        audit_store=_make_audit_store(),
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
        prometheus_client=prom,
    )

    assert ctx.vm_summaries[0].prometheus_metrics == ["CPU (5m): 65.0%", "Memory: 70.0%"]
    prom.fetch_vm_metrics.assert_awaited_once_with("10.0.0.1")


@pytest.mark.asyncio
async def test_build_context_skips_prometheus_when_client_none() -> None:
    inv = _make_inventory([("v1", "10.0.0.1")])
    disk, base = _empty_stores()

    ctx = await OperatorAssistant()._build_context(
        audit_store=_make_audit_store(),
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
        prometheus_client=None,
    )

    assert ctx.vm_summaries[0].prometheus_metrics == []


@pytest.mark.asyncio
async def test_build_context_queries_prometheus_for_each_vm() -> None:
    prom = _make_prom_client(["Load(5m): 1.2"])
    inv = _make_inventory([("v1", "10.0.0.1"), ("v2", "10.0.0.2")])
    disk, base = _empty_stores()

    ctx = await OperatorAssistant()._build_context(
        audit_store=_make_audit_store(),
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
        prometheus_client=prom,
    )

    assert prom.fetch_vm_metrics.await_count == 2
    assert all(v.prometheus_metrics == ["Load(5m): 1.2"] for v in ctx.vm_summaries)


# ---------------------------------------------------------------------------
# investigate() threads prometheus_client through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigate_passes_prometheus_client_to_context() -> None:
    prom = _make_prom_client(["CPU (5m): 88.0%"])
    inv = _make_inventory([("v1", "10.0.0.1")])
    disk, base = _empty_stores()

    from errander.models.analysis import AssistantResponse
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=AssistantResponse(
        summary="High CPU.", findings=["v1 at 88%"],
        recommendations=["Investigate"], risk_level="medium",
    ))

    await OperatorAssistant().investigate(
        "Any CPU pressure?",
        audit_store=_make_audit_store(),
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
        llm_client=llm,
        prometheus_client=prom,
    )

    # Prometheus was queried
    prom.fetch_vm_metrics.assert_awaited_once()
    # LLM was called with a prompt containing the metrics
    prompt_used = llm.complete.await_args.args[0]
    assert "CPU (5m): 88.0%" in prompt_used


# ---------------------------------------------------------------------------
# _format_prompt includes prometheus_metrics
# ---------------------------------------------------------------------------


def test_format_prompt_includes_prometheus_metrics() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(
                vm_id="v1", hostname="h1",
                prometheus_metrics=["CPU (5m): 92.1%", "Memory: 78.0%"],
            )
        ],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=0,
    )
    prompt = _format_prompt("Is CPU high?", ctx)
    assert "CPU (5m): 92.1%" in prompt
    assert "Memory: 78.0%" in prompt


def test_format_prompt_no_prometheus_line_when_empty() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[VMSignalSummary(vm_id="v1", hostname="h1")],
        recent_batch_count=0,
        last_batch_at=None,
        total_failures_7d=0,
    )
    prompt = _format_prompt("q", ctx)
    assert "Prometheus:" not in prompt
