"""Phase E Commit 4 tests: data source transparency in OperatorAssistant."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.operator_assistant import OperatorAssistant, _fallback_response
from errander.models.analysis import FleetContext, VMSignalSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audit_store(
    batches: list[dict[str, object]] | None = None,
    events: list[object] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.get_recent_batches = AsyncMock(return_value=batches or [])
    store.get_events = AsyncMock(return_value=events or [])
    return store


def _make_inventory(targets: list[str] | None = None) -> MagicMock:
    inv = MagicMock()
    if targets:
        tgt_mocks = []
        for i, t in enumerate(targets):
            tgt = MagicMock()
            tgt.name = t
            tgt.host = f"10.0.0.{i}"
            tgt_mocks.append(tgt)
        env = MagicMock(targets=tgt_mocks)
        inv.environments = {"dev": env}
    else:
        inv.environments = {}
    return inv


def _empty_context() -> FleetContext:
    return FleetContext(
        env_name="dev",
        vm_summaries=[],
        recent_batch_count=0,
        last_batch_at=None,
        total_failures_7d=0,
    )


# ---------------------------------------------------------------------------
# _build_context sources_used tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_always_has_audit_store() -> None:
    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "anything",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=_make_inventory(),
        env_name="dev",
    )
    assert "audit_store" in result.data_sources


@pytest.mark.asyncio
async def test_sources_includes_prometheus_when_data_present() -> None:
    prom = MagicMock()
    prom._base_url = "http://prom:9090"
    prom.fetch_vm_metrics = AsyncMock(return_value=["CPU: 80%"])
    inv = _make_inventory(["web-01"])

    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "status?",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inv,
        env_name="dev",
        prometheus_client=prom,
    )
    assert any("prometheus" in s and "prom:9090" in s for s in result.data_sources)


@pytest.mark.asyncio
async def test_sources_includes_prometheus_no_data_label() -> None:
    prom = MagicMock()
    prom._base_url = "http://prom:9090"
    prom.fetch_vm_metrics = AsyncMock(return_value=[])  # empty → no data
    inv = _make_inventory(["web-01"])

    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "status?",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inv,
        env_name="dev",
        prometheus_client=prom,
    )
    assert "prometheus(no_data)" in result.data_sources


@pytest.mark.asyncio
async def test_sources_includes_elk_when_data_present() -> None:
    elk = MagicMock()
    elk._base_url = "http://es:9200"
    elk.fetch_vm_errors = AsyncMock(return_value=["[ERROR] 5x db timeout"])
    elk.close = AsyncMock()
    inv = _make_inventory(["web-01"])

    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "errors?",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inv,
        env_name="dev",
        elk_client=elk,
    )
    assert any("elk" in s and "es:9200" in s for s in result.data_sources)


@pytest.mark.asyncio
async def test_sources_includes_live_probe_when_journal_present() -> None:
    """If VMSignalSummary has journal_errors, sources should include live_ssh_probe."""
    # We test _fallback_response directly with a FleetContext that has journal_errors
    summary = VMSignalSummary(vm_id="web-01", hostname="10.0.0.1")
    summary.journal_errors = ["Cannot connect to db"]
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[summary],
        recent_batch_count=0,
        last_batch_at=None,
        total_failures_7d=0,
        sources_used=["audit_store", "live_ssh_probe"],
    )
    response = _fallback_response("status?", ctx)
    assert "live_ssh_probe" in response.data_sources


@pytest.mark.asyncio
async def test_sources_no_external_tools_baseline() -> None:
    """With no prometheus/elk clients, sources_used has only audit_store."""
    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "baseline?",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=_make_inventory(),
        env_name="dev",
    )
    assert result.data_sources == ["audit_store"]


def test_fallback_response_includes_sources() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[],
        recent_batch_count=0,
        last_batch_at=None,
        total_failures_7d=0,
        sources_used=["audit_store", "prometheus(no_data)"],
    )
    response = _fallback_response("what's up?", ctx)
    assert "audit_store" in response.data_sources
    assert "prometheus(no_data)" in response.data_sources


@pytest.mark.asyncio
async def test_llm_response_sources_carried_forward() -> None:
    """When LLM returns a response, sources_used from context should be on data_sources."""
    from errander.models.analysis import AssistantResponse

    llm = MagicMock()
    llm.complete = AsyncMock(return_value=AssistantResponse(
        summary="All good",
        findings=["No issues"],
        recommendations=["Keep watching"],
        risk_level="low",
    ))
    inv = _make_inventory(["web-01"])

    prom = MagicMock()
    prom._base_url = "http://prom:9090"
    prom.fetch_vm_metrics = AsyncMock(return_value=["CPU: 10%"])

    assistant = OperatorAssistant()
    result = await assistant.investigate(
        "status?",
        audit_store=_make_audit_store(),
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inv,
        env_name="dev",
        llm_client=llm,
        prometheus_client=prom,
    )
    # LLM response should have sources from context carried forward
    assert "audit_store" in result.data_sources
    assert any("prometheus" in s for s in result.data_sources)
