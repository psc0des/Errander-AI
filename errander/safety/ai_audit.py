"""Per-LLM-decision audit store (finding #3.4).

Every call to LLMClient.complete() that influences a maintenance decision
is logged here: model, base URL, prompt template ID, SHA-256 prompt hash,
raw response, latency, token counts, and outcome (success/fallback/error).

Schema is created by migration 0011 in errander/safety/migrations.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from errander.safety.migrations import run_migrations

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO ai_decisions
    (batch_id, vm_id, decision_type, model, base_url,
     prompt_template_id, prompt_hash, response_raw, outcome,
     latency_ms, prompt_tokens, completion_tokens, timestamp,
     prompt_full, context_snapshot, model_params)
VALUES (:batch_id, :vm_id, :decision_type, :model, :base_url,
        :prompt_template_id, :prompt_hash, :response_raw, :outcome,
        :latency_ms, :prompt_tokens, :completion_tokens, :timestamp,
        :prompt_full, :context_snapshot, :model_params)
"""

_SELECT_SQL = """
SELECT batch_id, vm_id, decision_type, model, base_url,
       prompt_template_id, prompt_hash, response_raw, outcome,
       latency_ms, prompt_tokens, completion_tokens, timestamp,
       prompt_full, context_snapshot, model_params, id
FROM ai_decisions
"""


@dataclass
class AIDecision:
    """Record of a single LLM decision call.

    Attributes:
        batch_id: Batch run this decision belongs to.
        decision_type: What kind of decision (prioritize_actions, generate_report, analyze_failure).
        model: Model ID used.
        base_url: Endpoint base URL.
        prompt_template_id: Identifies the prompt template used (e.g. 'prioritize_v1').
        prompt_hash: SHA-256 of the rendered prompt — lets you detect prompt drift.
        outcome: 'success', 'fallback', 'error', 'timeout'.
        vm_id: Target VM (if applicable).
        response_raw: Raw JSON string returned by the LLM (None on failure).
        latency_ms: Round-trip latency in milliseconds (None on failure).
        prompt_tokens: Tokens in prompt (None if unavailable).
        completion_tokens: Tokens in completion (None if unavailable).
        timestamp: When this decision was made.
    """

    batch_id: str
    decision_type: str
    model: str
    base_url: str
    prompt_template_id: str
    prompt_hash: str
    outcome: str
    vm_id: str | None = None
    response_raw: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    prompt_full: str | None = None
    context_snapshot: str | None = None
    model_params: str | None = None
    decision_id: int | None = None

    @staticmethod
    def hash_prompt(prompt: str) -> str:
        """Return the first 16 hex chars of SHA-256(prompt)."""
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]


class AIDecisionStore:
    """Async database-backed store for per-LLM-decision audit records.

    Usage::

        db = AsyncDatabase("postgresql://errander:errander@localhost/errander")
        async with AIDecisionStore(db) as store:
            await store.log(decision)
            decisions = await store.get_decisions(batch_id="run-123")

    For testing, use AsyncDatabase(":memory:").
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        """Apply all pending schema migrations (including ai_decisions table)."""
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> AIDecisionStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def log(self, decision: AIDecision) -> None:
        """Write a decision record. Best-effort — never raises on DB error."""
        params = {
            "batch_id": decision.batch_id,
            "vm_id": decision.vm_id,
            "decision_type": decision.decision_type,
            "model": decision.model,
            "base_url": decision.base_url,
            "prompt_template_id": decision.prompt_template_id,
            "prompt_hash": decision.prompt_hash,
            "response_raw": decision.response_raw,
            "outcome": decision.outcome,
            "latency_ms": decision.latency_ms,
            "prompt_tokens": decision.prompt_tokens,
            "completion_tokens": decision.completion_tokens,
            "timestamp": decision.timestamp.isoformat(),
            "prompt_full": decision.prompt_full,
            "context_snapshot": decision.context_snapshot,
            "model_params": decision.model_params,
        }
        for attempt in (1, 2):
            try:
                async with self._db.begin() as conn:
                    await conn.execute(text(_INSERT_SQL), params)
                return
            except SQLAlchemyError as exc:
                if attempt == 1:
                    await asyncio.sleep(0.05)
                    continue
                logger.warning("AI decision audit write failed: %s", exc)
                return

    async def get_decisions(
        self,
        batch_id: str | None = None,
        vm_id: str | None = None,
        decision_type: str | None = None,
        limit: int = 50,
    ) -> list[AIDecision]:
        """Query AI decision records with optional filters."""
        clauses: list[str] = []
        params_dict: dict[str, object] = {}

        if batch_id is not None:
            clauses.append("batch_id = :batch_id")
            params_dict["batch_id"] = batch_id
        if vm_id is not None:
            clauses.append("vm_id = :vm_id")
            params_dict["vm_id"] = vm_id
        if decision_type is not None:
            clauses.append("decision_type = :decision_type")
            params_dict["decision_type"] = decision_type

        params_dict["limit"] = limit

        query = _SELECT_SQL
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC, id DESC LIMIT :limit"

        async with self._db.begin() as conn:
            result = await conn.execute(text(query), params_dict)
            rows = result.fetchall()
        return [_row_to_decision(row) for row in rows]

    async def get_decision_by_id(self, decision_id: int) -> AIDecision | None:
        """Return a single AI decision by its primary key, or None if not found."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(f"{_SELECT_SQL} WHERE id = :id LIMIT 1"),
                {"id": decision_id},
            )
            rows = result.fetchall()
        return _row_to_decision(rows[0]) if rows else None


def _row_to_decision(row: Any) -> AIDecision:
    return AIDecision(
        batch_id=str(row[0]),
        vm_id=str(row[1]) if row[1] is not None else None,
        decision_type=str(row[2]),
        model=str(row[3]),
        base_url=str(row[4]),
        prompt_template_id=str(row[5]),
        prompt_hash=str(row[6]),
        response_raw=str(row[7]) if row[7] is not None else None,
        outcome=str(row[8]),
        latency_ms=float(str(row[9])) if row[9] is not None else None,
        prompt_tokens=int(str(row[10])) if row[10] is not None else None,
        completion_tokens=int(str(row[11])) if row[11] is not None else None,
        timestamp=datetime.fromisoformat(str(row[12])),
        prompt_full=str(row[13]) if row[13] is not None else None,
        context_snapshot=str(row[14]) if row[14] is not None else None,
        model_params=str(row[15]) if row[15] is not None else None,
        decision_id=int(str(row[16])) if row[16] is not None else None,
    )
