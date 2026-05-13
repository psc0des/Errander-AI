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
from typing import Protocol

import aiosqlite

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
    """Interface for per-kind drift checks.

    Each concrete implementation captures one type of resource snapshot
    from a remote VM via SSH and returns one BaselineCapture per scope_key.
    """

    kind: str

    async def capture(self, ssh: object, vm: object) -> list[BaselineCapture]:
        """Capture the current resource state.

        Args:
            ssh: An open asyncssh connection or connection manager.
            vm: The VMTarget being scanned.

        Returns:
            One BaselineCapture per scope_key.  Empty list on unrecoverable error.
        """
        ...


# ---------------------------------------------------------------------------
# BaselineStore
# ---------------------------------------------------------------------------


class BaselineStore:
    """Async SQLite-backed store for per-kind drift baselines.

    The caller (AuditStore) must have already run migrations so the
    vm_baselines table exists before this store is used.

    Usage:
        async with BaselineStore("errander.sqlite") as store:
            comparison = await store.compare_and_save("prod/web-01", capture)
            if comparison.changed:
                print(comparison.unified_diff)
    """

    def __init__(self, db_path: str, retention_captures: int = 30) -> None:
        self._db_path = db_path
        self._retention = retention_captures
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database connection."""
        self._db = await aiosqlite.connect(self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> BaselineStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = (
                "BaselineStore not initialized — call initialize() or use as async context manager"
            )
            raise RuntimeError(msg)
        return self._db

    async def latest(
        self,
        vm_id: str,
        kind: str,
        scope_key: str = "",
    ) -> BaselineCapture | None:
        """Return the most recent baseline for (vm_id, kind, scope_key), or None.

        Args:
            vm_id: VM identifier.
            kind: Resource type.
            scope_key: Per-scope discriminator ('' for single-scope kinds).
        """
        db = self._ensure_connected()
        cursor = await db.execute(
            """
            SELECT baseline_kind, scope_key, content_blob, metadata
            FROM vm_baselines
            WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (vm_id, kind, scope_key),
        )
        row = await cursor.fetchone()
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
        """Persist a new baseline capture and prune old rows.

        Args:
            vm_id: VM identifier.
            capture: The snapshot to persist.
        """
        db = self._ensure_connected()
        now = datetime.now(tz=UTC).isoformat()
        meta_json = json.dumps(capture.metadata, ensure_ascii=False)

        await db.execute(
            """
            INSERT INTO vm_baselines
                (vm_id, baseline_kind, scope_key, captured_at, content_hash, content_blob, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vm_id,
                capture.kind,
                capture.scope_key,
                now,
                capture.content_hash,
                capture.content,
                meta_json,
            ),
        )
        await db.commit()
        await self._prune(db, vm_id, capture.kind, capture.scope_key)

    async def compare_and_save(
        self,
        vm_id: str,
        capture: BaselineCapture,
    ) -> BaselineComparison:
        """Compare capture against the latest stored baseline, then save it.

        Returns a BaselineComparison describing whether this is the first run,
        whether the content changed, and (if changed) a unified diff.

        Args:
            vm_id: VM identifier.
            capture: Freshly captured resource snapshot.
        """
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

    async def _prune(
        self,
        db: aiosqlite.Connection,
        vm_id: str,
        kind: str,
        scope_key: str,
    ) -> None:
        """Delete rows beyond retention_captures for (vm_id, kind, scope_key)."""
        await db.execute(
            """
            DELETE FROM vm_baselines
            WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM vm_baselines
                  WHERE vm_id = ? AND baseline_kind = ? AND scope_key = ?
                  ORDER BY captured_at DESC, id DESC
                  LIMIT ?
              )
            """,
            (vm_id, kind, scope_key, vm_id, kind, scope_key, self._retention),
        )
        await db.commit()
