"""Tests for ChatStore — dashboard chat conversation storage (Plan B).

Mirrors test_approval_store.py's fixture pattern. No race-safety contracts
to lock in here (chat has no approval semantics) — coverage is create/
append/order, per-user thread scoping, and ownership-checked delete.
"""

from __future__ import annotations

import pytest_asyncio

from errander.safety.chat_store import ChatStore


@pytest_asyncio.fixture
async def store() -> ChatStore:
    from tests.conftest import make_test_db
    s = ChatStore(make_test_db())
    await s.initialize()
    return s


async def test_create_thread_generates_server_side_id(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    assert thread.thread_id
    assert thread.user_id == "alice"
    assert thread.title == "New conversation"


async def test_create_thread_with_custom_title(store: ChatStore) -> None:
    thread = await store.create_thread("alice", title="Why is web-02 slow?")
    assert thread.title == "Why is web-02 slow?"


async def test_get_thread_returns_none_for_unknown_id(store: ChatStore) -> None:
    assert await store.get_thread("does-not-exist") is None


async def test_append_message_preserves_order(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    await store.append_message(thread.thread_id, role="user", content="first")
    await store.append_message(thread.thread_id, role="assistant", content="second")
    await store.append_message(thread.thread_id, role="user", content="third")

    messages = await store.get_messages(thread.thread_id)

    assert [m.content for m in messages] == ["first", "second", "third"]
    assert [m.role for m in messages] == ["user", "assistant", "user"]


async def test_append_message_stores_findings_and_risk_level(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    msg = await store.append_message(
        thread.thread_id, role="assistant", content="answer",
        findings_json='[{"text": "x", "evidence": []}]',
        recommendations_json='["do y"]',
        risk_level="low",
    )
    assert msg.findings_json == '[{"text": "x", "evidence": []}]'
    assert msg.recommendations_json == '["do y"]'
    assert msg.risk_level == "low"
    assert msg.message_id is not None


async def test_append_message_bumps_thread_updated_at(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    created = thread.updated_at
    await store.append_message(thread.thread_id, role="user", content="hi")
    refreshed = await store.get_thread(thread.thread_id)
    assert refreshed is not None
    assert refreshed.updated_at >= created


async def test_get_messages_respects_limit(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    for i in range(5):
        await store.append_message(thread.thread_id, role="user", content=f"msg-{i}")

    messages = await store.get_messages(thread.thread_id, limit=2)
    assert len(messages) == 2
    assert [m.content for m in messages] == ["msg-0", "msg-1"]


async def test_list_threads_scoped_per_user(store: ChatStore) -> None:
    thread_a = await store.create_thread("alice")
    await store.create_thread("bob")

    alice_threads = await store.list_threads("alice")

    assert [t.thread_id for t in alice_threads] == [thread_a.thread_id]


async def test_list_threads_orders_by_recency(store: ChatStore) -> None:
    older = await store.create_thread("alice", title="older")
    newer = await store.create_thread("alice", title="newer")
    # Touch "older" so it becomes the most recently updated.
    await store.append_message(older.thread_id, role="user", content="bump")

    threads = await store.list_threads("alice")

    assert threads[0].thread_id == older.thread_id
    assert threads[1].thread_id == newer.thread_id


async def test_list_threads_respects_limit(store: ChatStore) -> None:
    for _ in range(5):
        await store.create_thread("alice")

    threads = await store.list_threads("alice", limit=2)
    assert len(threads) == 2


async def test_delete_thread_removes_it_and_its_messages(store: ChatStore) -> None:
    thread = await store.create_thread("alice")
    await store.append_message(thread.thread_id, role="user", content="hi")

    deleted = await store.delete_thread(thread.thread_id, "alice")

    assert deleted is True
    assert await store.get_thread(thread.thread_id) is None
    assert await store.get_messages(thread.thread_id) == []


async def test_delete_thread_rejects_wrong_owner(store: ChatStore) -> None:
    thread = await store.create_thread("alice")

    deleted = await store.delete_thread(thread.thread_id, "bob")

    assert deleted is False
    assert await store.get_thread(thread.thread_id) is not None


async def test_delete_thread_unknown_id_returns_false(store: ChatStore) -> None:
    assert await store.delete_thread("does-not-exist", "alice") is False
