"""RBAC tests for the web UI (R2: web-only approval).

Drives the real aiohttp app (auth middleware + CSRF middleware + handlers)
through aiohttp's TestClient against the shared test PostgreSQL.

Locks in the §8a acceptance criteria:
- decisions only via an authenticated session with decide_approvals
- reader-group users (and anonymous visitors) cannot decide — server-side
- decided_by / decided_by_group record the named user and group
- zero-users bootstrap mode: GETs open on loopback, mutations 403

Covers errander.web.ui — the production web UI process (R3 process split).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
from collections.abc import AsyncIterator

import pytest
from aiohttp.test_utils import TestClient, TestServer

from errander.db.core import AsyncDatabase
from errander.safety.approval_store import ApprovalRequestStore
from errander.safety.user_store import SessionStore, UserStore
from errander.web.ui import (
    CSRF_SECRET_KEY,
    build_ui_app,
)
from tests.conftest import make_test_db


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabase]:
    database = make_test_db()
    yield database
    await database.close()


@pytest.fixture
async def stores(db: AsyncDatabase) -> tuple[UserStore, SessionStore, ApprovalRequestStore]:
    user_store = UserStore(db)
    session_store = SessionStore(db, user_store)
    approval_store = ApprovalRequestStore(db)
    return user_store, session_store, approval_store


@pytest.fixture
async def client(
    stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
) -> AsyncIterator[TestClient]:
    user_store, session_store, approval_store = stores
    app = build_ui_app(
        approval_store=approval_store,
        user_store=user_store,
        session_store=session_store,
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


async def _seed_pending(approval_store: ApprovalRequestStore, batch_id: str) -> None:
    await approval_store.create(
        batch_id,
        env_name="prod",
        plan_id="plan-1",
        plan_hash="hash-1",
        report="2 packages on web-01",
        timeout_seconds=600,
    )


class TestZeroUsersBootstrapMode:
    async def test_get_pages_open_on_loopback(self, client: TestClient) -> None:
        resp = await client.get("/ui/approvals")
        assert resp.status == 200

    async def test_decide_post_forbidden(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """No user accounts = no authorized deciders — fail closed."""
        _, _, approval_store = stores
        await _seed_pending(approval_store, "b-zero")
        resp = await client.post(
            "/ui/approvals/b-zero/approve", data=_csrf_pair(client),
        )
        assert resp.status == 403
        request = await approval_store.get("b-zero")
        assert request is not None and request.status == "pending"

    async def test_nonloopback_bind_refuses_open_gets(
        self, stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """Open-GET bootstrap mode is loopback-only — a network bind with
        zero users serves nothing unauthenticated."""
        user_store, session_store, approval_store = stores
        app = build_ui_app(
            approval_store=approval_store,
            user_store=user_store,
            session_store=session_store,
            loopback_bind=False,
        )
        test_client = TestClient(TestServer(app))
        await test_client.start_server()
        try:
            resp = await test_client.get("/ui/approvals", allow_redirects=False)
            assert resp.status == 403
        finally:
            await test_client.close()


class TestAuthenticationFlow:
    async def test_login_redirect_carries_next(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        resp = await client.get("/ui/approvals", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"].startswith("/ui/login?next=")
        assert "approvals" in resp.headers["Location"]

    async def test_login_then_access(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        await _login(client, "op", "pw")
        resp = await client.get("/ui/approvals")
        assert resp.status == 200

    async def test_bad_credentials_rejected(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        resp = await client.post(
            "/ui/login",
            data={"username": "op", "password": "wrong", **_csrf_pair(client)},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert "err=1" in resp.headers["Location"]

    async def test_next_path_must_be_internal(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """Open-redirect guard: external next targets collapse to /ui."""
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        resp = await client.post(
            "/ui/login?next=https://evil.example",
            data={"username": "op", "password": "pw", **_csrf_pair(client)},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == "/ui"

    async def test_logout_revokes_session(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        await _login(client, "op", "pw")
        await client.get("/ui/logout", allow_redirects=False)
        resp = await client.get("/ui/approvals", allow_redirects=False)
        assert resp.status == 302  # back to login


class TestDecisionRBAC:
    async def test_admin_decide_records_user_and_group(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """Acceptance #2: named user AND group on every decision."""
        user_store, _, approval_store = stores
        await user_store.create_user("sarathy", "pw", groups=["admin"], actor="t")
        await _seed_pending(approval_store, "b-admin")
        await _login(client, "sarathy", "pw")
        resp = await client.post(
            "/ui/approvals/b-admin/approve", data=_csrf_pair(client),
            allow_redirects=False,
        )
        assert resp.status == 302
        request = await approval_store.get("b-admin")
        assert request is not None
        assert request.status == "approved"
        assert request.decided_by == "ui:sarathy"
        assert request.decided_by_group == "admin"

    async def test_reader_cannot_decide(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """Acceptance #3: enforced server-side, not by hiding buttons."""
        user_store, _, approval_store = stores
        await user_store.create_user("viewer", "pw", groups=["reader"], actor="t")
        await _seed_pending(approval_store, "b-reader")
        await _login(client, "viewer", "pw")
        # Reader can view the queue…
        page = await client.get("/ui/approvals")
        assert page.status == 200
        # …but cannot decide.
        resp = await client.post(
            "/ui/approvals/b-reader/approve", data=_csrf_pair(client),
        )
        assert resp.status == 403
        request = await approval_store.get("b-reader")
        assert request is not None and request.status == "pending"

    async def test_anonymous_cannot_decide(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, approval_store = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        await _seed_pending(approval_store, "b-anon")
        resp = await client.post(
            "/ui/approvals/b-anon/approve", data=_csrf_pair(client),
            allow_redirects=False,
        )
        assert resp.status == 302  # bounced to login, nothing decided
        request = await approval_store.get("b-anon")
        assert request is not None and request.status == "pending"

    async def test_reject_records_identity_too(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, approval_store = stores
        await user_store.create_user("sarathy", "pw", groups=["admin"], actor="t")
        await _seed_pending(approval_store, "b-rej")
        await _login(client, "sarathy", "pw")
        resp = await client.post(
            "/ui/approvals/b-rej/reject", data=_csrf_pair(client),
            allow_redirects=False,
        )
        assert resp.status == 302
        request = await approval_store.get("b-rej")
        assert request is not None
        assert request.status == "rejected"
        assert request.decided_by == "ui:sarathy"
        assert request.decided_by_group == "admin"

    async def test_demoted_admin_loses_decide_mid_session(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        """Acceptance #4: group changes take effect without restart."""
        user_store, _, approval_store = stores
        await user_store.create_user("fired", "pw", groups=["admin"], actor="t")
        await _seed_pending(approval_store, "b-demote")
        await _login(client, "fired", "pw")
        await user_store.set_groups("fired", ["reader"], actor="cli:boss")
        resp = await client.post(
            "/ui/approvals/b-demote/approve", data=_csrf_pair(client),
        )
        assert resp.status == 403


class TestSettingsRBAC:
    async def test_reader_cannot_post_settings(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("viewer", "pw", groups=["reader"], actor="t")
        await _login(client, "viewer", "pw")
        resp = await client.post("/ui/settings", data=_csrf_pair(client))
        assert resp.status == 403

    async def test_reader_cannot_toggle_inventory(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("viewer", "pw", groups=["reader"], actor="t")
        await _login(client, "viewer", "pw")
        resp = await client.post("/ui/inventory/toggle", data=_csrf_pair(client))
        assert resp.status == 403


class TestHygieneRBAC:
    async def test_signed_url_grants_nothing_without_session(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Acceptance #3 for hygiene: the signed URL locates, never authorizes."""
        from errander.integrations.signed_url import make_signed_token

        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", "test-secret-32-bytes-or-longer!!")
        user_store, _, _ = stores
        await user_store.create_user("op", "pw", groups=["admin"], actor="t")
        token = make_signed_token({"batch_id": "b1", "vm_id": "v1"}, ttl_seconds=600)
        resp = await client.get(
            f"/ui/docker-hygiene/approve?token={token}", allow_redirects=False,
        )
        assert resp.status == 302  # login first — token alone is not authority

    async def test_reader_cannot_open_hygiene_form(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ApprovalRequestStore],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from errander.integrations.signed_url import make_signed_token

        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", "test-secret-32-bytes-or-longer!!")
        user_store, _, _ = stores
        await user_store.create_user("viewer", "pw", groups=["reader"], actor="t")
        await _login(client, "viewer", "pw")
        token = make_signed_token({"batch_id": "b1", "vm_id": "v1"}, ttl_seconds=600)
        resp = await client.get(f"/ui/docker-hygiene/approve?token={token}")
        assert resp.status == 403
