"""Tests for probe-triggered investigations (fable-plan Phase 3).

Covers: candidate grouping (pure), the VM-level dedup window (real
AIDecisionStore), cap enforcement, and the orchestrator's enrichment /
new-proposal-filing / D2 (LLM-down leaves the proposal untouched) behavior.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from errander.agent.investigation_tools import ReadOnlyTool, ToolRegistry
from errander.agent.investigation_trigger import (
    NoOpFallback,
    _recently_investigated,
    group_candidates_by_vm,
    run_triggered_investigations,
    select_investigation_targets,
)
from errander.integrations.llm import AssistantTurn
from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
)
from errander.safety.ai_audit import AIDecision, AIDecisionStore
from errander.safety.proposal_store import ProposalStore
from tests.conftest import make_test_db


def _proposal(vm_id: str, action_type: str = "disk_cleanup", **overrides: object) -> AgentProposal:
    defaults: dict[str, object] = {
        "env_name": "prod",
        "vm_id": vm_id,
        "kind": ProposalKind.ACTION,
        "action_type": action_type,
        "signal_kind": "disk_growth",
        "probe_id": "probe-1",
        "evidence": [ProposalEvidence(
            source="probe:disk_history", check="trend", observation="90%",
        )],
        "confidence": "medium",
    }
    defaults.update(overrides)
    return AgentProposal(**defaults)  # type: ignore[arg-type]


class _FakeLLM:
    _model = "fake-model"
    _base_url = "http://fake"

    def __init__(self, turns: list[AssistantTurn | None]) -> None:
        self._turns = list(turns)

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        timeout_seconds: int | None = None,
    ) -> AssistantTurn | None:
        return self._turns.pop(0) if self._turns else None


def _final(**data: Any) -> AssistantTurn:
    payload = {
        "summary": "investigated", "findings": [], "recommendations": [],
        "risk_level": "low", "proposed_work": [],
    }
    payload.update(data)
    return AssistantTurn(content=json.dumps(payload))


def _empty_registry() -> ToolRegistry:
    async def run(_args: dict[str, Any]) -> str:
        return "no data"
    return ToolRegistry([ReadOnlyTool(
        name="get_audit_events", description="d",
        parameters={"type": "object", "properties": {}}, run=run,
    )])


class TestGroupCandidatesByVm:
    def test_empty_list(self) -> None:
        assert group_candidates_by_vm([]) == {}

    def test_groups_multiple_signals_same_vm(self) -> None:
        p1 = _proposal("web-01", action_type="disk_cleanup")
        p2 = _proposal("web-01", action_type="log_rotation")
        grouped = group_candidates_by_vm([p1, p2])
        assert list(grouped.keys()) == ["web-01"]
        assert grouped["web-01"] == [p1, p2]

    def test_separates_different_vms_preserving_order(self) -> None:
        p1 = _proposal("web-01")
        p2 = _proposal("db-01")
        grouped = group_candidates_by_vm([p1, p2])
        assert list(grouped.keys()) == ["web-01", "db-01"]


class TestDedupWindow:
    @pytest.mark.asyncio
    async def test_no_prior_decision_not_deduped(self) -> None:
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        assert await _recently_investigated(store, "web-01", 24) is False

    @pytest.mark.asyncio
    async def test_recent_success_is_deduped(self) -> None:
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        await store.log(AIDecision(
            batch_id="probe-trigger:web-01", decision_type="investigation_agent",
            model="m", base_url="u", prompt_template_id="t", prompt_hash="h",
            outcome="success",
        ))
        assert await _recently_investigated(store, "web-01", 24) is True

    @pytest.mark.asyncio
    async def test_recent_fallback_not_deduped(self) -> None:
        """Only a genuine success blocks a retry — a prior failure doesn't."""
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        await store.log(AIDecision(
            batch_id="probe-trigger:web-01", decision_type="investigation_agent",
            model="m", base_url="u", prompt_template_id="t", prompt_hash="h",
            outcome="fallback",
        ))
        assert await _recently_investigated(store, "web-01", 24) is False

    @pytest.mark.asyncio
    async def test_stale_success_not_deduped(self) -> None:
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        await store.log(AIDecision(
            batch_id="probe-trigger:web-01", decision_type="investigation_agent",
            model="m", base_url="u", prompt_template_id="t", prompt_hash="h",
            outcome="success",
            timestamp=datetime.now(tz=UTC) - timedelta(hours=48),
        ))
        assert await _recently_investigated(store, "web-01", 24) is False

    @pytest.mark.asyncio
    async def test_different_vm_not_deduped(self) -> None:
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        await store.log(AIDecision(
            batch_id="probe-trigger:web-01", decision_type="investigation_agent",
            model="m", base_url="u", prompt_template_id="t", prompt_hash="h",
            outcome="success",
        ))
        assert await _recently_investigated(store, "web-02", 24) is False

    @pytest.mark.asyncio
    async def test_none_store_never_dedups(self) -> None:
        assert await _recently_investigated(None, "web-01", 24) is False


class TestSelectTargets:
    @pytest.mark.asyncio
    async def test_caps_to_max_per_probe(self) -> None:
        candidates = {
            "web-01": [_proposal("web-01")],
            "web-02": [_proposal("web-02")],
            "web-03": [_proposal("web-03")],
        }
        selected = await select_investigation_targets(
            candidates, ai_decision_store=None, dedup_hours=24, max_per_probe=2,
        )
        assert selected == ["web-01", "web-02"]

    @pytest.mark.asyncio
    async def test_skips_deduped_includes_next(self) -> None:
        store = AIDecisionStore(make_test_db())
        await store.initialize()
        await store.log(AIDecision(
            batch_id="probe-trigger:web-01", decision_type="investigation_agent",
            model="m", base_url="u", prompt_template_id="t", prompt_hash="h",
            outcome="success",
        ))
        candidates = {"web-01": [_proposal("web-01")], "web-02": [_proposal("web-02")]}
        selected = await select_investigation_targets(
            candidates, ai_decision_store=store, dedup_hours=24, max_per_probe=5,
        )
        assert selected == ["web-02"]


class TestRunTriggeredInvestigations:
    @pytest.mark.asyncio
    async def test_enrichment_merges_evidence_and_raises_confidence(self) -> None:
        pstore = ProposalStore(make_test_db())
        await pstore.initialize()
        stored, _ = await pstore.create_or_refresh(_proposal("web-01", confidence="medium"))

        llm = _FakeLLM([_final(
            summary="root cause found", risk_level="high",
            findings=[{"text": "confirmed disk saturation", "evidence": ["get_audit_events"]}],
        )])
        audit = AsyncMock()

        investigated = await run_triggered_investigations(
            [stored], env_name="prod", valid_vm_ids={"web-01"},
            tools=_empty_registry(), llm_client=llm,  # type: ignore[arg-type]
            proposal_store=pstore, audit_store=audit, ai_decision_store=None,
            max_investigations_per_probe=3, dedup_hours=24,
            max_tool_calls=8, timeout_seconds=60,
        )
        assert investigated == 1
        loaded = await pstore.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.confidence == "high"
        assert any(
            e.source == "investigation_agent" and "disk saturation" in e.observation
            for e in loaded.evidence
        )
        # Original detector evidence is preserved, not replaced.
        assert any(e.source == "probe:disk_history" for e in loaded.evidence)

    @pytest.mark.asyncio
    async def test_llm_down_leaves_proposal_untouched(self) -> None:
        """D2: on LLM/tool failure the Phase 1 template proposal stands as-is."""
        pstore = ProposalStore(make_test_db())
        await pstore.initialize()
        original = _proposal("web-01", confidence="medium")
        stored, _ = await pstore.create_or_refresh(original)

        llm = _FakeLLM([None])  # transport failure -> NoOpFallback -> empty response
        audit = AsyncMock()

        investigated = await run_triggered_investigations(
            [stored], env_name="prod", valid_vm_ids={"web-01"},
            tools=_empty_registry(), llm_client=llm,  # type: ignore[arg-type]
            proposal_store=pstore, audit_store=audit, ai_decision_store=None,
            max_investigations_per_probe=3, dedup_hours=24,
            max_tool_calls=8, timeout_seconds=60,
        )
        assert investigated == 1  # attempted — but no enrichment happened
        loaded = await pstore.get(stored.proposal_id)
        assert loaded is not None
        assert loaded.confidence == "medium"  # unchanged
        assert len(loaded.evidence) == 1  # unchanged — only the original entry
        audit.log_event.assert_not_awaited()  # nothing to log — no enrichment occurred

    @pytest.mark.asyncio
    async def test_new_proposed_work_filed_via_existing_dedup(self) -> None:
        pstore = ProposalStore(make_test_db())
        await pstore.initialize()
        stored, _ = await pstore.create_or_refresh(_proposal("web-01", action_type="disk_cleanup"))

        llm = _FakeLLM([_final(proposed_work=[
            {"vm_id": "web-02", "action_type": "log_rotation", "rationale": "logs huge"},
        ])])
        audit = AsyncMock()

        await run_triggered_investigations(
            [stored], env_name="prod", valid_vm_ids={"web-01", "web-02"},
            tools=_empty_registry(), llm_client=llm,  # type: ignore[arg-type]
            proposal_store=pstore, audit_store=audit, ai_decision_store=None,
            max_investigations_per_probe=3, dedup_hours=24,
            max_tool_calls=8, timeout_seconds=60,
        )
        pending = await pstore.get_pending()
        new_ones = [p for p in pending if p.vm_id == "web-02"]
        assert len(new_ones) == 1
        assert new_ones[0].origin == "investigation_agent"
        assert new_ones[0].action_type == "log_rotation"

    @pytest.mark.asyncio
    async def test_new_proposed_work_respects_suppression(self) -> None:
        """fable-plan Phase 4: the trigger's own proposed_work filing must not
        bypass suppression — an agentic recommendation for a repeatedly-
        rejected (vm, action) pair is suppressed the same as the detector's."""
        pstore = ProposalStore(make_test_db())
        await pstore.initialize()

        # web-02/log_rotation has been rejected twice already.
        for _ in range(2):
            rejected, _ = await pstore.create_or_refresh(
                _proposal("web-02", action_type="log_rotation"),
            )
            await pstore.decide(rejected.proposal_id, approved=False, decided_by="ui:a")

        stored, _ = await pstore.create_or_refresh(_proposal("web-01", action_type="disk_cleanup"))
        llm = _FakeLLM([_final(proposed_work=[
            {"vm_id": "web-02", "action_type": "log_rotation", "rationale": "logs huge"},
        ])])
        audit = AsyncMock()

        await run_triggered_investigations(
            [stored], env_name="prod", valid_vm_ids={"web-01", "web-02"},
            tools=_empty_registry(), llm_client=llm,  # type: ignore[arg-type]
            proposal_store=pstore, audit_store=audit, ai_decision_store=None,
            max_investigations_per_probe=3, dedup_hours=24,
            max_tool_calls=8, timeout_seconds=60,
            suppression_threshold=2, suppression_window_days=14,
        )
        pending = await pstore.get_pending()
        assert [p.vm_id for p in pending] == ["web-01"]  # web-02 suppressed

    @pytest.mark.asyncio
    async def test_no_candidates_returns_zero_without_llm_call(self) -> None:
        llm = AsyncMock()
        result = await run_triggered_investigations(
            [], env_name="prod", valid_vm_ids=set(),
            tools=_empty_registry(), llm_client=llm,
            proposal_store=AsyncMock(), audit_store=AsyncMock(),
            ai_decision_store=None, max_investigations_per_probe=3,
            dedup_hours=24, max_tool_calls=8, timeout_seconds=60,
        )
        assert result == 0
        llm.chat_with_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_vm_failure_does_not_kill_the_loop(self) -> None:
        """A raise mid-investigation for one VM must not stop the others."""
        pstore = ProposalStore(make_test_db())
        await pstore.initialize()
        s1, _ = await pstore.create_or_refresh(_proposal("web-01"))
        s2, _ = await pstore.create_or_refresh(_proposal("web-02"))

        class _RaisingLLM:
            _model = "m"
            _base_url = "u"
            calls = 0

            async def chat_with_tools(self, *_a: Any, **_k: Any) -> AssistantTurn | None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("boom")
                return _final(summary="ok for web-02")

        llm = _RaisingLLM()
        investigated = await run_triggered_investigations(
            [s1, s2], env_name="prod", valid_vm_ids={"web-01", "web-02"},
            tools=_empty_registry(), llm_client=llm,  # type: ignore[arg-type]
            proposal_store=pstore, audit_store=AsyncMock(), ai_decision_store=None,
            max_investigations_per_probe=3, dedup_hours=24,
            max_tool_calls=8, timeout_seconds=60,
        )
        assert investigated == 1  # web-01 failed (not counted); web-02 succeeded


class TestNoOpFallback:
    @pytest.mark.asyncio
    async def test_returns_empty_response_instantly(self) -> None:
        resp = await NoOpFallback().investigate("anything", audit_store="ignored")
        assert resp.summary == ""
        assert resp.findings == []
        assert resp.proposed_work == []
        assert resp.risk_level == "unknown"
