"""Per-kind drift baseline storage and comparison.

Each DriftCheck implementation (authorized_keys, sudoers, listening_ports,
scheduled_jobs) captures a resource snapshot, canonicalizes it, and stores it
here.  BaselineStore tracks the last `retention_captures` snapshots per
(vm_id, kind, scope_key) and produces unified diffs when content changes.

Schema is created by migration 0002 in errander/safety/migrations.py.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineCapture:
    """A single canonicalized snapshot of a monitored resource.

    Attributes:
        kind: Resource type ('sudoers', 'authorized_keys', 'listening_ports', 'scheduled_jobs').
        scope_key: Per-scope discriminator — username for authorized_keys, '' for others.
        content: Canonicalized text (comments stripped, sorted, normalized).
        metadata: Supplementary key/value pairs stored alongside but NOT hashed.
    """

    kind: str
    scope_key: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """SHA-256 hex digest of the canonicalized content."""
        return hashlib.sha256(self.content.encode()).hexdigest()


@dataclass(frozen=True)
class BaselineComparison:
    """Result of comparing a new capture against the stored baseline.

    Attributes:
        is_first_run: True when no prior baseline existed for this (vm, kind, scope).
        changed: True when content_hash differs from the previous capture.
        previous: The prior BaselineCapture, or None on first run.
        current: The newly captured snapshot (already saved).
        unified_diff: Rendered diff string; '' when unchanged or first run.
    """

    is_first_run: bool
    changed: bool
    previous: BaselineCapture | None
    current: BaselineCapture
    unified_diff: str


# ---------------------------------------------------------------------------
# DriftCheck protocol
# ---------------------------------------------------------------------------


class DriftCheck(Protocol):
    """Interface for per-kind drift checks."""

    kind: str

    async def capture(self, ssh: object, vm: object) -> list[BaselineCapture]:
        """Capture the current resource state."""
        ...


# ---------------------------------------------------------------------------
# BaselineStore
# ---------------------------------------------------------------------------


class BaselineStore:
    """Async database-backed store for per-kind drift baselines.

    The caller must have already run migrations so the vm_baselines table exists.

    Usage::

        db = AsyncDatabase("postgresql://errander:errander@localhost/errander")
        async with BaselineStore(db) as store:
            comparison = await store.compare_and_save("prod/web-01", capture)
            if comparison.changed:
                print(comparison.unified_diff)
    """

    def __init__(self, db: AsyncDatabase, retention_captures: int = 30) -> None:
        self._db = db
        self._retention = retention_captures

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> BaselineStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def latest(
        self,
        vm_id: str,
        kind: str,
        scope_key: str = "",
    ) -> BaselineCapture | None:
        """Return the most recent baseline for (vm_id, kind, scope_key), or None."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT baseline_kind, scope_key, content_blob, metadata
                FROM vm_baselines
                WHERE vm_id = :vm_id AND baseline_kind = :kind AND scope_key = :scope_key
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """),
                {"vm_id": vm_id, "kind": kind, "scope_key": scope_key},
            )
            row = result.fetchone()
        if row is None:
            return None

        meta: dict[str, str] = {}
        if row[3] is not None:
            try:
                parsed = json.loads(str(row[3]))
                if isinstance(parsed, dict):
                    meta = {str(k): str(v) for k, v in parsed.items()}
            except (json.JSONDecodeError, ValueError):
                pass

        return BaselineCapture(
            kind=str(row[0]),
            scope_key=str(row[1]),
            content=str(row[2]),
            metadata=meta,
        )

    async def save(self, vm_id: str, capture: BaselineCapture) -> None:
        """Persist a new baseline capture and prune old rows."""
        now = datetime.now(tz=UTC).isoformat()
        meta_json = json.dumps(capture.metadata, ensure_ascii=False)

        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO vm_baselines
                    (vm_id, baseline_kind, scope_key, captured_at, content_hash, content_blob, metadata)
                VALUES (:vm_id, :kind, :scope_key, :captured_at, :content_hash, :content_blob, :metadata)
                """),
                {
                    "vm_id": vm_id,
                    "kind": capture.kind,
                    "scope_key": capture.scope_key,
                    "captured_at": now,
                    "content_hash": capture.content_hash,
                    "content_blob": capture.content,
                    "metadata": meta_json,
                },
            )
            # Prune within the same transaction to keep retention consistent.
            await conn.execute(
                text("""
                DELETE FROM vm_baselines
                WHERE vm_id = :vm_id AND baseline_kind = :kind AND scope_key = :scope_key
                  AND id NOT IN (
                      SELECT id
                      FROM vm_baselines
                      WHERE vm_id = :vm_id AND baseline_kind = :kind AND scope_key = :scope_key
                      ORDER BY captured_at DESC, id DESC
                      LIMIT :retention
                  )
                """),
                {
                    "vm_id": vm_id,
                    "kind": capture.kind,
                    "scope_key": capture.scope_key,
                    "retention": self._retention,
                },
            )

    async def compare_and_save(
        self,
        vm_id: str,
        capture: BaselineCapture,
    ) -> BaselineComparison:
        """Compare capture against the latest stored baseline, then save it."""
        previous = await self.latest(vm_id, capture.kind, capture.scope_key)
        await self.save(vm_id, capture)

        if previous is None:
            return BaselineComparison(
                is_first_run=True,
                changed=False,
                previous=None,
                current=capture,
                unified_diff="",
            )

        if previous.content_hash == capture.content_hash:
            return BaselineComparison(
                is_first_run=False,
                changed=False,
                previous=previous,
                current=capture,
                unified_diff="",
            )

        diff_lines = list(
            difflib.unified_diff(
                previous.content.splitlines(keepends=True),
                capture.content.splitlines(keepends=True),
                fromfile=f"baseline/{capture.kind}:{capture.scope_key}",
                tofile=f"current/{capture.kind}:{capture.scope_key}",
            )
        )
        return BaselineComparison(
            is_first_run=False,
            changed=True,
            previous=previous,
            current=capture,
            unified_diff="".join(diff_lines),
        )
