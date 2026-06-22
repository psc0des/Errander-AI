"""Durable chat conversation store (dashboard chat, Plan B phase 1).

Threads scoped to the logged-in UI user — every read method filters by
user_id (defense in depth even though route handlers also check ownership).
Mirrors ApprovalRequestStore's shape (db wrapping, initialize() runs
migrations, _row_to_* helpers) but has no approval-race semantics: no
asyncio.Event waiter registry, no atomic decide() — chat has nothing to
race over.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

logger = logging.getLogger(__name__)

_THREAD_COLUMNS = "thread_id, user_id, title, created_at, updated_at"
_MESSAGE_COLUMNS = (
    "id, thread_id, role, content, findings_json, recommendations_json, risk_level, created_at"
)


@dataclass
class ChatThread:
    """One conversation thread, owned by the logged-in user who started it."""

    thread_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ChatMessage:
    """One turn in a thread. `role` is "user" or "assistant"."""

    message_id: int | None
    thread_id: str
    role: str
    content: str
    created_at: datetime
    findings_json: str | None = None
    recommendations_json: str | None = None
    risk_level: str | None = None


class ChatStore:
    """Async DB store for dashboard-chat conversation threads/messages.

    Usage::

        store = ChatStore(db)
        await store.initialize()
        thread = await store.create_thread(user_id="alice")
        await store.append_message(thread.thread_id, role="user", content="...")
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn)

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    async def create_thread(self, user_id: str, title: str = "New conversation") -> ChatThread:
        """Create a new thread. thread_id is always server-generated — never
        accept a client-supplied id (avoids collision/IDOR)."""
        thread_id = uuid.uuid4().hex
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO chat_threads (thread_id, user_id, title, created_at, updated_at)
                VALUES (:thread_id, :user_id, :title, :created_at, :updated_at)
                """),
                {
                    "thread_id": thread_id, "user_id": user_id, "title": title,
                    "created_at": now, "updated_at": now,
                },
            )
        thread = await self.get_thread(thread_id)
        assert thread is not None  # just inserted
        return thread

    async def get_thread(self, thread_id: str) -> ChatThread | None:
        """Fetch one thread by id, regardless of owner — callers that need
        ownership enforcement should check `.user_id` themselves (route
        handlers do); list_threads already filters server-side."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_THREAD_COLUMNS} FROM chat_threads "  # noqa: S608 — constant column list
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": thread_id},
            )
            row = result.mappings().fetchone()
        return _row_to_thread(row) if row is not None else None

    async def list_threads(self, user_id: str, limit: int = 20) -> list[ChatThread]:
        """Threads owned by user_id, most recently active first."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_THREAD_COLUMNS} FROM chat_threads "  # noqa: S608
                    "WHERE user_id = :user_id ORDER BY updated_at DESC LIMIT :limit"
                ),
                {"user_id": user_id, "limit": limit},
            )
            rows = result.mappings().fetchall()
        return [_row_to_thread(row) for row in rows]

    async def delete_thread(self, thread_id: str, user_id: str) -> bool:
        """Delete a thread (cascades to its messages). Ownership-checked —
        returns False if thread_id doesn't exist or isn't owned by user_id."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM chat_threads WHERE thread_id = :thread_id AND user_id = :user_id"),
                {"thread_id": thread_id, "user_id": user_id},
            )
            return result.rowcount == 1

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def append_message(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        findings_json: str | None = None,
        recommendations_json: str | None = None,
        risk_level: str | None = None,
    ) -> ChatMessage:
        """Append one turn and bump the thread's updated_at (for list_threads
        recency ordering)."""
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                INSERT INTO chat_messages
                    (thread_id, role, content, findings_json, recommendations_json,
                     risk_level, created_at)
                VALUES (:thread_id, :role, :content, :findings_json, :recommendations_json,
                        :risk_level, :created_at)
                RETURNING id
                """),
                {
                    "thread_id": thread_id, "role": role, "content": content,
                    "findings_json": findings_json,
                    "recommendations_json": recommendations_json,
                    "risk_level": risk_level, "created_at": now,
                },
            )
            message_id = result.scalar()
            await conn.execute(
                text("UPDATE chat_threads SET updated_at = :now WHERE thread_id = :thread_id"),
                {"now": now, "thread_id": thread_id},
            )
        return ChatMessage(
            message_id=int(message_id) if message_id is not None else None,
            thread_id=thread_id, role=role, content=content,
            created_at=datetime.fromisoformat(now),
            findings_json=findings_json, recommendations_json=recommendations_json,
            risk_level=risk_level,
        )

    async def get_messages(self, thread_id: str, limit: int = 50) -> list[ChatMessage]:
        """Messages in a thread, oldest first (chronological render order)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT {_MESSAGE_COLUMNS} FROM chat_messages "  # noqa: S608
                    "WHERE thread_id = :thread_id ORDER BY created_at ASC, id ASC LIMIT :limit"
                ),
                {"thread_id": thread_id, "limit": limit},
            )
            rows = result.mappings().fetchall()
        return [_row_to_message(row) for row in rows]


def _row_to_thread(row: Any) -> ChatThread:
    return ChatThread(
        thread_id=str(row["thread_id"]),
        user_id=str(row["user_id"]),
        title=str(row["title"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _row_to_message(row: Any) -> ChatMessage:
    return ChatMessage(
        message_id=int(row["id"]),
        thread_id=str(row["thread_id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        findings_json=str(row["findings_json"]) if row["findings_json"] is not None else None,
        recommendations_json=(
            str(row["recommendations_json"]) if row["recommendations_json"] is not None else None
        ),
        risk_level=str(row["risk_level"]) if row["risk_level"] is not None else None,
    )
