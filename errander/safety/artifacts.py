"""ArtifactStore — external blob storage for oversized graph state fields.

Subgraph state fields that exceed 4 KB when serialized (primarily
``patch_output`` from PatchingGraphState) are stored here instead of in
LangGraph checkpoint state, keeping checkpoint rows small and fast.

Migration #6 creates the ``artifacts`` table.

Each artifact is identified by a unique ``artifact_id`` (UUID). The calling
subgraph stores the ``artifact_id`` in its TypedDict state (a short string,
< 40 bytes) and calls ``retrieve()`` when it needs the blob for logging or
reporting.

Retention: blobs are retained for the duration of the batch and pruned by
``purge_before()`` after reporting is complete. The caller is responsible for
calling ``purge_before()`` — the store does not auto-prune.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)


class ArtifactStore:
    """Read/write the ``artifacts`` table.

    Args:
        db: AsyncDatabase shared with the caller.  Caller owns the lifecycle.
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def store(
        self,
        *,
        batch_id: str,
        vm_id: str,
        artifact_kind: str,
        content: str,
    ) -> str:
        """Persist *content* and return the generated artifact_id.

        Args:
            batch_id: Owning batch (for purge and audit correlation).
            vm_id: Owning VM (for display).
            artifact_kind: Logical name — e.g. ``"patch_output"``,
                ``"rotation_output"``.
            content: The raw string blob to store.

        Returns:
            A UUID4 artifact_id string.
        """
        artifact_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO artifacts
                    (id, batch_id, vm_id, artifact_kind, content, created_at)
                VALUES (:id, :batch_id, :vm_id, :artifact_kind, :content, :created_at)
                """),
                {
                    "id": artifact_id,
                    "batch_id": batch_id,
                    "vm_id": vm_id,
                    "artifact_kind": artifact_kind,
                    "content": content,
                    "created_at": now,
                },
            )
        logger.debug(
            "ArtifactStore: stored %s/%s/%s → %s (%d bytes)",
            batch_id, vm_id, artifact_kind, artifact_id[:8], len(content),
        )
        return artifact_id

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def retrieve(self, artifact_id: str) -> str | None:
        """Return the stored blob content, or None if not found."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT content FROM artifacts WHERE id = :id"),
                {"id": artifact_id},
            )
            row = result.fetchone()
        return str(row[0]) if row is not None else None

    async def retrieve_by_kind(
        self,
        batch_id: str,
        vm_id: str,
        artifact_kind: str,
    ) -> list[str]:
        """Return all blobs of *artifact_kind* for (batch_id, vm_id).

        Ordered oldest-first.  Typically returns one entry per subgraph run.
        """
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT content FROM artifacts "
                    "WHERE batch_id = :batch_id AND vm_id = :vm_id AND artifact_kind = :kind "
                    "ORDER BY created_at"
                ),
                {"batch_id": batch_id, "vm_id": vm_id, "kind": artifact_kind},
            )
            rows = result.fetchall()
        return [str(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def purge_before(self, cutoff_iso: str) -> int:
        """Delete artifacts created before *cutoff_iso* (ISO-8601 string).

        Returns the number of rows deleted.
        """
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM artifacts WHERE created_at < :cutoff"),
                {"cutoff": cutoff_iso},
            )
            deleted = result.rowcount
        if deleted:
            logger.info("ArtifactStore: purged %d artifacts older than %s", deleted, cutoff_iso)
        return deleted
