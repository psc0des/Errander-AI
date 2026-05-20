"""Batch lifecycle models.

Tracks the status and metadata of a maintenance batch run through its
full lifecycle: RUNNING → terminal state (COMPLETED, COMPLETED_WITH_FAILURES,
ABORTED, NEEDS_OPERATOR_REVIEW).

Separate from models/reports.py (which holds the rendered Slack report data)
so the persistence layer has a stable import target with no LLM/render deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BatchStatus(StrEnum):
    """Lifecycle status of a batch run stored in the batches table."""

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    ABORTED = "aborted"
    NEEDS_OPERATOR_REVIEW = "needs_operator_review"


@dataclass(frozen=True)
class BatchRecord:
    """A row from the batches table.

    All timestamps are ISO-8601 strings (SQLite TEXT affinity).
    finished_at is None while the batch is still RUNNING.
    """

    id: str
    env_name: str
    status: BatchStatus
    started_at: str
    finished_at: str | None
    dry_run: bool
    vm_count: int
    error: str | None
