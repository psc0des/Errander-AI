"""Tests for the user/group/session stores (R2: web-only approval RBAC)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from errander.db.core import AsyncDatabase
from errander.safety.user_store import (
    PERM_DECIDE_APPROVALS,
    PERM_MANAGE_SETTINGS,
    PERM_MANAGE_USERS,
    SessionStore,
    UserStore,
    hash_password,
    verify_password,
)
from tests.conftest import make_test_db


@pytest.fixture
async def db():
    database = make_test_db()
    yield database
    await database.close()


@pytest.fixture
async def user_store(db: AsyncDatabase) -> UserStore:
    return UserStore(db)


@pytest.fixture
async def session_store(db: AsyncDatabase, user_store: UserStore) -> SessionStore:
    return SessionStore(db, user_store)


class TestPasswordHashing:
    def test_round_trip(self) -> None:
        stored = hash_password("hunter2-but-better")
        assert stored.startswith("scrypt$")
        assert verify_password("hunter2-but-better", stored)

    def test_wrong_password_rejected(self) -> None:
        stored = hash_password("correct")
        assert not verify_password("incorrect", stored)

    def test_unique_salts(self) -> None:
        assert hash_password("same") != hash_password("same")

    def test_malformed_stored_hash_rejected(self) -> None:
        assert not verify_password("anything", "not-a-hash")
        assert not verify_password("anything", "md5$x$y$z$a$b")
        assert not verify_password("anything", "")


class TestUserStore:
    async def test_create_and_get_user(self, user_store: UserStore) -> None:
        await user_store.create_user(
            "sarathy", "pw-123", groups=["admin"], actor="cli:test",
        )
        user = await user_store.get_user("sarathy")
        assert user is not None
        assert user.username == "sarathy"
        assert user.groups == ["admin"]
        assert user.has_permission(PERM_DECIDE_APPROVALS)
        assert user.has_permission(PERM_MANAGE_USERS)
        assert user.has_permission(PERM_MANAGE_SETTINGS)
        assert user.group_granting(PERM_DECIDE_APPROVALS) == "admin"
        assert user.created_by == "cli:test"

    async def test_reader_has_no_permissions(self, user_store: UserStore) -> None:
        await user_store.create_user("viewer", "pw", groups=["reader"], actor="cli:test")
        user = await user_store.get_user("viewer")
        assert user is not None
        assert user.groups == ["reader"]
        assert not user.has_permission(PERM_DECIDE_APPROVALS)
        assert user.group_granting(PERM_DECIDE_APPROVALS) is None

    async def test_duplicate_username_rejected(self, user_store: UserStore) -> None:
        await user_store.create_user("dup", "pw", groups=["reader"], actor="t")
        with pytest.raises(ValueError, match="already exists"):
            await user_store.create_user("dup", "pw2", groups=["admin"], actor="t")

    async def test_unknown_group_rejected(self, user_store: UserStore) -> None:
        with pytest.raises(ValueError, match="unknown group"):
            await user_store.create_user("x", "pw", groups=["superuser"], actor="t")

    async def test_empty_groups_rejected(self, user_store: UserStore) -> None:
        with pytest.raises(ValueError, match="at least one group"):
            await user_store.create_user("x", "pw", groups=[], actor="t")

    async def test_invalid_username_rejected(self, user_store: UserStore) -> None:
        with pytest.raises(ValueError, match="invalid username"):
            await user_store.create_user("bad name!", "pw", groups=["reader"], actor="t")

    async def test_verify_credentials(self, user_store: UserStore) -> None:
        await user_store.create_user("ops", "secret", groups=["admin"], actor="t")
        user = await user_store.verify_credentials("ops", "secret")
        assert user is not None and user.username == "ops"
        assert await user_store.verify_credentials("ops", "wrong") is None
        assert await user_store.verify_credentials("nobody", "secret") is None

    async def test_set_groups_takes_effect_on_next_read(self, user_store: UserStore) -> None:
        """Acceptance #4: membership changes need no restart — next read sees them."""
        await user_store.create_user("flux", "pw", groups=["admin"], actor="t")
        assert await user_store.set_groups("flux", ["reader"], actor="cli:boss")
        user = await user_store.get_user("flux")
        assert user is not None
        assert user.groups == ["reader"]
        assert not user.has_permission(PERM_DECIDE_APPROVALS)

    async def test_set_groups_unknown_user(self, user_store: UserStore) -> None:
        assert not await user_store.set_groups("ghost", ["reader"], actor="t")

    async def test_set_password(self, user_store: UserStore) -> None:
        await user_store.create_user("rot", "old", groups=["reader"], actor="t")
        assert await user_store.set_password("rot", "new")
        assert await user_store.verify_credentials("rot", "old") is None
        assert await user_store.verify_credentials("rot", "new") is not None

    async def test_delete_user_revokes_sessions(
        self, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        await user_store.create_user("leaver", "pw", groups=["admin"], actor="t")
        token = await session_store.create("leaver")
        assert await session_store.resolve(token) is not None
        assert await user_store.delete_user("leaver")
        # ON DELETE CASCADE removed the session row.
        assert await session_store.resolve(token) is None
        assert await user_store.get_user("leaver") is None

    async def test_count_and_list(self, user_store: UserStore) -> None:
        assert await user_store.count_users() == 0
        await user_store.create_user("a", "pw", groups=["admin"], actor="t")
        await user_store.create_user("b", "pw", groups=["reader"], actor="t")
        assert await user_store.count_users() == 2
        users = await user_store.list_users()
        assert [u.username for u in users] == ["a", "b"]

    async def test_third_group_is_plain_inserts(
        self, db: AsyncDatabase, user_store: UserStore,
    ) -> None:
        """§8a schema requirement: adding an 'approver' group (decide but not
        manage users) must be data, not a migration."""
        async with db.begin() as conn:
            await conn.execute(text(
                "INSERT INTO groups (name, description) VALUES ('approver', 'decides only')"
            ))
            await conn.execute(text(
                "INSERT INTO group_permissions (group_name, permission) "
                "VALUES ('approver', 'decide_approvals')"
            ))
        await user_store.create_user("appr", "pw", groups=["approver"], actor="t")
        user = await user_store.get_user("appr")
        assert user is not None
        assert user.has_permission(PERM_DECIDE_APPROVALS)
        assert not user.has_permission(PERM_MANAGE_USERS)
        assert user.group_granting(PERM_DECIDE_APPROVALS) == "approver"


class TestSessionStore:
    async def test_create_resolve_destroy(
        self, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        await user_store.create_user("sess", "pw", groups=["admin"], actor="t")
        token = await session_store.create("sess")
        user = await session_store.resolve(token)
        assert user is not None and user.username == "sess"
        await session_store.destroy(token)
        assert await session_store.resolve(token) is None

    async def test_invalid_token(self, session_store: SessionStore) -> None:
        assert await session_store.resolve("") is None
        assert await session_store.resolve("garbage-token") is None

    async def test_expired_session_rejected_and_purged(
        self, db: AsyncDatabase, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        await user_store.create_user("late", "pw", groups=["reader"], actor="t")
        token = await session_store.create("late", ttl_seconds=3600)
        # Force the row into the past.
        past = (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
        async with db.begin() as conn:
            await conn.execute(text("UPDATE sessions SET expires_at = :p"), {"p": past})
        assert await session_store.resolve(token) is None

    async def test_purge_expired(
        self, db: AsyncDatabase, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        await user_store.create_user("p", "pw", groups=["reader"], actor="t")
        await session_store.create("p")
        await session_store.create("p")
        past = (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
        async with db.begin() as conn:
            await conn.execute(text("UPDATE sessions SET expires_at = :p"), {"p": past})
        assert await session_store.purge_expired() == 2

    async def test_raw_token_not_stored(
        self, db: AsyncDatabase, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        """The DB stores a hash — a leaked dump can't be replayed as a cookie."""
        await user_store.create_user("h", "pw", groups=["reader"], actor="t")
        token = await session_store.create("h")
        async with db.begin() as conn:
            result = await conn.execute(text("SELECT token_hash FROM sessions"))
            stored = [str(r[0]) for r in result.fetchall()]
        assert token not in stored

    async def test_groups_resolved_fresh_per_request(
        self, user_store: UserStore, session_store: SessionStore,
    ) -> None:
        """Demoting a user invalidates their decide power mid-session."""
        await user_store.create_user("demote", "pw", groups=["admin"], actor="t")
        token = await session_store.create("demote")
        user = await session_store.resolve(token)
        assert user is not None and user.has_permission(PERM_DECIDE_APPROVALS)
        await user_store.set_groups("demote", ["reader"], actor="t")
        user = await session_store.resolve(token)
        assert user is not None and not user.has_permission(PERM_DECIDE_APPROVALS)
