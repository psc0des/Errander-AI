"""Tests for the Layer A agentic investigation loop (fable-plan Phase 2).

Uses a scripted fake tool-calling LLM (no network) to lock the loop's
behavior: tool dispatch, budget enforcement, graceful fallback, proposed_work
validation, and untrusted-tool-result handling.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from errander.agent.investigation_agent import (
    InvestigationAgent,
    proposed_work_to_proposals,
)
from errander.agent.investigation_tools import ReadOnlyTool, ToolRegistry
from errander.integrations.llm import AssistantTurn, ToolCall
from errander.models.analysis import AssistantResponse


class _FakeLLM:
    """Scripted chat_with_tools: returns queued turns in order."""

    _model = "fake-model"
    _base_url = "http://fake"

    def __init__(self, turns: list[AssistantTurn | None]) -> None:
        self._turns = list(turns)
        self.calls = 0

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        timeout_seconds: int | None = None,
    ) -> AssistantTurn | None:
        self.calls += 1
        return self._turns.pop(0) if self._turns else None


def _final(**data: Any) -> AssistantTurn:
    payload = {
        "summary": "done",
        "findings": [],
        "recommendations": [],
        "risk_level": "low",
        "proposed_work": [],
    }
    payload.update(data)
    return AssistantTurn(content=json.dumps(payload))


def _tool_call(name: str, **args: Any) -> AssistantTurn:
    return AssistantTurn(tool_calls=[
        ToolCall(id="c1", name=name, arguments=json.dumps(args)),
    ])


def _registry(result: str = "tool output") -> ToolRegistry:
    async def run(_args: dict[str, Any]) -> str:
        return result
    return ToolRegistry([ReadOnlyTool(
        name="get_audit_events", description="d",
        parameters={"type": "object", "properties": {}}, run=run,
    )])


def _fallback() -> AsyncMock:
    fb = AsyncMock()
    fb.investigate.return_value = AssistantResponse(
        summary="deterministic fallback", findings=[], recommendations=[],
        risk_level="unknown",
    )
    return fb


class TestLoop:
    @pytest.mark.asyncio
    async def test_tool_then_final_answer(self) -> None:
        llm = _FakeLLM([
            _tool_call("get_audit_events", limit=5),
            _final(summary="root cause found", risk_level="medium"),
        ])
        fb = _fallback()
        agent = InvestigationAgent(max_tool_calls=8, timeout_seconds=60)
        resp = await agent.investigate_agentic(
            "why is web-01 slow?", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=fb, fallback_kwargs={},
        )
        assert resp.summary == "root cause found"
        assert resp.risk_level == "medium"
        assert llm.calls == 2
        fb.investigate.assert_not_awaited()  # never fell back

    @pytest.mark.asyncio
    async def test_immediate_final_answer_no_tools(self) -> None:
        llm = _FakeLLM([_final(summary="no investigation needed")])
        agent = InvestigationAgent()
        resp = await agent.investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=_fallback(), fallback_kwargs={},
        )
        assert resp.summary == "no investigation needed"


class TestFallback:
    @pytest.mark.asyncio
    async def test_llm_unavailable_falls_back(self) -> None:
        llm = _FakeLLM([None])  # transport failure
        fb = _fallback()
        resp = await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=fb, fallback_kwargs={},
        )
        assert resp.summary == "deterministic fallback"
        fb.investigate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_budget_exhausted_falls_back(self) -> None:
        # Always asks for a tool, never gives a final answer.
        llm = _FakeLLM([_tool_call("get_audit_events") for _ in range(10)])
        fb = _fallback()
        resp = await InvestigationAgent(max_tool_calls=3).investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=fb, fallback_kwargs={},
        )
        assert resp.summary == "deterministic fallback"
        assert llm.calls == 3  # stopped at the cap
        fb.investigate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unparseable_final_answer_falls_back(self) -> None:
        llm = _FakeLLM([AssistantTurn(content="not json at all")])
        fb = _fallback()
        resp = await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=fb, fallback_kwargs={},
        )
        assert resp.summary == "deterministic fallback"


class TestProposedWorkValidation:
    @pytest.mark.asyncio
    async def test_valid_proposed_work_kept(self) -> None:
        llm = _FakeLLM([_final(proposed_work=[
            {"vm_id": "web-01", "action_type": "disk_cleanup", "rationale": "disk 92%"},
        ])])
        resp = await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=_fallback(), fallback_kwargs={},
        )
        assert len(resp.proposed_work) == 1
        assert resp.proposed_work[0].action_type == "disk_cleanup"

    @pytest.mark.asyncio
    async def test_hallucinated_action_dropped_not_raised(self) -> None:
        """A non-proposable action (e.g. docker_hygiene) is dropped, answer survives."""
        llm = _FakeLLM([_final(
            summary="answer stands",
            proposed_work=[
                {"vm_id": "web-01", "action_type": "docker_hygiene", "rationale": "x"},
                {"vm_id": "web-02", "action_type": "log_rotation", "rationale": "logs big"},
            ],
        )])
        resp = await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=_fallback(), fallback_kwargs={},
        )
        assert resp.summary == "answer stands"
        assert [i.action_type for i in resp.proposed_work] == ["log_rotation"]

    @pytest.mark.asyncio
    async def test_injection_in_vm_id_dropped(self) -> None:
        llm = _FakeLLM([_final(proposed_work=[
            {"vm_id": "web-01; rm -rf /", "action_type": "disk_cleanup", "rationale": "x"},
        ])])
        resp = await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=_fallback(), fallback_kwargs={},
        )
        assert resp.proposed_work == []


class TestAudit:
    @pytest.mark.asyncio
    async def test_per_hop_and_final_audited(self) -> None:
        llm = _FakeLLM([_tool_call("get_audit_events"), _final()])
        ai_store = AsyncMock()
        await InvestigationAgent().investigate_agentic(
            "q", tools=_registry(), llm_client=llm,  # type: ignore[arg-type]
            fallback=_fallback(), fallback_kwargs={}, ai_decision_store=ai_store,
        )
        types = [c.args[0].decision_type for c in ai_store.log.await_args_list]
        # One step row for the tool call + one final row.
        assert "investigation_agent_step" in types
        assert "investigation_agent" in types


class TestProposedWorkToProposals:
    def test_inventory_gate_drops_unknown_vm(self) -> None:
        resp = AssistantResponse(
            summary="s", findings=[], recommendations=[], risk_level="low",
            proposed_work=[
                {"vm_id": "web-01", "action_type": "disk_cleanup", "rationale": "a"},  # type: ignore[list-item]
                {"vm_id": "ghost", "action_type": "log_rotation", "rationale": "b"},  # type: ignore[list-item]
            ],
        )
        proposals = proposed_work_to_proposals(
            resp, env_name="prod", valid_vm_ids={"web-01"},
        )
        assert [p.vm_id for p in proposals] == ["web-01"]
        assert proposals[0].origin == "investigation_agent"
        assert proposals[0].evidence[0].observation == "a"
