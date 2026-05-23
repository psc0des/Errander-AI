"""Tests for AIDecisionStore.get_decision_by_id() and decision_id field."""

from __future__ import annotations

import pytest

from errander.safety.ai_audit import AIDecision, AIDecisionStore


def _decision(**kwargs: object) -> AIDecision:
    defaults: dict[str, object] = {
        "batch_id": "batch-001",
        "decision_type": "prioritize_actions",
        "model": "qwen3-8b",
        "base_url": "http://10.0.1.5:8000/v1",
        "prompt_template_id": "prioritize_v1",
        "prompt_hash": "abc123def456",
        "outcome": "success",
        "vm_id": "dev/web-01",
    }
    defaults.update(kwargs)
    return AIDecision(**defaults)  # type: ignore[arg-type]


class TestGetDecisionById:
    @pytest.mark.asyncio
    async def test_get_decision_by_id_returns_correct_record(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.log(_decision(batch_id="batch-find-001", outcome="success"))
            decisions = await store.get_decisions(batch_id="batch-find-001")
            assert len(decisions) == 1
            stored_id = decisions[0].decision_id
            assert stored_id is not None

            found = await store.get_decision_by_id(stored_id)

        assert found is not None
        assert found.batch_id == "batch-find-001"
        assert found.outcome == "success"
        assert found.decision_id == stored_id

    @pytest.mark.asyncio
    async def test_get_decision_by_id_unknown_id_returns_none(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            result = await store.get_decision_by_id(99999)
        assert result is None

    @pytest.mark.asyncio
    async def test_decision_id_field_populated_after_log_and_query(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.log(_decision())
            decisions = await store.get_decisions(batch_id="batch-001")

        assert len(decisions) == 1
        assert decisions[0].decision_id is not None
        assert decisions[0].decision_id > 0

    @pytest.mark.asyncio
    async def test_id_field_roundtrip_all_fields(self) -> None:
        d = _decision(
            batch_id="batch-rt-001",
            outcome="fallback",
            prompt_full="test prompt",
            context_snapshot='{"vm": "web-01"}',
            model_params='{"temperature": 0.1}',
        )
        async with AIDecisionStore(":memory:") as store:
            await store.log(d)
            decisions = await store.get_decisions(batch_id="batch-rt-001")
            assert len(decisions) == 1
            did = decisions[0].decision_id
            assert did is not None

            found = await store.get_decision_by_id(did)

        assert found is not None
        assert found.outcome == "fallback"
        assert found.prompt_full == "test prompt"
        assert found.context_snapshot == '{"vm": "web-01"}'
        assert found.model_params == '{"temperature": 0.1}'

    @pytest.mark.asyncio
    async def test_multiple_decisions_have_distinct_ids(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.log(_decision(batch_id="batch-a"))
            await store.log(_decision(batch_id="batch-b"))
            all_decisions = await store.get_decisions(limit=10)

        ids = [d.decision_id for d in all_decisions if d.decision_id is not None]
        assert len(ids) == 2
        assert ids[0] != ids[1]
