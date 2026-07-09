"""Tests for the golden fleet scenario harness (fable-plan Phase 5).

Distinct from test_golden_plans.py (which grades the batch-planning
pipeline's safety properties). This file grades detect-and-propose: does
the deterministic detector propose the right thing for a known root cause,
and only the right thing?

Two kinds of coverage here:
  1. The REAL default_scenarios() must all pass — this is a regression
     alarm on errander.agent.proposal_detector itself.
  2. The scoring math must correctly FAIL a deliberately-wrong scenario —
     proving the harness isn't vacuously green (a scorer that always says
     "pass" is worse than no scorer at all).
"""

from __future__ import annotations

import pytest

from errander.evals.golden_scenarios import (
    GoldenScenario,
    ScenarioResult,
    default_scenarios,
    run_golden_scenarios,
)
from errander.models.reports import DigestReport, ProbeVMResult
from errander.safety.audit import AuditStore
from errander.safety.proposal_store import ProposalStore
from tests.conftest import make_test_db


class TestDefaultScenariosStoreLess:
    """The offline path — zero I/O, safe to run anywhere, no LLM."""

    @pytest.mark.asyncio
    async def test_all_non_suppression_scenarios_pass(self) -> None:
        summary = await run_golden_scenarios(default_scenarios())
        non_skipped = [r for r in summary.results if not r.skipped]
        assert non_skipped, "expected at least one non-store-dependent scenario"
        failing = [r for r in non_skipped if not r.passed]
        assert not failing, f"golden scenarios regressed: {[r.scenario_name for r in failing]}"

    @pytest.mark.asyncio
    async def test_perfect_precision_and_recall(self) -> None:
        summary = await run_golden_scenarios(default_scenarios())
        assert summary.precision == 1.0
        assert summary.recall == 1.0

    @pytest.mark.asyncio
    async def test_store_dependent_scenario_is_skipped_without_a_store(self) -> None:
        summary = await run_golden_scenarios(default_scenarios())
        skipped = [r for r in summary.results if r.skipped]
        assert len(skipped) == 1
        assert skipped[0].scenario_name == "suppressed_pair_not_reproposed"
        assert skipped[0].passed is True  # skipped counts as passed, not failed

    @pytest.mark.asyncio
    async def test_scenario_count_matches_registry(self) -> None:
        scenarios = default_scenarios()
        summary = await run_golden_scenarios(scenarios)
        assert len(summary.results) == len(scenarios)
        assert {s.name for s in scenarios} == {r.scenario_name for r in summary.results}


class TestDefaultScenariosStoreBacked:
    """With a real ProposalStore — full coverage including Phase 4 suppression."""

    @pytest.mark.asyncio
    async def test_all_scenarios_pass_including_suppression(self) -> None:
        db = make_test_db()
        pstore = ProposalStore(db)
        await pstore.initialize()
        astore = AuditStore(db, strict_mode=False)
        await astore.initialize()

        summary = await run_golden_scenarios(
            default_scenarios(), proposal_store=pstore, audit_store=astore,
        )
        assert summary.all_passed, [
            (r.scenario_name, r.false_positives, r.false_negatives)
            for r in summary.results if not r.passed
        ]
        assert not any(r.skipped for r in summary.results)

    @pytest.mark.asyncio
    async def test_suppressed_scenario_actually_exercises_suppression(self) -> None:
        """Not just 'passed' — confirm the suppressed pair really produced
        zero stored proposals, proving file_or_suppress_one was invoked."""
        db = make_test_db()
        pstore = ProposalStore(db)
        await pstore.initialize()
        astore = AuditStore(db, strict_mode=False)
        await astore.initialize()

        await run_golden_scenarios(default_scenarios(), proposal_store=pstore, audit_store=astore)
        pending = await pstore.get_pending()
        assert not any(p.vm_id == "web-07" for p in pending)


class TestScoringMathCatchesRegressions:
    """The scoring harness itself must correctly fail a wrong scenario —
    proving it's not vacuously green."""

    @pytest.mark.asyncio
    async def test_wrong_expectation_produces_false_positive(self) -> None:
        report = DigestReport(
            probe_id="regression-1", env_name="prod",
            generated_at=default_scenarios()[0].report.generated_at,
            vm_results=[ProbeVMResult(
                vm_id="web-99", hostname="10.0.0.99",
                disk_growth_alerts=[{"mountpoint": "/", "used_pct_end": 85.0, "delta_pct": 6.0}],
            )],
        )
        # Deliberately wrong: the detector WILL propose disk_cleanup, but we
        # assert it shouldn't — the harness must catch this as a failure.
        bad_scenario = GoldenScenario(
            name="deliberately_wrong",
            description="Sanity check that the scorer can fail.",
            report=report,
            enabled_actions_by_vm={"web-99": {"disk_cleanup"}},
            expected_action_proposals=set(),  # wrong on purpose
        )
        summary = await run_golden_scenarios([bad_scenario])
        result = summary.results[0]
        assert result.passed is False
        assert ("web-99", "disk_cleanup") in result.false_positives
        assert summary.all_passed is False

    @pytest.mark.asyncio
    async def test_missed_expectation_produces_false_negative(self) -> None:
        report = DigestReport(
            probe_id="regression-2", env_name="prod",
            generated_at=default_scenarios()[0].report.generated_at,
            vm_results=[ProbeVMResult(vm_id="web-98", hostname="10.0.0.98")],  # no signals
        )
        bad_scenario = GoldenScenario(
            name="expects_something_that_never_comes",
            description="Sanity check for false-negative detection.",
            report=report,
            enabled_actions_by_vm={"web-98": {"disk_cleanup"}},
            expected_action_proposals={("web-98", "disk_cleanup")},  # wrong on purpose
        )
        summary = await run_golden_scenarios([bad_scenario])
        result = summary.results[0]
        assert result.passed is False
        assert ("web-98", "disk_cleanup") in result.false_negatives


class TestGoldenEvalSummaryMath:
    """Pure math on hand-built ScenarioResults — no I/O."""

    def test_precision_recall_with_mixed_results(self) -> None:
        from errander.evals.golden_scenarios import GoldenEvalSummary

        summary = GoldenEvalSummary(results=[
            ScenarioResult(
                "s1",
                true_positives={("a", "disk_cleanup")},
                false_positives={("a", "log_rotation")},
            ),
            ScenarioResult(
                "s2",
                true_positives={("b", "disk_cleanup")},
                false_negatives={("b", "log_rotation")},
            ),
        ])
        # tp=2, fp=1, fn=1
        assert summary.precision == pytest.approx(2 / 3)
        assert summary.recall == pytest.approx(2 / 3)
        assert summary.all_passed is False

    def test_empty_results_yield_perfect_score(self) -> None:
        from errander.evals.golden_scenarios import GoldenEvalSummary

        summary = GoldenEvalSummary(results=[])
        assert summary.precision == 1.0
        assert summary.recall == 1.0
        assert summary.all_passed is True
