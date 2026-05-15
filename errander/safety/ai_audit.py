"""Per-LLM-decision audit store (finding #3.4).

Every call to LLMClient.complete() that influences a maintenance decision
is logged here: model, base URL, prompt template ID, SHA-256 prompt hash,
raw response, latency, token counts, and outcome (success/fallback/error).

Schema is designed for PostgreSQL migration (TEXT types, ISO timestamps).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL,
    vm_id       TEXT,
    decision_type TEXT NOT NULL,
    model       TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response_raw TEXT,
    outcome     TEXT NOT NULL,
    latency_ms  REAL,
    prompt_tokens  INTEGER,
    completion_tokens INTEGER,
    timestamp   TEXT NOT NULL
)
"""

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_ai_batch ON ai_decisions (batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_ai_vm    ON ai_decisions (vm_id)",
    "CREATE INDEX IF NOT EXISTS idx_ai_ts    ON ai_decisions (timestamp DESC)",
]

_INSERT_SQL = """
INSERT INTO ai_decisions
    (batch_id, vm_id, decision_type, model, base_url,
     prompt_template_id, prompt_hash, response_raw, outcome,
     latency_ms, prompt_tokens, completion_tokens, timestamp)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_SQL = """
SELECT batch_id, vm_id, decision_type, model, base_url,
       prompt_template_id, prompt_hash, response_raw, outcome,
       latency_ms, prompt_tokens, completion_tokens, timestamp
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

    @staticmethod
    def hash_prompt(prompt: str) -> str:
        """Return the first 16 hex chars of SHA-256(prompt)."""
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]


class AIDecisionStore:
    """Async SQLite-backed store for per-LLM-decision audit records.

    Usage:
        async with AIDecisionStore("errander.sqlite") as store:
            await store.log(decision)
            decisions = await store.get_decisions(batch_id="run-123")

    For testing, use ":memory:" as the database path.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_TABLE_SQL)
        for idx in _CREATE_INDEX_SQL:
            await self._db.execute(idx)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> AIDecisionStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("AIDecisionStore not initialized")
        return self._db

    async def log(self, decision: AIDecision) -> None:
        """Write a decision record. Best-effort — never raises on DB error."""
        db = self._ensure_connected()
        params = (
            decision.batch_id,
            decision.vm_id,
            decision.decision_type,
            decision.model,
            decision.base_url,
            decision.prompt_template_id,
            decision.prompt_hash,
            decision.response_raw,
            decision.outcome,
            decision.latency_ms,
            decision.prompt_tokens,
            decision.completion_tokens,
            decision.timestamp.isoformat(),
        )
        for attempt in (1, 2):
            try:
                await db.execute(_INSERT_SQL, params)
                await db.commit()
                return
            except aiosqlite.Error as exc:
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
        db = self._ensure_connected()

        clauses: list[str] = []
        params: list[str | int] = []

        if batch_id is not None:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if vm_id is not None:
            clauses.append("vm_id = ?")
            params.append(vm_id)
        if decision_type is not None:
            clauses.append("decision_type = ?")
            params.append(decision_type)

        query = _SELECT_SQL
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(limit)

        rows = await db.execute_fetchall(query, params)
        return [_row_to_decision(row) for row in rows]


def _row_to_decision(row: aiosqlite.Row) -> AIDecision:
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
    )
