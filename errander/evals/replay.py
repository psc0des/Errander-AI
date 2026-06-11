"""Prompt replay eval — re-send stored LLM decisions to a candidate model (Phase 2).

Workflow:
  1. Query ai_decisions for decisions with prompt_full stored.
  2. Re-send each prompt_full to the candidate model.
  3. Run deterministic assertions on the response (schema, injection, unknown actions).
  4. Persist results in ai_eval_runs + ai_eval_results tables.
  5. Return an EvalRun summary for CLI/Web display.

All assertions are deterministic — no LLM needed to evaluate them.
Layer A only: reads from stores, never writes to target VMs.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from errander.safety.migrations import run_migrations

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
    from errander.integrations.llm import LLMClient
    from errander.safety.ai_audit import AIDecision, AIDecisionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Assertion rules — deterministic checks on replayed LLM output
# ---------------------------------------------------------------------------

# Shell metacharacters (same pattern as decisions._INJECTION_RE)
_INJECTION_RE = re.compile(r"[;&|`$(){}\\\n]|\.\./")

# Valid action type values (kept in sync with ActionType enum)
_KNOWN_ACTIONS: frozenset[str] = frozenset([
    "patching", "disk_cleanup", "log_rotation",
    "docker_hygiene", "backup_verify", "service_restart",
])

# Legacy action types that must not appear in new recommendations
_LEGACY_ACTIONS: frozenset[str] = frozenset(["docker_prune"])

_VALID_RECOMMENDATIONS: frozenset[str] = frozenset(["retry", "rollback", "escalate"])
_VALID_RISK_LEVELS: frozenset[str] = frozenset(["low", "medium", "high", "unknown"])


def check_assertions(decision_type: str, response_raw: str | None) -> list[str]:
    """Return a list of violation strings for this (decision_type, response) pair.

    An empty list means the response passes all checks.
    A None response is an error, not a violation — callers should handle it separately.
    """
    if response_raw is None:
        return ["no_response"]

    try:
        data: object = json.loads(response_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"parse_error:{exc}"]

    if not isinstance(data, dict):
        return ["schema:response_not_an_object"]

    if decision_type == "prioritize_actions":
        return _check_prioritize(data)
    if decision_type in ("failure_analysis", "analyze_failure"):
        return _check_failure_analysis(data)
    if decision_type in ("report", "generate_report"):
        return _check_report(data)
    # operator_assistant and any future decision type
    return _check_operator_assistant(data)


def _check_prioritize(data: dict[str, object]) -> list[str]:
    violations: list[str] = []
    action_types = data.get("action_types")
    if action_types is None:
        return ["schema:missing_action_types_field"]
    if not isinstance(action_types, list):
        return ["schema:action_types_not_a_list"]
    for item in action_types:
        if not isinstance(item, str):
            violations.append(f"schema:action_type_not_string:{item!r}")
            continue
        if _INJECTION_RE.search(item):
            violations.append(f"injection:{item!r}")
        elif item in _LEGACY_ACTIONS:
            violations.append(f"legacy_action:{item!r}")
        elif item not in _KNOWN_ACTIONS:
            violations.append(f"unknown_action:{item!r}")
    return violations


def _check_failure_analysis(data: dict[str, object]) -> list[str]:
    violations: list[str] = []
    rec = data.get("recommendation")
    if rec is None:
        violations.append("schema:missing_recommendation")
    elif rec not in _VALID_RECOMMENDATIONS:
        violations.append(f"invalid_recommendation:{rec!r}")
    if "reason" not in data:
        violations.append("schema:missing_reason")
    return violations


def _check_report(data: dict[str, object]) -> list[str]:
    violations: list[str] = []
    report = data.get("report")
    if report is None:
        violations.append("schema:missing_report_field")
    elif not str(report).strip():
        violations.append("schema:empty_report")
    return violations


def _check_operator_assistant(data: dict[str, object]) -> list[str]:
    violations: list[str] = []
    for required in ("summary", "findings", "recommendations", "risk_level"):
        if required not in data:
            violations.append(f"schema:missing_{required}")
    risk = data.get("risk_level", "")
    if risk and risk not in _VALID_RISK_LEVELS:
        violations.append(f"invalid_risk_level:{risk!r}")
    return violations


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Result for one replayed ai_decisions entry."""

    run_id: str
    original_id: int | None
    decision_type: str
    model: str
    prompt_hash: str
    outcome: str  # 'pass', 'fail', 'error', 'skipped'
    violations: list[str] = field(default_factory=list)
    response_raw: str | None = None
    latency_ms: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class EvalRun:
    """Summary of one --ai-eval-replay invocation."""

    run_id: str
    model: str
    decision_type: str | None
    source_count: int
    pass_count: int
    fail_count: int
    error_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    results: list[EvalResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# EvalStore — async PostgreSQL persistence for runs and results
# ---------------------------------------------------------------------------

_INSERT_RUN_SQL = """
INSERT INTO ai_eval_runs
    (run_id, model, decision_type, source_count, pass_count, fail_count, error_count, timestamp)
VALUES (:run_id, :model, :decision_type, :source_count, :pass_count, :fail_count, :error_count, :timestamp)
"""

_INSERT_RESULT_SQL = """
INSERT INTO ai_eval_results
    (run_id, original_id, decision_type, model, prompt_hash,
     response_raw, outcome, violations, latency_ms, timestamp)
VALUES (:run_id, :original_id, :decision_type, :model, :prompt_hash,
        :response_raw, :outcome, :violations, :latency_ms, :timestamp)
"""

_SELECT_RUNS_SQL = """
SELECT run_id, model, decision_type, source_count, pass_count, fail_count, error_count, timestamp
FROM ai_eval_runs
ORDER BY timestamp DESC
LIMIT :limit
"""

_SELECT_RESULTS_SQL = """
SELECT run_id, original_id, decision_type, model, prompt_hash,
       response_raw, outcome, violations, latency_ms, timestamp
FROM ai_eval_results
WHERE run_id = :run_id
ORDER BY id ASC
"""


class EvalStore:
    """Async store for replay eval runs and per-decision results.

    Uses the same DB as the rest of the audit stores.
    Tables are created via migration 9.

    Usage::

        async with EvalStore(AsyncDatabase(":memory:")) as store:
            await store.save_run(run)
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> EvalStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def save_run(self, run: EvalRun) -> None:
        """Persist the EvalRun header and all its EvalResults."""
        async with self._db.begin() as conn:
            await conn.execute(
                text(_INSERT_RUN_SQL),
                {
                    "run_id": run.run_id,
                    "model": run.model,
                    "decision_type": run.decision_type,
                    "source_count": run.source_count,
                    "pass_count": run.pass_count,
                    "fail_count": run.fail_count,
                    "error_count": run.error_count,
                    "timestamp": run.timestamp.isoformat(),
                },
            )
            if run.results:
                await conn.execute(
                    text(_INSERT_RESULT_SQL),
                    [
                        {
                            "run_id": r.run_id,
                            "original_id": r.original_id,
                            "decision_type": r.decision_type,
                            "model": r.model,
                            "prompt_hash": r.prompt_hash,
                            "response_raw": r.response_raw,
                            "outcome": r.outcome,
                            "violations": json.dumps(r.violations) if r.violations else None,
                            "latency_ms": r.latency_ms,
                            "timestamp": r.timestamp.isoformat(),
                        }
                        for r in run.results
                    ],
                )

    async def get_runs(self, limit: int = 20) -> list[EvalRun]:
        """Return recent eval runs, newest first."""
        async with self._db.begin() as conn:
            result = await conn.execute(text(_SELECT_RUNS_SQL), {"limit": limit})
            rows = result.fetchall()
        return [_row_to_run(r) for r in rows]

    async def get_results(self, run_id: str) -> list[EvalResult]:
        """Return all per-decision results for a run."""
        async with self._db.begin() as conn:
            result = await conn.execute(text(_SELECT_RESULTS_SQL), {"run_id": run_id})
            rows = result.fetchall()
        return [_row_to_result(r) for r in rows]


def _row_to_run(row: Any) -> EvalRun:
    return EvalRun(
        run_id=str(row[0]),
        model=str(row[1]),
        decision_type=str(row[2]) if row[2] is not None else None,
        source_count=int(str(row[3])),
        pass_count=int(str(row[4])),
        fail_count=int(str(row[5])),
        error_count=int(str(row[6])),
        timestamp=datetime.fromisoformat(str(row[7])),
    )


def _row_to_result(row: Any) -> EvalResult:
    violations_raw = row[7]
    violations: list[str] = json.loads(str(violations_raw)) if violations_raw else []
    return EvalResult(
        run_id=str(row[0]),
        original_id=int(str(row[1])) if row[1] is not None else None,
        decision_type=str(row[2]),
        model=str(row[3]),
        prompt_hash=str(row[4]),
        response_raw=str(row[5]) if row[5] is not None else None,
        outcome=str(row[6]),
        violations=violations,
        latency_ms=float(str(row[8])) if row[8] is not None else None,
        timestamp=datetime.fromisoformat(str(row[9])),
    )


# ---------------------------------------------------------------------------
# ReplayRunner — orchestrates the replay loop
# ---------------------------------------------------------------------------

async def run_replay(
    ai_store: AIDecisionStore,
    eval_store: EvalStore,
    candidate_client: LLMClient,
    decision_type: str | None = None,
    batch_id: str | None = None,
    limit: int = 20,
) -> EvalRun:
    """Replay stored LLM decisions against a candidate model.

    Queries ai_decisions for entries with prompt_full populated, re-sends each
    to candidate_client, runs assertions, stores results, and returns the EvalRun.
    """
    model_id = getattr(candidate_client, "_model", "unknown")
    run_id = str(uuid.uuid4())
    now = datetime.now(tz=UTC)

    decisions: list[AIDecision] = await ai_store.get_decisions(
        batch_id=batch_id,
        decision_type=decision_type,
        limit=limit,
    )

    results: list[EvalResult] = []
    pass_count = fail_count = error_count = 0

    for decision in decisions:
        if not decision.prompt_full:
            # Skip decisions where the full prompt was not captured
            results.append(EvalResult(
                run_id=run_id,
                original_id=decision.decision_id,
                decision_type=decision.decision_type,
                model=model_id,
                prompt_hash=decision.prompt_hash,
                outcome="skipped",
            ))
            continue

        prompt_hash = decision.prompt_hash
        t0 = time.monotonic()

        try:
            # Re-send the exact stored prompt to the candidate model
            from pydantic import BaseModel

            class _RawResponse(BaseModel):
                model_config = {"extra": "allow"}

            raw_result = await candidate_client.complete(
                decision.prompt_full, _RawResponse
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Replay error for decision %s: %s", decision.decision_id, exc)
            raw_result = None

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if raw_result is None:
            error_count += 1
            results.append(EvalResult(
                run_id=run_id,
                original_id=decision.decision_id,
                decision_type=decision.decision_type,
                model=model_id,
                prompt_hash=prompt_hash,
                outcome="error",
                latency_ms=latency_ms,
            ))
            continue

        # Serialize the response back to JSON for assertion checking
        response_raw = raw_result.model_dump_json()

        violations = check_assertions(decision.decision_type, response_raw)
        outcome = "pass" if not violations else "fail"
        if outcome == "pass":
            pass_count += 1
        else:
            fail_count += 1

        results.append(EvalResult(
            run_id=run_id,
            original_id=decision.decision_id,
            decision_type=decision.decision_type,
            model=model_id,
            prompt_hash=prompt_hash,
            response_raw=response_raw,
            outcome=outcome,
            violations=violations,
            latency_ms=latency_ms,
        ))

    run = EvalRun(
        run_id=run_id,
        model=model_id,
        decision_type=decision_type,
        source_count=len(decisions),
        pass_count=pass_count,
        fail_count=fail_count,
        error_count=error_count,
        timestamp=now,
        results=results,
    )
    await eval_store.save_run(run)
    return run
