"""Tests for the agentic guardrail golden scenarios (fable-plan Phase 5).

These regression-test errander.agent.investigation_agent's own validation
logic (_parse_final) by scripting a fake LLM that tries to cheat — cite an
uncalled tool, recommend a destructive action, inject shell metacharacters
into a vm_id — and asserting the existing guardrails catch it. Offline,
no network, no real LLM.

As with test_golden_fleet_scenarios.py, coverage has two halves: the real
default scenarios must all pass (regression alarm on investigation_agent.py),
and the harness's own pass/fail detection must correctly flag a scenario
that's deliberately mis-specified (proving it isn't vacuously green).
"""

from __future__ import annotations

import pytest

from errander.evals.agentic_guardrails import (
    AgenticGuardrailScenario,
    default_agentic_scenarios,
    run_agentic_guardrail_scenarios,
)
from errander.integrations.llm import AssistantTurn, ToolCall


class TestDefaultScenarios:
    @pytest.mark.asyncio
    async def test_all_scenarios_pass(self) -> None:
        results = await run_agentic_guardrail_scenarios(default_agentic_scenarios())
        failing = [r for r in results if not r.passed]
        assert not failing, f"guardrail regression: {[(r.scenario_name, r.failures) for r in failing]}"

    @pytest.mark.asyncio
    async def test_scenario_count_matches_registry(self) -> None:
        scenarios = default_agentic_scenarios()
        results = await run_agentic_guardrail_scenarios(scenarios)
        assert len(results) == len(scenarios)
        assert {s.name for s in scenarios} == {r.scenario_name for r in results}

    @pytest.mark.asyncio
    async def test_uncited_evidence_scenario_actually_had_a_second_finding(self) -> None:
        """Sanity: the scenario's fabricated finding really was in the
        model's raw answer — the guardrail stripped it, not our test setup
        omitting it."""
        scenarios = {s.name: s for s in default_agentic_scenarios()}
        scenario = scenarios["uncited_tool_evidence_stripped"]
        final_turn = scenario.turns[-1]
        assert final_turn.content is not None
        assert "get_vm_metrics" in final_turn.content  # present in the raw script
        assert "fabricated finding" in final_turn.content


class TestHarnessCatchesRegressions:
    """The guardrail scenario harness must correctly FAIL a scenario whose
    assertions don't match reality — proving it isn't vacuously green."""

    @pytest.mark.asyncio
    async def test_wrong_forbidden_evidence_is_flagged(self) -> None:
        """Assert a tool WAS called must be flagged as 'leaked' if we (the
        scenario author) mistakenly forbid a citation the model legitimately
        earned by calling that tool."""
        scenario = AgenticGuardrailScenario(
            name="deliberately_wrong",
            description="Sanity check that forbidding a legitimate citation fails.",
            turns=[
                AssistantTurn(content=None, tool_calls=[
                    ToolCall(id="c1", name="get_audit_events", arguments="{}"),
                ]),
                AssistantTurn(content=(
                    '{"summary": "s", "findings": [{"text": "t", '
                    '"evidence": ["get_audit_events"]}], "recommendations": [], '
                    '"risk_level": "low", "proposed_work": []}'
                )),
            ],
            available_tools=["get_audit_events"],
            forbidden_evidence={"get_audit_events"},  # wrong — this WAS called
        )
        results = await run_agentic_guardrail_scenarios([scenario])
        assert results[0].passed is False
        assert any("forbidden evidence survived" in f for f in results[0].failures)

    @pytest.mark.asyncio
    async def test_wrongly_expected_proposed_work_is_flagged_missing(self) -> None:
        scenario = AgenticGuardrailScenario(
            name="expects_work_that_never_comes",
            description="Sanity check for missing-expected-work detection.",
            turns=[AssistantTurn(content=(
                '{"summary": "s", "findings": [], "recommendations": [], '
                '"risk_level": "low", "proposed_work": []}'
            ))],
            expected_proposed_work={("web-01", "disk_cleanup")},  # never produced
        )
        results = await run_agentic_guardrail_scenarios([scenario])
        assert results[0].passed is False
        assert any("expected proposed_work missing" in f for f in results[0].failures)


class TestGuardrailResult:
    def test_passed_true_when_no_failures(self) -> None:
        from errander.evals.agentic_guardrails import GuardrailResult

        assert GuardrailResult("s", failures=[]).passed is True

    def test_passed_false_when_any_failure(self) -> None:
        from errander.evals.agentic_guardrails import GuardrailResult

        assert GuardrailResult("s", failures=["x"]).passed is False
