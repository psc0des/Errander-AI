"""Agent lease — single-process enforcement for Errander-AI.

Prevents two agent processes from running simultaneously against the same
SQLite database, which would corrupt LangGraph checkpoints and produce
duplicate batch audit events.

The lease is a single row in the ``agent_lease`` table (migration #7).
On startup, the agent calls ``acquire()`` — it succeeds if the table is
empty or the existing lease has expired (last_heartbeat older than TTL).
The agent must call ``heartbeat()`` every ``heartbeat_interval_seconds``
to keep the lease alive.  On shutdown, ``release()`` deletes the row.

Design notes:
  - TTL default: 90 seconds.  An agent that crashes without releasing
    the lease will be evicted after 90 s of silence.
  - Heartbeat default: 30 seconds (TTL / 3 — keeps headroom).
  - NOT a distributed lock — SQLite is single-writer.  The lease is
    a safety net against accidental concurrent runs, not a cluster lock.
  - The table holds at most one row (PRIMARY KEY on a fixed ``id = 1``).
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

_LEASE_TTL_SECONDS = 90
_HEARTBEAT_INTERVAL_SECONDS = 30


class AgentLeaseError(RuntimeError):
    """Raised when the agent cannot acquire or renew the lease."""


class AgentLease:
    """Read/write the ``agent_lease`` table.

    Args:
        db: Open aiosqlite connection.  Caller owns the lifecycle.
        ttl_seconds: Seconds after which a silent lease is considered expired.
        pid: Process ID (defaults to ``os.getpid()``).
        hostname: Hostname (defaults to ``socket.gethostname()``).
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        ttl_seconds: int = _LEASE_TTL_SECONDS,
        pid: int | None = None,
        hostname: str | None = None,
    ) -> None:
        self._db = db
        self._ttl = ttl_seconds
        self._pid = pid if pid is not None else os.getpid()
        self._hostname = hostname if hostname is not None else socket.gethostname()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def acquire(self) -> None:
        """Acquire the agent lease.

        Succeeds if the table is empty or the existing lease has expired.

        Raises:
            AgentLeaseError: If another live process holds the lease.
        """
        now = datetime.now(tz=UTC)
        expiry_cutoff = (now - timedelta(seconds=self._ttl)).isoformat()

        # Delete any expired lease (idempotent — no-op if table is empty).
        await self._db.execute(
            "DELETE FROM agent_lease WHERE last_heartbeat < ?",
            (expiry_cutoff,),
        )
        await self._db.commit()

        # Check if a live lease exists.
        cursor = await self._db.execute("SELECT pid, hostname, last_heartbeat FROM agent_lease")
        row = await cursor.fetchone()
        if row is not None:
            pid, hostname, heartbeat = row
            raise AgentLeaseError(
                f"Another agent process holds the lease: "
                f"pid={pid} hostname={hostname} last_heartbeat={heartbeat}. "
                f"If that process is dead, wait {self._ttl}s for the lease to expire."
            )

        # Insert our lease (REPLACE handles the edge case of two simultaneous acquires).
        await self._db.execute(
            """
            INSERT OR REPLACE INTO agent_lease (id, pid, hostname, acquired_at, last_heartbeat)
            VALUES (1, ?, ?, ?, ?)
            """,
            (self._pid, self._hostname, now.isoformat(), now.isoformat()),
        )
        await self._db.commit()
        logger.info(
            "Agent lease acquired: pid=%d hostname=%s", self._pid, self._hostname,
        )

    async def heartbeat(self) -> None:
        """Renew the lease by updating last_heartbeat.

        Should be called every ``heartbeat_interval_seconds``.  If the row
        has been deleted (e.g., by an operator), this is a no-op — the next
        heartbeat cycle will re-detect the missing lease.
        """
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            "UPDATE agent_lease SET last_heartbeat = ? WHERE id = 1 AND pid = ?",
            (now, self._pid),
        )
        await self._db.commit()
        logger.debug("Agent lease heartbeat: pid=%d", self._pid)

    async def release(self) -> None:
        """Release the lease on clean shutdown.

        Only deletes the row if this process owns it (pid match).
        """
        await self._db.execute(
            "DELETE FROM agent_lease WHERE id = 1 AND pid = ?",
            (self._pid,),
        )
        await self._db.commit()
        logger.info("Agent lease released: pid=%d", self._pid)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    async def current_holder(self) -> dict[str, object] | None:
        """Return the current lease holder dict, or None if no lease exists."""
        cursor = await self._db.execute(
            "SELECT pid, hostname, acquired_at, last_heartbeat FROM agent_lease WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        pid, hostname, acquired_at, last_heartbeat = row
        return {
            "pid": int(str(pid)),
            "hostname": str(hostname),
            "acquired_at": str(acquired_at),
            "last_heartbeat": str(last_heartbeat),
        }

    async def is_expired(self) -> bool:
        """Return True if the current lease has expired (or no lease exists)."""
        holder = await self.current_holder()
        if holder is None:
            return True
        last_hb_str = str(holder["last_heartbeat"])
        try:
            last_hb = datetime.fromisoformat(last_hb_str)
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=UTC)
            age = (datetime.now(tz=UTC) - last_hb).total_seconds()
            return age > self._ttl
        except ValueError:
            return True
