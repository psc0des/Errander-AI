"""Tests for /ui/chat — dashboard chat (Plan B phase 1).

Drives the real aiohttp app (auth middleware + CSRF middleware + handlers)
through aiohttp's TestClient, mirroring test_rbac.py's pattern. Covers:
unauthenticated access redirects, CSRF enforcement (proves the existing
global middleware covers the new routes with zero per-handler code), the
engine being called and its answer rendered+persisted, and the
chat-enabled-but-not-configured / chat-disabled notices (never a 500).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from errander.db.core import AsyncDatabase
from errander.models.analysis import AssistantResponse, Finding
from errander.safety.approval_store import ApprovalRequestStore
from errander.safety.audit import AuditStore
from errander.safety.chat_store import ChatStore
from errander.safety.user_store import SessionStore, UserStore
from errander.web.ui import CSRF_SECRET_KEY, ChatEngineDeps, build_ui_app
from tests.conftest import make_test_db


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabase]:
    database = make_test_db()
    yield database
    await database.close()


@pytest.fixture
async def stores(
    db: AsyncDatabase,
) -> tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore]:
    user_store = UserStore(db)
    await user_store.initialize()
    session_store = SessionStore(db, user_store)
    approval_store = ApprovalRequestStore(db)
    await approval_store.initialize()
    audit_store = AuditStore(db, strict_mode=False)
    await audit_store.__aenter__()
    chat_store = ChatStore(db)
    await chat_store.initialize()
    return user_store, session_store, approval_store, audit_store, chat_store


def _make_engine_deps(*, llm_client: object = "configured") -> ChatEngineDeps:
    inv = MagicMock()
    inv.environments = {}
    settings = MagicMock(chat_max_history_turns=20, investigation_agent_enabled=False)
    return ChatEngineDeps(
        disk_history_store=MagicMock(),
        baseline_store=MagicMock(),
        inventory=inv,
        settings=settings,
        llm_client=MagicMock() if llm_client == "configured" else None,
    )


@pytest.fixture
async def client_with_chat(
    stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
) -> AsyncIterator[TestClient]:
    user_store, session_store, approval_store, audit_store, chat_store = stores
    app = build_ui_app(
        approval_store=approval_store, user_store=user_store, session_store=session_store,
        audit_store=audit_store, chat_store=chat_store, chat_engine_deps=_make_engine_deps(),
        loopback_bind=True,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


@pytest.fixture
async def client_chat_disabled(
    stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
) -> AsyncIterator[TestClient]:
    """chat_store=None — the ERRANDER_CHAT_ENABLED=false scenario."""
    user_store, session_store, approval_store, audit_store, _ = stores
    app = build_ui_app(
        approval_store=approval_store, user_store=user_store, session_store=session_store,
        audit_store=audit_store, loopback_bind=True,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


@pytest.fixture
async def client_chat_no_engine_deps(
    stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
) -> AsyncIterator[TestClient]:
    """chat_store present but chat_engine_deps=None — e.g. no inventory.yaml found."""
    user_store, session_store, approval_store, audit_store, chat_store = stores
    app = build_ui_app(
        approval_store=approval_store, user_store=user_store, session_store=session_store,
        audit_store=audit_store, chat_store=chat_store, chat_engine_deps=None,
        loopback_bind=True,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


def _csrf_pair(client: TestClient) -> dict[str, str]:
    """Forge a valid double-submit CSRF pair for the app under test."""
    secret: str = client.app[CSRF_SECRET_KEY]
    nonce = "test-nonce"
    token = hmac_mod.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    client.session.cookie_jar.update_cookies({"errander_csrf": nonce})
    return {"_csrf_token": token}


async def _login(client: TestClient, username: str, password: str) -> None:
    resp = await client.post(
        "/ui/login",
        data={"username": username, "password": password, **_csrf_pair(client)},
        allow_redirects=False,
    )
    assert resp.status == 302
    assert resp.headers["Location"] != "/ui/login?err=1", "login failed"


async def _seed_user(user_store: UserStore, username: str = "alice", password: str = "pw12345678") -> None:
    await user_store.create_user(username, password, groups=["reader"], actor="test")


# ---------------------------------------------------------------------------
# Disabled / not-configured — never a 500
# ---------------------------------------------------------------------------


class TestChatDisabled:
    async def test_disabled_renders_notice_not_500(self, client_chat_disabled: TestClient) -> None:
        resp = await client_chat_disabled.get("/ui/chat")
        assert resp.status == 200
        text = await resp.text()
        assert "disabled" in text.lower()

    async def test_thread_page_with_no_engine_deps_shows_notice(
        self,
        client_chat_no_engine_deps: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store, _, _, _, chat_store = stores
        await _seed_user(user_store)
        await _login(client_chat_no_engine_deps, "alice", "pw12345678")
        thread = await chat_store.create_thread("alice")

        resp = await client_chat_no_engine_deps.get(f"/ui/chat/{thread.thread_id}")
        assert resp.status == 200
        text = await resp.text()
        assert "no llm/inventory is configured" in text.lower()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestChatAuth:
    async def test_unauthenticated_get_redirects_to_login(self, client_with_chat: TestClient) -> None:
        resp = await client_with_chat.get("/ui/chat", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/ui/login"

    async def test_authenticated_get_renders(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store = stores[0]
        await _seed_user(user_store)
        await _login(client_with_chat, "alice", "pw12345678")

        resp = await client_with_chat.get("/ui/chat")
        assert resp.status == 200
        text = await resp.text()
        assert "New conversation" in text


# ---------------------------------------------------------------------------
# CSRF — proves the existing global middleware covers the new routes
# ---------------------------------------------------------------------------


class TestChatCSRF:
    async def test_new_thread_post_without_csrf_is_403(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store = stores[0]
        await _seed_user(user_store)
        await _login(client_with_chat, "alice", "pw12345678")

        resp = await client_with_chat.post("/ui/chat/new", data={})
        assert resp.status == 403

    async def test_new_thread_post_with_csrf_succeeds(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store = stores[0]
        await _seed_user(user_store)
        await _login(client_with_chat, "alice", "pw12345678")

        resp = await client_with_chat.post(
            "/ui/chat/new", data=_csrf_pair(client_with_chat), allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"].startswith("/ui/chat/")


# ---------------------------------------------------------------------------
# Engine call + render + persistence
# ---------------------------------------------------------------------------


class TestChatMessageFlow:
    async def test_message_calls_engine_and_renders_answer(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store, _, _, _, chat_store = stores
        await _seed_user(user_store)
        await _login(client_with_chat, "alice", "pw12345678")
        thread = await chat_store.create_thread("alice")

        fake_response = AssistantResponse(
            summary="web-02 is fine.",
            findings=[Finding(text="No recent failures", evidence=["audit_store"])],
            recommendations=["Keep monitoring"],
            risk_level="low",
        )
        with patch(
            "errander.agent.operator_assistant.OperatorAssistant.investigate",
            new=AsyncMock(return_value=fake_response),
        ):
            resp = await client_with_chat.post(
                f"/ui/chat/{thread.thread_id}/message",
                data={"message": "is web-02 healthy?", **_csrf_pair(client_with_chat)},
                allow_redirects=True,
            )

        assert resp.status == 200
        text = await resp.text()
        assert "web-02 is fine." in text
        assert "No recent failures" in text
        assert "Keep monitoring" in text

        messages = await chat_store.get_messages(thread.thread_id)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].content == "is web-02 healthy?"
        assert messages[1].content == "web-02 is fine."

    async def test_message_post_without_csrf_is_403(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store, _, _, _, chat_store = stores
        await _seed_user(user_store)
        await _login(client_with_chat, "alice", "pw12345678")
        thread = await chat_store.create_thread("alice")

        resp = await client_with_chat.post(
            f"/ui/chat/{thread.thread_id}/message", data={"message": "hi"},
        )
        assert resp.status == 403

    async def test_cannot_post_to_another_users_thread(
        self,
        client_with_chat: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore, AuditStore, ChatStore],
    ) -> None:
        user_store, _, _, _, chat_store = stores
        await _seed_user(user_store, "alice")
        await _seed_user(user_store, "bob")
        bob_thread = await chat_store.create_thread("bob")

        await _login(client_with_chat, "alice", "pw12345678")
        resp = await client_with_chat.post(
            f"/ui/chat/{bob_thread.thread_id}/message",
            data={"message": "hi", **_csrf_pair(client_with_chat)},
            allow_redirects=False,
        )
        # Ownership check fails closed — redirected away, never appended.
        assert resp.status == 302
        messages = await chat_store.get_messages(bob_thread.thread_id)
        assert messages == []
