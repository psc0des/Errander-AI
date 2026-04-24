"""DB-backed overrides for settings and inventory.

Two tables:
- settings_overrides: LLM/approval settings editable at runtime via the UI.
  Precedence: env vars > DB overrides > YAML > defaults.
- inventory_overrides: disable/enable YAML VMs, or add ad-hoc VMs via the UI.

All writes are append-safe (upsert). Reads decrypt secrets transparently.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import aiosqlite

from errander.integrations.secrets import SecretsManager

logger = logging.getLogger(__name__)

_CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'ui',
    note TEXT DEFAULT ''
)
"""

_CREATE_INVENTORY_SQL = """
CREATE TABLE IF NOT EXISTS inventory_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env_name TEXT NOT NULL,
    vm_name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('yaml_override', 'db_addition')),
    disabled INTEGER NOT NULL DEFAULT 0,
    host TEXT,
    ssh_user TEXT,
    ssh_key_path TEXT,
    os_family TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'ui',
    note TEXT DEFAULT '',
    UNIQUE(env_name, vm_name)
)
"""


class OverridesStore:
    """Async SQLite store for UI-managed settings and inventory overrides."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open DB and create tables if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_SETTINGS_SQL)
        await self._db.execute(_CREATE_INVENTORY_SQL)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> OverridesStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ── Settings overrides ───────────────────────────────────────────────────

    async def get_settings_overrides(self) -> dict[str, str]:
        """Return all settings overrides as {key: decrypted_value}."""
        assert self._db is not None
        sm = SecretsManager(require_key=False)
        async with self._db.execute(
            "SELECT key, value FROM settings_overrides"
        ) as cur:
            rows = await cur.fetchall()
        return {row[0]: sm.decrypt_if_needed(row[1]) for row in rows}

    async def get_settings_overrides_raw(self) -> list[dict[str, object]]:
        """Return all rows with metadata (for the settings UI)."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT key, value, is_secret, updated_at, updated_by, note FROM settings_overrides"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "key": r[0],
                "value": r[1],
                "is_secret": bool(r[2]),
                "updated_at": r[3],
                "updated_by": r[4],
                "note": r[5],
            }
            for r in rows
        ]

    async def set_setting_override(
        self,
        key: str,
        value: str,
        is_secret: bool = False,
        updated_by: str = "ui",
        note: str = "",
    ) -> None:
        """Upsert a settings override. Encrypts value if is_secret."""
        assert self._db is not None
        stored_value = value
        if is_secret:
            sm = SecretsManager()
            stored_value = sm.encrypt(value)
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            """
            INSERT INTO settings_overrides (key, value, is_secret, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                is_secret=excluded.is_secret,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by,
                note=excluded.note
            """,
            (key, stored_value, int(is_secret), now, updated_by, note),
        )
        await self._db.commit()

    async def delete_setting_override(self, key: str) -> None:
        """Remove a settings override (reverts to YAML/env/default)."""
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM settings_overrides WHERE key = ?", (key,)
        )
        await self._db.commit()

    # ── Inventory overrides ──────────────────────────────────────────────────

    async def get_inventory_overrides(self, env_name: str) -> list[dict[str, object]]:
        """Return all inventory override rows for an environment."""
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT env_name, vm_name, source, disabled, host, ssh_user,
                   ssh_key_path, os_family, updated_at, updated_by, note
            FROM inventory_overrides
            WHERE env_name = ?
            """,
            (env_name,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "env_name": r[0],
                "vm_name": r[1],
                "source": r[2],
                "disabled": bool(r[3]),
                "host": r[4],
                "ssh_user": r[5],
                "ssh_key_path": r[6],
                "os_family": r[7],
                "updated_at": r[8],
                "updated_by": r[9],
                "note": r[10],
            }
            for r in rows
        ]

    async def get_all_inventory_overrides(self) -> list[dict[str, object]]:
        """Return all inventory override rows across all environments."""
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT env_name, vm_name, source, disabled, host, ssh_user,
                   ssh_key_path, os_family, updated_at, updated_by, note
            FROM inventory_overrides
            ORDER BY env_name, vm_name
            """
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "env_name": r[0],
                "vm_name": r[1],
                "source": r[2],
                "disabled": bool(r[3]),
                "host": r[4],
                "ssh_user": r[5],
                "ssh_key_path": r[6],
                "os_family": r[7],
                "updated_at": r[8],
                "updated_by": r[9],
                "note": r[10],
            }
            for r in rows
        ]

    async def upsert_inventory_override(
        self,
        env_name: str,
        vm_name: str,
        source: str,
        disabled: bool = False,
        host: str | None = None,
        ssh_user: str | None = None,
        ssh_key_path: str | None = None,
        os_family: str | None = None,
        updated_by: str = "ui",
        note: str = "",
    ) -> None:
        """Insert or update an inventory override row."""
        assert self._db is not None
        now = datetime.now(tz=UTC).isoformat()
        await self._db.execute(
            """
            INSERT INTO inventory_overrides
                (env_name, vm_name, source, disabled, host, ssh_user,
                 ssh_key_path, os_family, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(env_name, vm_name) DO UPDATE SET
                source=excluded.source,
                disabled=excluded.disabled,
                host=excluded.host,
                ssh_user=excluded.ssh_user,
                ssh_key_path=excluded.ssh_key_path,
                os_family=excluded.os_family,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by,
                note=excluded.note
            """,
            (
                env_name, vm_name, source, int(disabled), host,
                ssh_user, ssh_key_path, os_family, now, updated_by, note,
            ),
        )
        await self._db.commit()

    async def delete_inventory_override(self, env_name: str, vm_name: str) -> None:
        """Delete an inventory override row."""
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM inventory_overrides WHERE env_name = ? AND vm_name = ?",
            (env_name, vm_name),
        )
        await self._db.commit()
