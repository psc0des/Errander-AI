"""ArtifactStore — external blob storage for oversized graph state fields.

Subgraph state fields that exceed 4 KB when serialized (primarily
``patch_output`` from PatchingGraphState and ``prune_output`` from
DockerPruneGraphState) are stored here instead of in LangGraph checkpoint
state, keeping checkpoint rows small and fast.

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

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class ArtifactStore:
    """Read/write the ``artifacts`` table.

    Args:
        db: Open aiosqlite connection.  Caller owns the lifecycle.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
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
                ``"prune_output"``, ``"rotation_output"``.
            content: The raw string blob to store.

        Returns:
            A UUID4 artifact_id string.
        """
        artifact_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            """
            INSERT INTO artifacts
                (id, batch_id, vm_id, artifact_kind, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (artifact_id, batch_id, vm_id, artifact_kind, content, now),
        )
        await self._db.commit()
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
        cursor = await self._db.execute(
            "SELECT content FROM artifacts WHERE id = ?",
            (artifact_id,),
        )
        row = await cursor.fetchone()
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
        cursor = await self._db.execute(
            "SELECT content FROM artifacts "
            "WHERE batch_id = ? AND vm_id = ? AND artifact_kind = ? "
            "ORDER BY created_at",
            (batch_id, vm_id, artifact_kind),
        )
        rows = await cursor.fetchall()
        return [str(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def purge_before(self, cutoff_iso: str) -> int:
        """Delete artifacts created before *cutoff_iso* (ISO-8601 string).

        Returns the number of rows deleted.
        """
        cursor = await self._db.execute(
            "DELETE FROM artifacts WHERE created_at < ?",
            (cutoff_iso,),
        )
        await self._db.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("ArtifactStore: purged %d artifacts older than %s", deleted, cutoff_iso)
        return deleted
