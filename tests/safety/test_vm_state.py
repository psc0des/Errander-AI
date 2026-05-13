"""Tests for VMStateStore — per-VM mutable state (needs_reboot tracking)."""

from __future__ import annotations

import pytest

from errander.safety.audit import AuditStore
from errander.safety.vm_state import VMStateStore


async def _store() -> VMStateStore:
    """Return an initialised in-memory VMStateStore (migrations run via AuditStore)."""
    # AuditStore.initialize() runs all migrations, creating vm_state table.
    async with AuditStore(":memory:") as audit:
        db = audit._db
        assert db is not None
        store = VMStateStore(":memory:")
        # Share the same in-memory DB connection (same URI → same DB for :memory:)
        # by injecting the open connection directly.
        store._db = db
        return store


class TestVMStateStoreLifecycle:
    async def test_context_manager(self) -> None:
        async with AuditStore(":memory:"):
            pass  # ensures tables are created

    async def test_operations_without_init_raise(self) -> None:
        store = VMStateStore(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            await store.get("dev/web-01")

    async def test_double_close_is_safe(self) -> None:
        store = VMStateStore(":memory:")
        await store.initialize()
        await store.close()
        await store.close()


class TestVMStateStoreReboot:
    """Tests for needs_reboot lifecycle."""

    async def _make_store(self) -> VMStateStore:
        """Return a VMStateStore sharing connection with a migrated AuditStore."""
        store = VMStateStore(":memory:")
        await store.initialize()
        # Run migrations on the same connection so vm_state table exists
        from errander.safety.migrations import run_migrations
        assert store._db is not None
        await run_migrations(store._db)
        return store

    async def test_get_unknown_vm_returns_none(self) -> None:
        store = await self._make_store()
        result = await store.get("dev/unknown")
        assert result is None
        await store.close()

    async def test_set_and_get_needs_reboot(self) -> None:
        store = await self._make_store()
        await store.set_needs_reboot("dev/web-01", "packages require reboot", ("linux-image-6.1",))
        state = await store.get("dev/web-01")
        assert state is not None
        assert state.needs_reboot is True
        assert state.needs_reboot_reason == "packages require reboot"
        assert "linux-image-6.1" in state.needs_reboot_pkgs
        await store.close()

    async def test_clear_needs_reboot(self) -> None:
        store = await self._make_store()
        await store.set_needs_reboot("dev/web-01", "some reason")
        await store.clear_needs_reboot("dev/web-01")
        state = await store.get("dev/web-01")
        assert state is not None
        assert state.needs_reboot is False
        assert state.needs_reboot_reason is None
        assert state.needs_reboot_pkgs == ()
        await store.close()

    async def test_clear_nonexistent_vm_creates_row(self) -> None:
        store = await self._make_store()
        await store.clear_needs_reboot("dev/new-vm")
        state = await store.get("dev/new-vm")
        assert state is not None
        assert state.needs_reboot is False
        await store.close()

    async def test_list_needs_reboot_filters_correctly(self) -> None:
        store = await self._make_store()
        await store.set_needs_reboot("dev/web-01", "packages")
        await store.set_needs_reboot("dev/web-02", "packages")
        await store.clear_needs_reboot("dev/web-01")
        needs_reboot = await store.list_needs_reboot()
        ids = [s.vm_id for s in needs_reboot]
        assert "dev/web-02" in ids
        assert "dev/web-01" not in ids
        await store.close()

    async def test_set_needs_reboot_upserts_existing(self) -> None:
        store = await self._make_store()
        await store.set_needs_reboot("dev/web-01", "reason-1", ("pkg-a",))
        await store.set_needs_reboot("dev/web-01", "reason-2", ("pkg-b",))
        state = await store.get("dev/web-01")
        assert state is not None
        assert state.needs_reboot_reason == "reason-2"
        assert "pkg-b" in state.needs_reboot_pkgs
        await store.close()

    async def test_empty_pkgs_is_valid(self) -> None:
        store = await self._make_store()
        await store.set_needs_reboot("dev/web-01", "rhel needs-restarting")
        state = await store.get("dev/web-01")
        assert state is not None
        assert state.needs_reboot is True
        assert state.needs_reboot_pkgs == ()
        await store.close()
