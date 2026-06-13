"""User, group, and session stores (R2: web-only approval with RBAC).

Users are members of groups; groups carry permissions (``group_permissions``
rows seeded by migration #14). The v1 groups are ``admin`` (decide_approvals,
manage_users, manage_settings) and ``reader`` (no permission rows — viewing
is implicit for any authenticated user). Adding a third group later (e.g.
``approver``: can decide but not manage users) is plain INSERTs, never a
schema migration.

Passwords are hashed with stdlib :func:`hashlib.scrypt` (no extra
dependency). Sessions are DB rows keyed by the SHA-256 of the cookie token,
so they survive agent restarts and remain valid across processes (ready for
the R3 process split).

Audit logging is the caller's job: CLI user-management handlers and the
startup seed write USER_* audit events recording the acting identity —
acceptance criterion: group membership changes are themselves audit-logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase

logger = logging.getLogger(__name__)

#: Permission vocabulary (rows in group_permissions).
PERM_DECIDE_APPROVALS = "decide_approvals"
PERM_MANAGE_USERS = "manage_users"
PERM_MANAGE_SETTINGS = "manage_settings"

#: scrypt parameters — interactive-login grade (RFC 7914 recommendations).
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


@dataclass
class User:
    """An authenticated (or looked-up) user with resolved groups/permissions."""

    username: str
    groups: list[str] = field(default_factory=list)
    permissions: frozenset[str] = frozenset()
    created_at: datetime | None = None
    created_by: str = ""

    def has_permission(self, permission: str) -> bool:
        """True when any of the user's groups grants this permission."""
        return permission in self.permissions

    def group_granting(self, permission: str) -> str | None:
        """First group (alphabetical) that could grant this permission.

        Used to record ``decided_by_group`` on approval decisions. Resolution
        is heuristic-free in v1 because only ``admin`` carries permissions;
        with more groups this still answers "as which group did they act".
        """
        if not self.has_permission(permission):
            return None
        return self._permission_group_map.get(permission)

    # Populated by the store when resolving the user (group → permission map).
    _permission_group_map: dict[str, str] = field(default_factory=dict, repr=False)


def hash_password(password: str) -> str:
    """Hash a password as ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return "$".join((
        "scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    ))


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify of a password against a stored scrypt hash."""
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(), salt=salt,
            n=int(n_s), r=int(r_s), p=int(p_s), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


class UserStore:
    """Async DB store for users, group membership, and permissions."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        groups: list[str],
        actor: str,
    ) -> User:
        """Create a user and assign group memberships.

        Raises:
            ValueError: username exists, no groups given, or unknown group.
        """
        if not username or not username.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError(f"invalid username {username!r} (alphanumeric plus -_. only)")
        if not groups:
            raise ValueError("a user must belong to at least one group")
        await self._validate_groups(groups)
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                INSERT INTO users (username, password_hash, created_at, created_by)
                VALUES (:username, :password_hash, :created_at, :created_by)
                ON CONFLICT (username) DO NOTHING
                """),
                {
                    "username": username,
                    "password_hash": hash_password(password),
                    "created_at": now,
                    "created_by": actor,
                },
            )
            if result.rowcount != 1:
                raise ValueError(f"user {username!r} already exists")
            for group in groups:
                await conn.execute(
                    text("""
                    INSERT INTO user_groups (username, group_name, added_by, added_at)
                    VALUES (:username, :group_name, :added_by, :added_at)
                    ON CONFLICT (username, group_name) DO NOTHING
                    """),
                    {"username": username, "group_name": group, "added_by": actor, "added_at": now},
                )
        logger.info("User %s created by %s (groups: %s)", username, actor, ", ".join(groups))
        user = await self.get_user(username)
        assert user is not None  # just inserted
        return user

    async def set_password(self, username: str, password: str) -> bool:
        """Replace a user's password hash. Returns False for unknown users."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("UPDATE users SET password_hash = :h WHERE username = :u"),
                {"h": hash_password(password), "u": username},
            )
        return result.rowcount == 1

    async def set_groups(self, username: str, groups: list[str], *, actor: str) -> bool:
        """Replace a user's group memberships. Returns False for unknown users.

        Takes effect on the next request — the auth middleware resolves
        groups from the DB per request, no restart needed.
        """
        if not groups:
            raise ValueError("a user must belong to at least one group")
        await self._validate_groups(groups)
        if await self.get_user(username) is None:
            return False
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("DELETE FROM user_groups WHERE username = :u"), {"u": username},
            )
            for group in groups:
                await conn.execute(
                    text("""
                    INSERT INTO user_groups (username, group_name, added_by, added_at)
                    VALUES (:username, :group_name, :added_by, :added_at)
                    """),
                    {"username": username, "group_name": group, "added_by": actor, "added_at": now},
                )
        logger.info("User %s groups set to [%s] by %s", username, ", ".join(groups), actor)
        return True

    async def set_totp_secret(self, username: str, secret: str | None) -> bool:
        """Set (or clear, with ``None``) a user's TOTP secret. False if unknown."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("UPDATE users SET totp_secret = :s WHERE username = :u"),
                {"s": secret, "u": username},
            )
        return result.rowcount == 1

    async def delete_user(self, username: str) -> bool:
        """Delete a user (sessions/memberships cascade). False if unknown."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM users WHERE username = :u"), {"u": username},
            )
        deleted = result.rowcount == 1
        if deleted:
            logger.info("User %s deleted", username)
        return deleted

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_totp_secret(self, username: str) -> str | None:
        """Return the user's TOTP secret, or None if unset/unknown."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT totp_secret FROM users WHERE username = :u"),
                {"u": username},
            )
            row = result.fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    async def verify_credentials(self, username: str, password: str) -> User | None:
        """Validate a login attempt. Returns the resolved User or None."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT password_hash FROM users WHERE username = :u"),
                {"u": username},
            )
            row = result.fetchone()
        if row is None:
            # Burn comparable time so unknown-user vs bad-password is not
            # distinguishable by response latency.
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, str(row[0])):
            return None
        return await self.get_user(username)

    async def get_user(self, username: str) -> User | None:
        """Fetch one user with groups and permissions resolved."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT username, created_at, created_by FROM users WHERE username = :u"),
                {"u": username},
            )
            row = result.fetchone()
            if row is None:
                return None
            grp_result = await conn.execute(
                text("""
                SELECT ug.group_name, gp.permission
                FROM user_groups ug
                LEFT JOIN group_permissions gp ON gp.group_name = ug.group_name
                WHERE ug.username = :u
                ORDER BY ug.group_name
                """),
                {"u": username},
            )
            grp_rows = grp_result.fetchall()
        groups: list[str] = []
        permissions: set[str] = set()
        perm_group_map: dict[str, str] = {}
        for group_name, permission in grp_rows:
            if group_name not in groups:
                groups.append(str(group_name))
            if permission is not None:
                permissions.add(str(permission))
                perm_group_map.setdefault(str(permission), str(group_name))
        user = User(
            username=str(row[0]),
            groups=groups,
            permissions=frozenset(permissions),
            created_at=datetime.fromisoformat(str(row[1])) if row[1] else None,
            created_by=str(row[2] or ""),
        )
        user._permission_group_map = perm_group_map
        return user

    async def list_users(self) -> list[User]:
        """All users with groups resolved, alphabetical."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT username FROM users ORDER BY username"),
            )
            names = [str(r[0]) for r in result.fetchall()]
        users: list[User] = []
        for name in names:
            user = await self.get_user(name)
            if user is not None:
                users.append(user)
        return users

    async def count_users(self) -> int:
        """Number of user accounts (zero = bootstrap/legacy-open mode)."""
        async with self._db.begin() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM users"))
            count = result.scalar()
        return int(count or 0)

    async def list_groups(self) -> dict[str, list[str]]:
        """Group name → sorted permission list (for --user-list display)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT g.name, gp.permission
                FROM groups g LEFT JOIN group_permissions gp ON gp.group_name = g.name
                ORDER BY g.name, gp.permission
                """),
            )
            rows = result.fetchall()
        out: dict[str, list[str]] = {}
        for name, permission in rows:
            out.setdefault(str(name), [])
            if permission is not None:
                out[str(name)].append(str(permission))
        return out

    async def _validate_groups(self, groups: list[str]) -> None:
        known = set(await self.list_groups())
        unknown = [g for g in groups if g not in known]
        if unknown:
            raise ValueError(
                f"unknown group(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})"
            )


#: Pre-computed hash used to equalize timing for unknown usernames.
_DUMMY_HASH = hash_password("errander-timing-equalizer")


class SessionStore:
    """DB-backed login sessions. Cookie holds a random token; the DB stores
    its SHA-256, so a leaked DB dump cannot be replayed as a cookie."""

    #: Default session lifetime: 8 hours (matches the previous in-memory TTL).
    DEFAULT_TTL_SECONDS = 8 * 3600

    def __init__(self, db: AsyncDatabase, user_store: UserStore) -> None:
        self._db = db
        self._users = user_store

    async def create(self, username: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
        """Create a session row and return the raw cookie token."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(tz=UTC)
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO sessions (token_hash, username, created_at, expires_at)
                VALUES (:token_hash, :username, :created_at, :expires_at)
                """),
                {
                    "token_hash": _token_hash(token),
                    "username": username,
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                },
            )
        return token

    async def resolve(self, token: str) -> User | None:
        """Token → User with fresh groups/permissions; None if invalid/expired."""
        if not token:
            return None
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT username, expires_at FROM sessions WHERE token_hash = :h"),
                {"h": _token_hash(token)},
            )
            row = result.fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(str(row[1])) <= datetime.now(tz=UTC):
            await self.destroy(token)
            return None
        # Groups/permissions are re-read per request so membership changes
        # take effect without restart (acceptance criterion #4).
        return await self._users.get_user(str(row[0]))

    async def destroy(self, token: str) -> None:
        """Delete one session (logout)."""
        async with self._db.begin() as conn:
            await conn.execute(
                text("DELETE FROM sessions WHERE token_hash = :h"),
                {"h": _token_hash(token)},
            )

    async def purge_expired(self) -> int:
        """Delete expired session rows; returns how many were removed."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM sessions WHERE expires_at <= :now"),
                {"now": datetime.now(tz=UTC).isoformat()},
            )
        return int(result.rowcount or 0)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
