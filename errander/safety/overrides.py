"""DB-backed overrides for settings and inventory.

Two tables (created by migration #10):
- settings_overrides: LLM/approval settings editable at runtime via the UI.
  Precedence: env vars > DB overrides > YAML > defaults.
- inventory_overrides: disable/enable YAML VMs, or add ad-hoc VMs via the UI.

All writes are append-safe (upsert). Reads decrypt secrets transparently.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from errander.integrations.secrets import SecretsManager

if TYPE_CHECKING:
    from errander.db.core import AsyncDatabase
logger = logging.getLogger(__name__)


class OverridesStore:
    """Async database store for UI-managed settings and inventory overrides."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def initialize(self) -> None:
        from errander.safety.migrations import run_migrations
        async with self._db.begin() as conn:
            await run_migrations(conn, self._db.dialect)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> OverridesStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ── Settings overrides ───────────────────────────────────────────────────

    async def get_settings_overrides(self) -> dict[str, str]:
        """Return all settings overrides as {key: decrypted_value}."""
        sm = SecretsManager(require_key=False)
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT key, value FROM settings_overrides")
            )
            rows = result.fetchall()
        return {row[0]: sm.decrypt_if_needed(row[1]) for row in rows}

    async def get_settings_overrides_raw(self) -> list[dict[str, object]]:
        """Return all rows with metadata (for the settings UI)."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("SELECT key, value, is_secret, updated_at, updated_by, note FROM settings_overrides")
            )
            rows = result.fetchall()
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
        stored_value = value
        if is_secret:
            sm = SecretsManager()
            stored_value = sm.encrypt(value)
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO settings_overrides (key, value, is_secret, updated_at, updated_by, note)
                VALUES (:key, :value, :is_secret, :updated_at, :updated_by, :note)
                ON CONFLICT(key) DO UPDATE SET
                    value=EXCLUDED.value,
                    is_secret=EXCLUDED.is_secret,
                    updated_at=EXCLUDED.updated_at,
                    updated_by=EXCLUDED.updated_by,
                    note=EXCLUDED.note
                """),
                {
                    "key": key,
                    "value": stored_value,
                    "is_secret": int(is_secret),
                    "updated_at": now,
                    "updated_by": updated_by,
                    "note": note,
                },
            )

    async def delete_setting_override(self, key: str) -> None:
        """Remove a settings override (reverts to YAML/env/default)."""
        async with self._db.begin() as conn:
            await conn.execute(
                text("DELETE FROM settings_overrides WHERE key = :key"),
                {"key": key},
            )

    # ── Inventory overrides ──────────────────────────────────────────────────

    async def get_inventory_overrides(self, env_name: str) -> list[dict[str, object]]:
        """Return all inventory override rows for an environment."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT env_name, vm_name, source, disabled, host, ssh_user,
                       ssh_key_path, os_family, updated_at, updated_by, note
                FROM inventory_overrides
                WHERE env_name = :env_name
                """),
                {"env_name": env_name},
            )
            rows = result.fetchall()
        return [
            {
                "env_name": r[0], "vm_name": r[1], "source": r[2],
                "disabled": bool(r[3]), "host": r[4], "ssh_user": r[5],
                "ssh_key_path": r[6], "os_family": r[7],
                "updated_at": r[8], "updated_by": r[9], "note": r[10],
            }
            for r in rows
        ]

    async def get_all_inventory_overrides(self) -> list[dict[str, object]]:
        """Return all inventory override rows across all environments."""
        async with self._db.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT env_name, vm_name, source, disabled, host, ssh_user,
                       ssh_key_path, os_family, updated_at, updated_by, note
                FROM inventory_overrides
                ORDER BY env_name, vm_name
                """)
            )
            rows = result.fetchall()
        return [
            {
                "env_name": r[0], "vm_name": r[1], "source": r[2],
                "disabled": bool(r[3]), "host": r[4], "ssh_user": r[5],
                "ssh_key_path": r[6], "os_family": r[7],
                "updated_at": r[8], "updated_by": r[9], "note": r[10],
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
        now = datetime.now(tz=UTC).isoformat()
        async with self._db.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO inventory_overrides
                    (env_name, vm_name, source, disabled, host, ssh_user,
                     ssh_key_path, os_family, updated_at, updated_by, note)
                VALUES (:env_name, :vm_name, :source, :disabled, :host, :ssh_user,
                        :ssh_key_path, :os_family, :updated_at, :updated_by, :note)
                ON CONFLICT(env_name, vm_name) DO UPDATE SET
                    source=EXCLUDED.source,
                    disabled=EXCLUDED.disabled,
                    host=EXCLUDED.host,
                    ssh_user=EXCLUDED.ssh_user,
                    ssh_key_path=EXCLUDED.ssh_key_path,
                    os_family=EXCLUDED.os_family,
                    updated_at=EXCLUDED.updated_at,
                    updated_by=EXCLUDED.updated_by,
                    note=EXCLUDED.note
                """),
                {
                    "env_name": env_name, "vm_name": vm_name, "source": source,
                    "disabled": int(disabled), "host": host,
                    "ssh_user": ssh_user, "ssh_key_path": ssh_key_path,
                    "os_family": os_family, "updated_at": now,
                    "updated_by": updated_by, "note": note,
                },
            )

    async def delete_inventory_override(self, env_name: str, vm_name: str) -> None:
        """Delete an inventory override row."""
        async with self._db.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM inventory_overrides WHERE env_name = :env_name AND vm_name = :vm_name"
                ),
                {"env_name": env_name, "vm_name": vm_name},
            )
