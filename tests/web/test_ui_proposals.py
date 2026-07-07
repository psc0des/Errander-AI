"""Web UI tests for the agent proposal queue (fable-plan Phase 1).

Drives the real aiohttp app (auth + CSRF middleware + handlers) through
aiohttp's TestClient. Locks in:
- decisions require an authenticated user with decide_approvals (RBAC)
- the queue renders the AGENT-ORIGINATED badge + evidence chain
- approve/reject/snooze record the named user and group
- review-only proposals never become executable
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
from collections.abc import AsyncIterator

import pytest
from aiohttp.test_utils import TestClient, TestServer

from errander.db.core import AsyncDatabase
from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
    ProposalStatus,
)
from errander.safety.proposal_store import ProposalStore
from errander.safety.user_store import SessionStore, UserStore
from errander.web.ui import CSRF_SECRET_KEY, build_ui_app
from tests.conftest import make_test_db


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabase]:
    database = make_test_db()
    yield database
    await database.close()


@pytest.fixture
async def stores(db: AsyncDatabase) -> tuple[UserStore, SessionStore, ProposalStore]:
    user_store = UserStore(db)
    session_store = SessionStore(db, user_store)
    proposal_store = ProposalStore(db)
    return user_store, session_store, proposal_store


@pytest.fixture
async def client(
    stores: tuple[UserStore, SessionStore, ProposalStore],
) -> AsyncIterator[TestClient]:
    user_store, session_store, proposal_store = stores
    app = build_ui_app(
        proposal_store=proposal_store,
        user_store=user_store,
        session_store=session_store,
        loopback_bind=True,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


def _csrf_pair(client: TestClient) -> dict[str, str]:
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


async def _seed_proposal(
    store: ProposalStore, *, kind: ProposalKind = ProposalKind.ACTION,
) -> AgentProposal:
    proposal = AgentProposal(
        env_name="prod",
        vm_id="web-01",
        kind=kind,
        action_type="disk_cleanup" if kind == ProposalKind.ACTION else "",
        signal_kind="disk_growth" if kind == ProposalKind.ACTION else "drift",
        probe_id="probe-77",
        evidence=[ProposalEvidence(
            source="probe:disk_history",
            check="disk growth trend for /var",
            observation="/var at 91% used, +12.0% over window",
        )],
        confidence="high",
    )
    stored, _ = await store.create_or_refresh(proposal)
    return stored


class TestQueuePage:
    async def test_page_renders_badge_and_evidence(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        _, _, proposal_store = stores
        await _seed_proposal(proposal_store)
        resp = await client.get("/ui/proposals")
        assert resp.status == 200
        html = await resp.text()
        assert "AGENT-ORIGINATED" in html
        assert "disk_cleanup" in html
        assert "/var at 91% used" in html  # evidence chain rendered
        assert "probe:disk_history" in html  # provenance visible

    async def test_empty_queue_renders(self, client: TestClient) -> None:
        resp = await client.get("/ui/proposals")
        assert resp.status == 200
        assert "No pending proposals" in await resp.text()


class TestRBAC:
    async def test_zero_users_decide_forbidden(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        """No user accounts = no authorized deciders — fail closed."""
        _, _, proposal_store = stores
        p = await _seed_proposal(proposal_store)
        resp = await client.post(
            f"/ui/proposals/{p.proposal_id}/approve", data=_csrf_pair(client),
        )
        assert resp.status == 403
        loaded = await proposal_store.get(p.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.PENDING

    async def test_reader_cannot_decide(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        user_store, _, proposal_store = stores
        await user_store.create_user("bob", "pw-bob-123", groups=["reader"], actor="t")
        p = await _seed_proposal(proposal_store)
        await _login(client, "bob", "pw-bob-123")
        resp = await client.post(
            f"/ui/proposals/{p.proposal_id}/approve", data=_csrf_pair(client),
        )
        assert resp.status == 403
        loaded = await proposal_store.get(p.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.PENDING


class TestDecisions:
    async def test_admin_approve_records_identity(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        user_store, _, proposal_store = stores
        await user_store.create_user("alice", "pw-alice-123", groups=["admin"], actor="t")
        p = await _seed_proposal(proposal_store)
        await _login(client, "alice", "pw-alice-123")
        resp = await client.post(
            f"/ui/proposals/{p.proposal_id}/approve",
            data=_csrf_pair(client), allow_redirects=False,
        )
        assert resp.status == 302
        loaded = await proposal_store.get(p.proposal_id)
        assert loaded is not None
        assert loaded.status == ProposalStatus.APPROVED
        assert loaded.decided_by == "ui:alice"
        assert loaded.decided_by_group == "admin"

    async def test_admin_reject(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        user_store, _, proposal_store = stores
        await user_store.create_user("alice", "pw-alice-123", groups=["admin"], actor="t")
        p = await _seed_proposal(proposal_store)
        await _login(client, "alice", "pw-alice-123")
        resp = await client.post(
            f"/ui/proposals/{p.proposal_id}/reject",
            data=_csrf_pair(client), allow_redirects=False,
        )
        assert resp.status == 302
        loaded = await proposal_store.get(p.proposal_id)
        assert loaded is not None and loaded.status == ProposalStatus.REJECTED

    async def test_admin_snooze_with_days(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        user_store, _, proposal_store = stores
        await user_store.create_user("alice", "pw-alice-123", groups=["admin"], actor="t")
        p = await _seed_proposal(proposal_store)
        await _login(client, "alice", "pw-alice-123")
        resp = await client.post(
            f"/ui/proposals/{p.proposal_id}/snooze",
            data={"snooze_days": "7", **_csrf_pair(client)}, allow_redirects=False,
        )
        assert resp.status == 302
        loaded = await proposal_store.get(p.proposal_id)
        assert loaded is not None
        assert loaded.status == ProposalStatus.SNOOZED
        assert loaded.snoozed_until is not None

    async def test_approved_review_proposal_is_never_claimable(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        """Approving a review proposal acknowledges — nothing becomes executable."""
        user_store, _, proposal_store = stores
        await user_store.create_user("alice", "pw-alice-123", groups=["admin"], actor="t")
        p = await _seed_proposal(proposal_store, kind=ProposalKind.REVIEW)
        await _login(client, "alice", "pw-alice-123")
        await client.post(
            f"/ui/proposals/{p.proposal_id}/approve",
            data=_csrf_pair(client), allow_redirects=False,
        )
        assert await proposal_store.get_approved_unclaimed() == []
        assert await proposal_store.mark_execution_started(p.proposal_id) is False

    async def test_unknown_proposal_404(
        self, client: TestClient,
        stores: tuple[UserStore, SessionStore, ProposalStore],
    ) -> None:
        user_store, _, _ = stores
        await user_store.create_user("alice", "pw-alice-123", groups=["admin"], actor="t")
        await _login(client, "alice", "pw-alice-123")
        resp = await client.post(
            "/ui/proposals/nope/approve", data=_csrf_pair(client),
        )
        assert resp.status == 404
