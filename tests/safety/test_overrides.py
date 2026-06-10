"""Unit tests for OverridesStore — settings and inventory DB-backed overrides."""

from __future__ import annotations

import pytest
import pytest_asyncio

from errander.db.core import AsyncDatabase
from errander.safety.overrides import OverridesStore


@pytest_asyncio.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.sqlite")
    async with OverridesStore(AsyncDatabase(db_path)) as s:
        yield s


# ── Settings overrides ──────────────────────────────────────────────────────


class TestSettingsOverrides:
    async def test_empty_store_returns_empty_dict(self, store):
        result = await store.get_settings_overrides()
        assert result == {}

    async def test_set_and_get_plain_setting(self, store):
        await store.set_setting_override("ERRANDER_LLM_MODEL", "gpt-4o")
        result = await store.get_settings_overrides()
        assert result["ERRANDER_LLM_MODEL"] == "gpt-4o"

    async def test_upsert_updates_existing_value(self, store):
        await store.set_setting_override("ERRANDER_LLM_MODEL", "gpt-4o")
        await store.set_setting_override("ERRANDER_LLM_MODEL", "claude-3-5-sonnet")
        result = await store.get_settings_overrides()
        assert result["ERRANDER_LLM_MODEL"] == "claude-3-5-sonnet"

    async def test_delete_setting_removes_it(self, store):
        await store.set_setting_override("ERRANDER_LLM_MODEL", "gpt-4o")
        await store.delete_setting_override("ERRANDER_LLM_MODEL")
        result = await store.get_settings_overrides()
        assert "ERRANDER_LLM_MODEL" not in result

    async def test_delete_nonexistent_is_noop(self, store):
        await store.delete_setting_override("ERRANDER_NONEXISTENT_KEY")
        result = await store.get_settings_overrides()
        assert result == {}

    async def test_set_secret_encrypts_at_rest(self, store, monkeypatch):
        monkeypatch.setenv("ERRANDER_SECRETS_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdA==")
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        monkeypatch.setenv("ERRANDER_SECRETS_KEY", key.decode())

        await store.set_setting_override("ERRANDER_LLM_API_KEY", "sk-secret", is_secret=True)

        # Raw row should contain enc:v1: prefix
        rows = await store.get_settings_overrides_raw()
        assert rows[0]["is_secret"] is True
        assert str(rows[0]["value"]).startswith("enc:v1:")

        # get_settings_overrides() should decrypt transparently
        plain = await store.get_settings_overrides()
        assert plain["ERRANDER_LLM_API_KEY"] == "sk-secret"

    async def test_multiple_settings(self, store):
        await store.set_setting_override("ERRANDER_LLM_MODEL", "qwen3")
        await store.set_setting_override("ERRANDER_LLM_TIMEOUT", "120")
        result = await store.get_settings_overrides()
        assert result["ERRANDER_LLM_MODEL"] == "qwen3"
        assert result["ERRANDER_LLM_TIMEOUT"] == "120"

    async def test_raw_includes_metadata(self, store):
        await store.set_setting_override(
            "ERRANDER_LLM_MODEL", "gpt-4o", updated_by="admin", note="switched for cost"
        )
        rows = await store.get_settings_overrides_raw()
        assert len(rows) == 1
        row = rows[0]
        assert row["key"] == "ERRANDER_LLM_MODEL"
        assert row["updated_by"] == "admin"
        assert row["note"] == "switched for cost"
        assert row["updated_at"] != ""


# ── Inventory overrides ──────────────────────────────────────────────────────


class TestInventoryOverrides:
    async def test_empty_returns_empty_list(self, store):
        result = await store.get_inventory_overrides("production")
        assert result == []

    async def test_upsert_yaml_override_disabled(self, store):
        await store.upsert_inventory_override(
            env_name="production",
            vm_name="web-01",
            source="yaml_override",
            disabled=True,
        )
        rows = await store.get_inventory_overrides("production")
        assert len(rows) == 1
        assert rows[0]["vm_name"] == "web-01"
        assert rows[0]["disabled"] is True
        assert rows[0]["source"] == "yaml_override"

    async def test_upsert_db_addition(self, store):
        await store.upsert_inventory_override(
            env_name="staging",
            vm_name="temp-worker",
            source="db_addition",
            host="10.0.1.99",
            ssh_user="ubuntu",
            ssh_key_path="/keys/staging.pem",
            os_family="ubuntu",
        )
        rows = await store.get_inventory_overrides("staging")
        assert len(rows) == 1
        row = rows[0]
        assert row["vm_name"] == "temp-worker"
        assert row["host"] == "10.0.1.99"
        assert row["disabled"] is False

    async def test_upsert_updates_existing(self, store):
        await store.upsert_inventory_override(
            "production", "web-01", "yaml_override", disabled=True
        )
        await store.upsert_inventory_override(
            "production", "web-01", "yaml_override", disabled=False
        )
        rows = await store.get_inventory_overrides("production")
        assert len(rows) == 1
        assert rows[0]["disabled"] is False

    async def test_delete_inventory_override(self, store):
        await store.upsert_inventory_override(
            "production", "temp-01", "db_addition", host="10.0.2.1"
        )
        await store.delete_inventory_override("production", "temp-01")
        rows = await store.get_inventory_overrides("production")
        assert rows == []

    async def test_overrides_scoped_by_env(self, store):
        await store.upsert_inventory_override("production", "web-01", "yaml_override")
        await store.upsert_inventory_override("staging", "web-01", "db_addition", host="10.0.1.1")

        prod_rows = await store.get_inventory_overrides("production")
        stg_rows = await store.get_inventory_overrides("staging")
        assert len(prod_rows) == 1
        assert len(stg_rows) == 1
        assert prod_rows[0]["source"] == "yaml_override"
        assert stg_rows[0]["source"] == "db_addition"

    async def test_get_all_inventory_overrides(self, store):
        await store.upsert_inventory_override("production", "web-01", "yaml_override")
        await store.upsert_inventory_override("staging", "db-01", "db_addition", host="10.0.1.2")

        all_rows = await store.get_all_inventory_overrides()
        env_names = {r["env_name"] for r in all_rows}
        assert "production" in env_names
        assert "staging" in env_names

    async def test_invalid_source_raises(self, store):
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            await store.upsert_inventory_override(
                "production", "web-01", "invalid_source"
            )


# ── Context manager ─────────────────────────────────────────────────────────


class TestContextManager:
    async def test_aenter_aexit(self, tmp_path):
        db_path = str(tmp_path / "ctx.sqlite")
        async with OverridesStore(AsyncDatabase(db_path)) as s:
            await s.set_setting_override("ERRANDER_LLM_MODEL", "test")
            result = await s.get_settings_overrides()
            assert result["ERRANDER_LLM_MODEL"] == "test"

    async def test_close_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "idem.sqlite")
        s = OverridesStore(AsyncDatabase(db_path))
        await s.initialize()
        await s.close()
        await s.close()  # second close must not raise
