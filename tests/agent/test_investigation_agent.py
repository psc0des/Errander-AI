"""Tests for errander/agent/investigation_agent.py — the agentic ReAct loop.

Drives InvestigationAgent.investigate_agentic() with a fake tool-calling LLM
stub. Covers: multi-hop success + citation handling (the model can only cite
a source_id it actually saw — review delta #1), the tool-call budget cap
forcing a final, tools-less answer (delta #5), turn-1 zero-tool-calls
capability detection (delta #4), LLM-unreachable/unsupported fallback,
malicious/oversized tool-result redaction+capping, per-hop audit logging
the delta only (delta #3), and the fallback metric (delta #8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.investigation_agent import InvestigationAgent
from errander.integrations.llm import LLMClient, ToolCallRequest, ToolCallResult
from errander.models.analysis import AssistantResponse
from errander.observability.metrics import INVESTIGATION_FALLBACK_TOTAL
from tests.agent.test_operator_assistant import _empty_stores, _make_audit_store, _make_inventory

# ---------------------------------------------------------------------------
# Fake LLM client — scripted complete_with_tools() responses, one per hop
# ---------------------------------------------------------------------------


class _FakeLLMClient:
    """complete() defaults to returning None so that, when the agentic loop
    falls back to OperatorAssistant.investigate(), that path's own
    LLM-unavailable handling produces its deterministic _fallback_response()
    — making "did we actually fall back" observable via response.summary.

    Duck-types LLMClient rather than subclassing it — cast to LLMClient at
    each call site (mirrors how existing operator_assistant tests pass a
    plain MagicMock() for the same parameter)."""

    def __init__(self, responses: list[ToolCallResult | None]) -> None:
        self._responses = list(responses)
        self._model = "test-model"
        self._base_url = "http://test-llm"
        self._temperature = 0.1
        self.calls: list[dict[str, Any]] = []
        self.complete = AsyncMock(return_value=None)

    async def complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        *, timeout_seconds: int | None = None,
    ) -> ToolCallResult | None:
        self.calls.append({
            "messages": list(messages), "tools": list(tools), "timeout_seconds": timeout_seconds,
        })
        if not self._responses:
            return None
        return self._responses.pop(0)


def _as_llm(llm: _FakeLLMClient | None) -> LLMClient | None:
    return cast("LLMClient | None", llm)


def _tool_call(call_id: str, name: str, **args: object) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=name, arguments_json=json.dumps(args))


def _tool_request(*calls: ToolCallRequest) -> ToolCallResult:
    return ToolCallResult(content=None, tool_calls=list(calls))


def _final(payload: Mapping[str, object]) -> ToolCallResult:
    return ToolCallResult(content=json.dumps(payload), tool_calls=[])


def _make_ai_store() -> MagicMock:
    store = MagicMock()
    store.log = AsyncMock()
    return store


async def _run(
    llm: _FakeLLMClient | None,
    *,
    ai_decision_store: MagicMock | None = None,
    max_tool_calls: int = 8,
    timeout_seconds: int = 180,
    question: str = "why did things fail?",
) -> AssistantResponse:
    audit = _make_audit_store()
    disk, baseline = _empty_stores()
    inventory = _make_inventory(["dev"])
    agent = InvestigationAgent()
    return await agent.investigate_agentic(
        question,
        audit_store=audit, disk_history_store=disk, baseline_store=baseline,
        inventory=inventory, llm_client=_as_llm(llm), ai_decision_store=ai_decision_store,
        max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds,
    )


_EMPTY_FINAL: dict[str, object] = {"summary": "done", "findings": [], "recommendations": [], "risk_level": "low"}


# ---------------------------------------------------------------------------
# Multi-hop success + citation handling (delta #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multihop_success_keeps_real_citation_strips_hallucinated() -> None:
    ai_store = _make_ai_store()
    final_payload = {
        "summary": "Investigated audit events.",
        "findings": [
            {"text": "Real finding", "evidence": ["get_audit_events#1"]},
            {"text": "Hallucinated finding", "evidence": ["made_up_source#99"]},
        ],
        "recommendations": ["Review further"],
        "risk_level": "low",
    }
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _final(final_payload),
    ])

    response = await _run(llm, ai_decision_store=ai_store)

    assert response.findings[0].evidence == ["get_audit_events#1"]
    assert response.findings[1].evidence == [], "hallucinated source must be stripped"
    assert response.data_sources == ["get_audit_events#1"]


@pytest.mark.asyncio
async def test_citation_source_id_is_embedded_in_tool_result_message() -> None:
    """The model can only cite a source_id it actually saw — confirm the
    loop embeds it in the tool message content, not just tracks it internally."""
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _final(_EMPTY_FINAL),
    ])

    await _run(llm)

    second_call_messages = llm.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"].startswith("[source_id=get_audit_events#1]")


# ---------------------------------------------------------------------------
# Per-hop audit logging — delta only, not cumulative (delta #3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_hop_audit_logs_delta_not_cumulative_transcript() -> None:
    ai_store = _make_ai_store()
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _tool_request(_tool_call("call_2", "list_inventory")),
        _final(_EMPTY_FINAL),
    ])

    await _run(llm, ai_decision_store=ai_store)

    assert ai_store.log.await_count == 2, "one audit row per tool call dispatched"
    rows = [call.args[0] for call in ai_store.log.await_args_list]
    for row in rows:
        # The delta is a small structured record (hop/tool/arguments/source_id) —
        # never the growing message history. A cumulative log would blow up
        # in size hop-over-hop; this one stays flat and small regardless of
        # how many hops preceded it.
        delta = json.loads(row.prompt_full)
        assert set(delta.keys()) == {"hop", "tool", "arguments", "source_id"}
        assert len(row.prompt_full) < 300
    assert rows[0].decision_type == "investigation_agent_step"


# ---------------------------------------------------------------------------
# Tool-call budget cap forces a final, tools-less answer (delta #5 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tool_calls_cap_forces_empty_tools_final_answer() -> None:
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _final({"summary": "forced final answer", "findings": [], "recommendations": [], "risk_level": "low"}),
    ])

    response = await _run(llm, max_tool_calls=1)

    assert response.summary == "forced final answer"
    # The second call must have been offered NO tools — proves call_tools
    # (not "call_tools or tools") is what's actually passed once the budget
    # is spent, otherwise this would silently keep offering all tools.
    assert llm.calls[1]["tools"] == []
    # A "budget exhausted" nudge must accompany the forced call.
    last_user_msg = [m for m in llm.calls[1]["messages"] if m.get("role") == "user"][-1]
    assert "budget" in last_user_msg["content"].lower()


@pytest.mark.asyncio
async def test_budget_exhausted_with_unparseable_final_falls_back() -> None:
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        ToolCallResult(content="not valid json", tool_calls=[]),
    ])

    response = await _run(llm, max_tool_calls=1)

    assert "LLM unavailable" in response.summary, "must fall back to the deterministic path"


# ---------------------------------------------------------------------------
# Wall-clock timeout cap (delta #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_exhausted_before_first_hop_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two real readings (loop start, first deadline check), then a stable
    # "far in the future" value for any later monotonic() calls made by the
    # deterministic fallback path's own latency timing.
    times = iter([0.0, 1000.0])
    monkeypatch.setattr(
        "errander.agent.investigation_agent.time.monotonic", lambda: next(times, 1000.0),
    )
    llm = _FakeLLMClient([_final(_EMPTY_FINAL)])

    response = await _run(llm, timeout_seconds=1)

    assert llm.calls == [], "must never call the LLM once the deadline has already passed"
    assert "LLM unavailable" in response.summary


# ---------------------------------------------------------------------------
# Capability detection — turn-1 zero-tool-calls (delta #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_endpoint_on_first_hop_falls_back() -> None:
    """complete_with_tools() returning None on hop 0 is the APIStatusError
    capability-detection signal."""
    llm = _FakeLLMClient([None])
    before = INVESTIGATION_FALLBACK_TOTAL.labels(reason="unsupported")._value.get()

    response = await _run(llm)

    after = INVESTIGATION_FALLBACK_TOTAL.labels(reason="unsupported")._value.get()
    assert after == before + 1
    assert "LLM unavailable" in response.summary


@pytest.mark.asyncio
async def test_empty_turn1_falls_back_even_with_a_parseable_answer() -> None:
    """A parseable-but-tool-free first answer is still treated as a
    capability failure (the endpoint likely ignored tools=), not accepted
    as a lucky shortcut — this is the regression test for delta #4."""
    llm = _FakeLLMClient([_final({
        "summary": "answered without using any tools",
        "findings": [], "recommendations": [], "risk_level": "low",
    })])
    before = INVESTIGATION_FALLBACK_TOTAL.labels(reason="empty_turn1")._value.get()

    response = await _run(llm)

    after = INVESTIGATION_FALLBACK_TOTAL.labels(reason="empty_turn1")._value.get()
    assert after == before + 1
    assert "LLM unavailable" in response.summary, (
        "the agentic answer must be discarded, not returned, on turn-1 capability failure"
    )


# ---------------------------------------------------------------------------
# LLM unreachable / absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_client_none_falls_back_without_calling_anything() -> None:
    response = await _run(None)
    assert "LLM unavailable" in response.summary


@pytest.mark.asyncio
async def test_llm_goes_down_mid_loop_falls_back() -> None:
    """A successful first hop followed by the LLM going unreachable —
    distinct from turn-1 capability failure (reason="llm_down", not
    "unsupported")."""
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        None,
    ])
    before = INVESTIGATION_FALLBACK_TOTAL.labels(reason="llm_down")._value.get()

    response = await _run(llm)

    after = INVESTIGATION_FALLBACK_TOTAL.labels(reason="llm_down")._value.get()
    assert after == before + 1
    assert "LLM unavailable" in response.summary


# ---------------------------------------------------------------------------
# Malicious / oversized tool results are redacted + capped (Layer A guardrail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malicious_tool_result_is_redacted_and_capped() -> None:
    audit = _make_audit_store()
    secret_event = MagicMock(
        timestamp="password: hunter2 " + ("x" * 2000),
        event_type="action_failed", vm_id=None, action_type=None,
    )

    async def _get_events(**_kw: object) -> list[MagicMock]:
        return [secret_event]

    audit.get_events = _get_events
    disk, baseline = _empty_stores()
    inventory = _make_inventory(["dev"])
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _final(_EMPTY_FINAL),
    ])

    agent = InvestigationAgent()
    await agent.investigate_agentic(
        "any failures?",
        audit_store=audit, disk_history_store=disk, baseline_store=baseline,
        inventory=inventory, llm_client=_as_llm(llm),
    )

    second_call_messages = llm.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m.get("role") == "tool")
    assert "hunter2" not in tool_msg["content"], "secret must be redacted before re-entering the model"
    assert len(tool_msg["content"]) < 600, "tool result must be capped, not passed through unbounded"


# ---------------------------------------------------------------------------
# Defensive clamp on misconfigured settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_budget_settings_are_clamped_not_fatal() -> None:
    """A misconfigured env var (e.g. ERRANDER_INVESTIGATION_AGENT_MAX_TOOL_CALLS=0)
    must not silently produce a zero-iteration loop — fall back to the
    hardcoded defaults and keep working. Uses a real tool hop before the
    final answer so this doesn't trip the unrelated turn-1 capability check
    (delta #4) — that's covered separately."""
    llm = _FakeLLMClient([
        _tool_request(_tool_call("call_1", "get_audit_events", limit=5)),
        _final({"summary": "done", "findings": [], "recommendations": [], "risk_level": "low"}),
    ])

    response = await _run(llm, max_tool_calls=0, timeout_seconds=-5)

    assert response.summary == "done"
    assert llm.calls, "the clamp must not prevent the loop from running at all"


# ---------------------------------------------------------------------------
# Layer A contract: never raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_in_loop_falls_back_never_raises() -> None:
    class _ExplodingLLMClient(_FakeLLMClient):
        async def complete_with_tools(self, *args: object, **kwargs: object) -> ToolCallResult | None:
            raise RuntimeError("boom — simulated unexpected failure")

    llm = _ExplodingLLMClient([])
    response = await _run(llm)  # must not raise
    assert "LLM unavailable" in response.summary
