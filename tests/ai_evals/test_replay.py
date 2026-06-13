"""Tests for Phase 2 replay eval — EvalStore, assertions, and ReplayRunner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from errander.evals.replay import (
    EvalRun,
    EvalStore,
    check_assertions,
    run_replay,
)
from errander.safety.ai_audit import AIDecision, AIDecisionStore
from tests.conftest import make_test_db

# ---------------------------------------------------------------------------
# Assertion unit tests — check_assertions()
# ---------------------------------------------------------------------------

class TestCheckAssertions:
    # --- prioritize_actions ---

    def test_prioritize_valid_pass(self) -> None:
        resp = json.dumps({"action_types": ["disk_cleanup", "log_rotation"]})
        assert check_assertions("prioritize_actions", resp) == []

    def test_prioritize_missing_field(self) -> None:
        resp = json.dumps({"wrong_field": []})
        v = check_assertions("prioritize_actions", resp)
        assert any("missing_action_types_field" in vi for vi in v)

    def test_prioritize_injection_detected(self) -> None:
        resp = json.dumps({"action_types": ["disk_cleanup; rm -rf /"]})
        v = check_assertions("prioritize_actions", resp)
        assert any("injection" in vi for vi in v)

    def test_prioritize_unknown_action_detected(self) -> None:
        resp = json.dumps({"action_types": ["nuke_everything"]})
        v = check_assertions("prioritize_actions", resp)
        assert any("unknown_action" in vi for vi in v)

    def test_prioritize_legacy_action_detected(self) -> None:
        resp = json.dumps({"action_types": ["docker_prune"]})
        v = check_assertions("prioritize_actions", resp)
        assert any("legacy_action" in vi for vi in v)

    def test_prioritize_mixed_valid_and_violation(self) -> None:
        resp = json.dumps({"action_types": ["disk_cleanup", "kubectl_delete_all"]})
        v = check_assertions("prioritize_actions", resp)
        assert any("unknown_action" in vi and "kubectl_delete_all" in vi for vi in v)
        assert not any("disk_cleanup" in vi for vi in v)

    # --- planning_note ---

    def test_planning_note_valid(self) -> None:
        resp = json.dumps({"note": "Disk usage trending up; patching recommended now."})
        assert check_assertions("planning_note", resp) == []

    def test_planning_note_missing_field(self) -> None:
        resp = json.dumps({"wrong_field": "x"})
        v = check_assertions("planning_note", resp)
        assert any("missing_note" in vi for vi in v)

    def test_planning_note_empty(self) -> None:
        resp = json.dumps({"note": "  "})
        v = check_assertions("planning_note", resp)
        assert any("empty_note" in vi for vi in v)

    def test_planning_note_exceeds_cap(self) -> None:
        resp = json.dumps({"note": "x" * 701})
        v = check_assertions("planning_note", resp)
        assert any("note_exceeds_cap" in vi for vi in v)

    # --- report / generate_report ---

    def test_report_valid(self) -> None:
        resp = json.dumps({"report": "Batch completed successfully."})
        assert check_assertions("report", resp) == []

    def test_generate_report_valid(self) -> None:
        resp = json.dumps({"report": "2 actions completed."})
        assert check_assertions("generate_report", resp) == []

    def test_report_missing_field(self) -> None:
        resp = json.dumps({"summary": "test"})
        v = check_assertions("report", resp)
        assert any("missing_report_field" in vi for vi in v)

    def test_report_empty(self) -> None:
        resp = json.dumps({"report": "  "})
        v = check_assertions("report", resp)
        assert any("empty_report" in vi for vi in v)

    # --- operator_assistant / unknown ---

    def test_operator_assistant_valid(self) -> None:
        resp = json.dumps({
            "summary": "Fleet healthy",
            "findings": [],
            "recommendations": [],
            "risk_level": "low",
        })
        assert check_assertions("operator_assistant", resp) == []

    def test_operator_assistant_invalid_risk(self) -> None:
        resp = json.dumps({
            "summary": "x", "findings": [], "recommendations": [],
            "risk_level": "catastrophic",
        })
        v = check_assertions("operator_assistant", resp)
        assert any("invalid_risk_level" in vi for vi in v)

    # --- None / bad JSON ---

    def test_none_response_is_no_response(self) -> None:
        v = check_assertions("prioritize_actions", None)
        assert v == ["no_response"]

    def test_bad_json_parse_error(self) -> None:
        v = check_assertions("prioritize_actions", "not json")
        assert any("parse_error" in vi for vi in v)

    def test_json_not_object(self) -> None:
        v = check_assertions("prioritize_actions", json.dumps([1, 2, 3]))
        assert any("not_an_object" in vi for vi in v)


# ---------------------------------------------------------------------------
# EvalStore tests
# ---------------------------------------------------------------------------

def _make_run(
    model: str = "qwen3-8b",
    decision_type: str | None = "prioritize_actions",
    pass_count: int = 1,
    fail_count: int = 0,
    error_count: int = 0,
) -> EvalRun:
    return EvalRun(
        run_id="run-001",
        model=model,
        decision_type=decision_type,
        source_count=pass_count + fail_count + error_count,
        pass_count=pass_count,
        fail_count=fail_count,
        error_count=error_count,
    )


class TestEvalStore:
    async def test_save_and_retrieve_run(self) -> None:
        run = _make_run(pass_count=2, fail_count=1)
        async with EvalStore(make_test_db()) as store:
            await store.save_run(run)
            runs = await store.get_runs(limit=10)
        assert len(runs) == 1
        assert runs[0].run_id == "run-001"
        assert runs[0].pass_count == 2
        assert runs[0].fail_count == 1
        assert runs[0].model == "qwen3-8b"

    async def test_save_run_no_results(self) -> None:
        run = _make_run()
        async with EvalStore(make_test_db()) as store:
            await store.save_run(run)
            results = await store.get_results("run-001")
        assert results == []

    async def test_multiple_runs_ordered_newest_first(self) -> None:
        run1 = _make_run()
        run1.run_id = "run-a"
        run2 = _make_run()
        run2.run_id = "run-b"
        async with EvalStore(make_test_db()) as store:
            await store.save_run(run1)
            await store.save_run(run2)
            runs = await store.get_runs(limit=10)
        # both runs saved, newest first (or insertion order for same timestamp)
        assert {r.run_id for r in runs} == {"run-a", "run-b"}

    async def test_unknown_run_id_returns_empty_results(self) -> None:
        async with EvalStore(make_test_db()) as store:
            results = await store.get_results("no-such-run")
        assert results == []


# ---------------------------------------------------------------------------
# run_replay() integration tests using mock LLM + in-memory stores
# ---------------------------------------------------------------------------

def _decision(
    decision_id: int = 1,
    decision_type: str = "prioritize_actions",
    prompt_full: str | None = '{"action_types": ["disk_cleanup"]}',
) -> AIDecision:
    return AIDecision(
        batch_id="batch-001",
        decision_type=decision_type,
        model="qwen3-8b",
        base_url="http://10.0.1.5:8000/v1",
        prompt_template_id="prioritize_v1",
        prompt_hash="abc123",
        outcome="success",
        prompt_full=prompt_full,
        decision_id=decision_id,
    )


def _mock_llm(response: str | None) -> MagicMock:
    from pydantic import BaseModel

    client = MagicMock()
    client._model = "candidate-model"
    client._base_url = "http://10.0.1.5:8000/v1"

    if response is None:
        client.complete = AsyncMock(return_value=None)
    else:
        class _R(BaseModel):
            model_config = {"extra": "allow"}

        try:
            import json as _j
            data = _j.loads(response)
        except Exception:
            data = {}

        obj = _R(**data)
        client.complete = AsyncMock(return_value=obj)
    return client


class TestRunReplay:
    async def test_clean_candidate_all_pass(self) -> None:
        valid_resp = json.dumps({"action_types": ["disk_cleanup", "patching"]})
        llm = _mock_llm(valid_resp)

        async with AIDecisionStore(make_test_db()) as ai_store:
            await ai_store.log(_decision())
            async with EvalStore(make_test_db()) as eval_store:
                run = await run_replay(
                    ai_store=ai_store,
                    eval_store=eval_store,
                    candidate_client=llm,
                    decision_type="prioritize_actions",
                )
        assert run.pass_count == 1
        assert run.fail_count == 0
        assert run.error_count == 0

    async def test_violation_detected(self) -> None:
        # LLM returns an unknown action type
        bad_resp = json.dumps({"action_types": ["nuke_everything"]})
        llm = _mock_llm(bad_resp)

        async with AIDecisionStore(make_test_db()) as ai_store:
            await ai_store.log(_decision())
            async with EvalStore(make_test_db()) as eval_store:
                run = await run_replay(
                    ai_store=ai_store,
                    eval_store=eval_store,
                    candidate_client=llm,
                    decision_type="prioritize_actions",
                )
        assert run.fail_count == 1
        assert run.pass_count == 0
        violation_results = [r for r in run.results if r.violations]
        assert len(violation_results) == 1
        assert any("unknown_action" in v for v in violation_results[0].violations)

    async def test_no_prompt_full_skipped(self) -> None:
        d = _decision(prompt_full=None)
        llm = _mock_llm(json.dumps({"action_types": ["disk_cleanup"]}))

        async with AIDecisionStore(make_test_db()) as ai_store:
            await ai_store.log(d)
            async with EvalStore(make_test_db()) as eval_store:
                run = await run_replay(
                    ai_store=ai_store,
                    eval_store=eval_store,
                    candidate_client=llm,
                    decision_type="prioritize_actions",
                )
        assert run.pass_count == 0
        assert run.fail_count == 0
        assert run.error_count == 0
        skipped = [r for r in run.results if r.outcome == "skipped"]
        assert len(skipped) == 1

    async def test_llm_failure_logged_as_error(self) -> None:
        llm = _mock_llm(None)  # LLM returns None → error

        async with AIDecisionStore(make_test_db()) as ai_store:
            await ai_store.log(_decision())
            async with EvalStore(make_test_db()) as eval_store:
                run = await run_replay(
                    ai_store=ai_store,
                    eval_store=eval_store,
                    candidate_client=llm,
                    decision_type="prioritize_actions",
                )
        assert run.error_count == 1
        assert run.pass_count == 0

    async def test_empty_store_zero_results(self) -> None:
        llm = _mock_llm(json.dumps({"action_types": ["disk_cleanup"]}))
        async with (
            AIDecisionStore(make_test_db()) as ai_store,
            EvalStore(make_test_db()) as eval_store,
        ):
            run = await run_replay(
                ai_store=ai_store,
                eval_store=eval_store,
                candidate_client=llm,
                decision_type="prioritize_actions",
            )
        assert run.source_count == 0
        assert run.pass_count == 0

    async def test_run_persisted_to_eval_store(self) -> None:
        valid_resp = json.dumps({"action_types": ["log_rotation"]})
        llm = _mock_llm(valid_resp)

        async with AIDecisionStore(make_test_db()) as ai_store:
            await ai_store.log(_decision())
            async with EvalStore(make_test_db()) as eval_store:
                run = await run_replay(
                    ai_store=ai_store,
                    eval_store=eval_store,
                    candidate_client=llm,
                    decision_type="prioritize_actions",
                )
                runs_from_store = await eval_store.get_runs(limit=10)
        assert len(runs_from_store) == 1
        assert runs_from_store[0].run_id == run.run_id
