"""Tests for BaselineStore — per-kind drift baseline storage and comparison."""

from __future__ import annotations

from sqlalchemy import text

from errander.safety.baselines import BaselineCapture, BaselineStore
from errander.safety.migrations import run_migrations
from tests.conftest import make_test_db


async def _make_store(retention: int = 30) -> BaselineStore:
    db = make_test_db()
    async with db.begin() as conn:
        await run_migrations(conn)
    return BaselineStore(db, retention_captures=retention)


def _capture(
    content: str,
    kind: str = "sudoers",
    scope_key: str = "",
    metadata: dict[str, str] | None = None,
) -> BaselineCapture:
    return BaselineCapture(kind=kind, scope_key=scope_key, content=content, metadata=metadata or {})


class TestBaselineStoreLifecycle:
    async def test_context_manager(self) -> None:
        db = make_test_db()
        async with db.begin() as conn:
            await run_migrations(conn)
        async with BaselineStore(db) as store:
            assert store._db is db

    async def test_double_close_is_safe(self) -> None:
        store = await _make_store()
        await store.close()
        await store.close()


class TestBaselineStoreLatest:
    async def test_latest_returns_none_on_first_run(self) -> None:
        store = await _make_store()
        result = await store.latest("dev/web-01", "sudoers")
        assert result is None
        await store.close()

    async def test_latest_returns_most_recent(self) -> None:
        store = await _make_store()
        cap1 = _capture("root ALL=(ALL) ALL")
        cap2 = _capture("root ALL=(ALL) ALL\ndeploy ALL=(ALL) NOPASSWD:/usr/bin/systemctl")
        await store.save("dev/web-01", cap1)
        await store.save("dev/web-01", cap2)
        latest = await store.latest("dev/web-01", "sudoers")
        assert latest is not None
        assert latest.content == cap2.content
        await store.close()

    async def test_latest_scope_key_isolation(self) -> None:
        store = await _make_store()
        cap_alice = _capture("ssh-ed25519 AAAA alice", scope_key="alice", kind="authorized_keys")
        cap_bob = _capture("ssh-ed25519 BBBB bob", scope_key="bob", kind="authorized_keys")
        await store.save("dev/web-01", cap_alice)
        await store.save("dev/web-01", cap_bob)
        alice = await store.latest("dev/web-01", "authorized_keys", "alice")
        bob = await store.latest("dev/web-01", "authorized_keys", "bob")
        assert alice is not None and "alice" in alice.content
        assert bob is not None and "bob" in bob.content
        await store.close()


class TestBaselineStoreCompareAndSave:
    async def test_first_run_returns_is_first_run(self) -> None:
        store = await _make_store()
        result = await store.compare_and_save("dev/web-01", _capture("root ALL=(ALL) ALL"))
        assert result.is_first_run is True
        assert result.changed is False
        assert result.unified_diff == ""
        assert result.previous is None
        await store.close()

    async def test_no_change_returns_unchanged(self) -> None:
        store = await _make_store()
        cap = _capture("root ALL=(ALL) ALL")
        await store.compare_and_save("dev/web-01", cap)
        result = await store.compare_and_save("dev/web-01", cap)
        assert result.is_first_run is False
        assert result.changed is False
        assert result.unified_diff == ""
        await store.close()

    async def test_change_detected_and_diff_rendered(self) -> None:
        store = await _make_store()
        cap1 = _capture("root ALL=(ALL) ALL")
        cap2 = _capture("root ALL=(ALL) ALL\ndeploy ALL=(ALL) NOPASSWD:/usr/bin/systemctl")
        await store.compare_and_save("dev/web-01", cap1)
        result = await store.compare_and_save("dev/web-01", cap2)
        assert result.changed is True
        assert result.is_first_run is False
        assert "deploy" in result.unified_diff
        assert result.previous is not None
        assert result.previous.content == cap1.content
        await store.close()

    async def test_canonicalization_preserves_hash(self) -> None:
        """Content hash depends on content string, not insertion order."""
        content = "line-a\nline-b\n"
        cap1 = _capture(content)
        cap2 = _capture(content)
        assert cap1.content_hash == cap2.content_hash

    async def test_different_content_different_hash(self) -> None:
        assert _capture("a").content_hash != _capture("b").content_hash

    async def test_metadata_not_included_in_hash(self) -> None:
        cap1 = _capture("content", metadata={"lines": "1"})
        cap2 = _capture("content", metadata={"lines": "99"})
        assert cap1.content_hash == cap2.content_hash


class TestBaselineStorePruning:
    async def test_pruning_keeps_retention_count(self) -> None:
        store = await _make_store(retention=3)
        for i in range(6):
            await store.save("dev/web-01", _capture(f"version-{i}"))
        async with store._db.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM vm_baselines"
                    " WHERE vm_id = :vid AND baseline_kind = :kind"
                ),
                {"vid": "dev/web-01", "kind": "sudoers"},
            )
            row = result.fetchone()
        assert int(str(row[0])) == 3
        await store.close()

    async def test_pruning_retains_newest_rows(self) -> None:
        store = await _make_store(retention=2)
        for i in range(4):
            await store.save("dev/web-01", _capture(f"content-{i}"))
        latest = await store.latest("dev/web-01", "sudoers")
        assert latest is not None
        assert latest.content == "content-3"
        await store.close()

    async def test_pruning_isolated_by_scope_key(self) -> None:
        store = await _make_store(retention=2)
        for i in range(4):
            cap = _capture(f"alice-{i}", scope_key="alice", kind="authorized_keys")
            await store.save("dev/web-01", cap)
        for i in range(4):
            cap = _capture(f"bob-{i}", scope_key="bob", kind="authorized_keys")
            await store.save("dev/web-01", cap)
        async with store._db.begin() as conn:
            res_alice = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM vm_baselines"
                    " WHERE vm_id = :vid AND scope_key = :sk"
                ),
                {"vid": "dev/web-01", "sk": "alice"},
            )
            row_alice = res_alice.fetchone()
            res_bob = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM vm_baselines"
                    " WHERE vm_id = :vid AND scope_key = :sk"
                ),
                {"vid": "dev/web-01", "sk": "bob"},
            )
            row_bob = res_bob.fetchone()
        assert int(str(row_alice[0])) == 2
        assert int(str(row_bob[0])) == 2
        await store.close()
