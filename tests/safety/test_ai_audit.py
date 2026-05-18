"""Tests for AIDecisionStore — including D1 full-prompt capture columns."""

from __future__ import annotations

import json

import aiosqlite
import pytest

from errander.safety.ai_audit import AIDecision, AIDecisionStore


def _decision(**kwargs: object) -> AIDecision:
    defaults: dict[str, object] = {
        "batch_id": "batch-001",
        "decision_type": "prioritize_actions",
        "model": "qwen3-8b",
        "base_url": "http://localhost:8000",
        "prompt_template_id": "prioritize_v1",
        "prompt_hash": "abc123",
        "outcome": "success",
    }
    defaults.update(kwargs)
    return AIDecision(**defaults)  # type: ignore[arg-type]


class TestAIDecisionStoreLifecycle:
    async def test_context_manager_opens_and_closes(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            assert store._db is not None
        assert store._db is None

    async def test_not_initialized_raises(self) -> None:
        store = AIDecisionStore(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            await store.log(_decision())

    async def test_idempotent_initialize(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.initialize()  # second call must not raise
            assert store._db is not None


class TestAIDecisionStoreSchema:
    async def test_d1_columns_present_after_init(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            db = store._db
            assert db is not None
            cursor = await db.execute("PRAGMA table_info(ai_decisions)")
            cols = {str(row[1]) for row in await cursor.fetchall()}
        assert "prompt_full" in cols
        assert "context_snapshot" in cols
        assert "model_params" in cols

    async def test_schema_migration_on_old_table(self) -> None:
        db = await aiosqlite.connect(":memory:")
        # Create the pre-D1 table (14 columns, no prompt_full etc.)
        await db.execute("""
            CREATE TABLE ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                vm_id TEXT,
                decision_type TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                prompt_template_id TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                response_raw TEXT,
                outcome TEXT NOT NULL,
                latency_ms REAL,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                timestamp TEXT NOT NULL
            )
        """)
        await db.commit()

        store = AIDecisionStore.__new__(AIDecisionStore)
        store._db_path = ":memory:"
        store._db = db

        # Simulate the D1 ALTER TABLE portion of initialize()
        import contextlib
        for col_def in AIDecisionStore._D1_COLUMNS:
            with contextlib.suppress(aiosqlite.OperationalError):
                await db.execute(f"ALTER TABLE ai_decisions ADD COLUMN {col_def}")
        await db.commit()

        cursor = await db.execute("PRAGMA table_info(ai_decisions)")
        cols = {str(row[1]) for row in await cursor.fetchall()}
        assert "prompt_full" in cols
        assert "context_snapshot" in cols
        assert "model_params" in cols
        await db.close()

    async def test_alter_idempotent_on_fresh_table(self) -> None:
        # Fresh install: _CREATE_TABLE_SQL includes new columns.
        # Running ALTER TABLE a second time (via a second initialize) must not raise.
        async with AIDecisionStore(":memory:") as store:
            db = store._db
            assert db is not None
            import contextlib
            for col_def in AIDecisionStore._D1_COLUMNS:
                with contextlib.suppress(aiosqlite.OperationalError):
                    await db.execute(f"ALTER TABLE ai_decisions ADD COLUMN {col_def}")
            await db.commit()
            # No exception raised — idempotent
            cursor = await db.execute("PRAGMA table_info(ai_decisions)")
            cols = {str(row[1]) for row in await cursor.fetchall()}
        assert "prompt_full" in cols


class TestAIDecisionLog:
    async def test_log_without_new_fields(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            d = _decision()
            await store.log(d)
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 1
        assert results[0].prompt_full is None
        assert results[0].context_snapshot is None
        assert results[0].model_params is None

    async def test_log_with_prompt_full(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            d = _decision(prompt_full="Analyze this VM and prioritize actions.")
            await store.log(d)
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 1
        assert results[0].prompt_full == "Analyze this VM and prioritize actions."

    async def test_log_with_context_snapshot(self) -> None:
        snapshot = json.dumps({
            "vm_info": {"os_family": "ubuntu", "pending_packages": 5},
            "available_actions": ["disk_cleanup", "patching"],
        })
        async with AIDecisionStore(":memory:") as store:
            d = _decision(context_snapshot=snapshot)
            await store.log(d)
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 1
        assert results[0].context_snapshot == snapshot
        parsed = json.loads(results[0].context_snapshot)
        assert parsed["vm_info"]["os_family"] == "ubuntu"

    async def test_log_with_model_params(self) -> None:
        params = json.dumps({"temperature": 0.1})
        async with AIDecisionStore(":memory:") as store:
            d = _decision(model_params=params)
            await store.log(d)
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 1
        assert results[0].model_params == params

    async def test_log_all_new_fields_round_trip(self) -> None:
        prompt = "You are an SRE agent. Prioritize maintenance for ubuntu vm."
        ctx = json.dumps({"vm_info": {"os_family": "ubuntu"}, "available_actions": ["patching"]})
        mp = json.dumps({"temperature": 0.0})
        async with AIDecisionStore(":memory:") as store:
            d = _decision(
                prompt_full=prompt,
                context_snapshot=ctx,
                model_params=mp,
            )
            await store.log(d)
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 1
        r = results[0]
        assert r.prompt_full == prompt
        assert r.context_snapshot == ctx
        assert r.model_params == mp

    async def test_multiple_decisions_ordered_newest_first(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.log(_decision(prompt_full="first"))
            await store.log(_decision(prompt_full="second"))
            results = await store.get_decisions(batch_id="batch-001")
        assert len(results) == 2
        # newest first
        assert results[0].prompt_full == "second"
        assert results[1].prompt_full == "first"

    async def test_new_fields_independent_of_other_filters(self) -> None:
        async with AIDecisionStore(":memory:") as store:
            await store.log(_decision(
                vm_id="dev/web-01",
                prompt_full="prompt-for-web-01",
                context_snapshot='{"vm": "web-01"}',
            ))
            await store.log(_decision(
                vm_id="dev/db-01",
                prompt_full="prompt-for-db-01",
                context_snapshot='{"vm": "db-01"}',
            ))
            web_results = await store.get_decisions(vm_id="dev/web-01")
            db_results = await store.get_decisions(vm_id="dev/db-01")
        assert len(web_results) == 1
        assert web_results[0].prompt_full == "prompt-for-web-01"
        assert len(db_results) == 1
        assert db_results[0].context_snapshot == '{"vm": "db-01"}'


class TestAIDecisionHashPrompt:
    def test_hash_prompt_is_16_chars(self) -> None:
        h = AIDecision.hash_prompt("hello world")
        assert len(h) == 16

    def test_hash_prompt_is_deterministic(self) -> None:
        h1 = AIDecision.hash_prompt("same prompt")
        h2 = AIDecision.hash_prompt("same prompt")
        assert h1 == h2

    def test_hash_prompt_differs_for_different_prompts(self) -> None:
        h1 = AIDecision.hash_prompt("prompt A")
        h2 = AIDecision.hash_prompt("prompt B")
        assert h1 != h2
