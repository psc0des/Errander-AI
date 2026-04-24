"""Tests for file-based VM locking."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from errander.safety.locking import FileLocker, LockInfo, _sanitize_vm_id


class TestLockInfo:
    """Tests for LockInfo dataclass."""

    def test_not_expired(self) -> None:
        now = datetime.now(tz=timezone.utc)
        info = LockInfo(
            vm_id="vm-1", batch_id="b-1",
            acquired_at=now.isoformat(), ttl_seconds=3600,
        )
        assert not info.is_expired(now)

    def test_expired(self) -> None:
        past = datetime.now(tz=timezone.utc) - timedelta(hours=3)
        info = LockInfo(
            vm_id="vm-1", batch_id="b-1",
            acquired_at=past.isoformat(), ttl_seconds=3600,
        )
        assert info.is_expired()

    def test_frozen(self) -> None:
        info = LockInfo(vm_id="vm-1", batch_id="b-1", acquired_at="", ttl_seconds=0)
        with pytest.raises(AttributeError):
            info.vm_id = "other"  # type: ignore[misc]


class TestSanitizeVmId:
    """Tests for VM ID sanitization."""

    def test_slashes_replaced(self) -> None:
        assert _sanitize_vm_id("production/web-01") == "production_web-01"

    def test_safe_chars_preserved(self) -> None:
        assert _sanitize_vm_id("web-01.prod") == "web-01.prod"


class TestFileLocker:
    """Tests for FileLocker acquire/release/query operations."""

    async def test_acquire_and_release(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert await locker.acquire("vm-1", "batch-A")
        assert await locker.is_locked("vm-1")
        assert await locker.release("vm-1", "batch-A")
        assert not await locker.is_locked("vm-1")

    async def test_acquire_blocked_by_existing_lock(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert await locker.acquire("vm-1", "batch-A")
        assert not await locker.acquire("vm-1", "batch-B")

    async def test_same_batch_cannot_double_acquire(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert await locker.acquire("vm-1", "batch-A")
        assert not await locker.acquire("vm-1", "batch-A")

    async def test_release_wrong_batch_fails(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A")
        assert not await locker.release("vm-1", "batch-B")
        # Lock still held
        assert await locker.is_locked("vm-1")

    async def test_release_nonexistent_returns_false(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert not await locker.release("vm-1", "batch-A")

    async def test_stale_lock_auto_cleaned(self, tmp_path: Path) -> None:
        """Expired locks are cleaned up on acquire."""
        locker = FileLocker(tmp_path / "locks")
        # Write an already-expired lock
        lock_path = locker._lock_path("vm-1")
        past = datetime.now(tz=timezone.utc) - timedelta(hours=5)
        lock_data = {
            "vm_id": "vm-1", "batch_id": "old-batch",
            "acquired_at": past.isoformat(), "ttl_seconds": 3600,
        }
        lock_path.write_text(json.dumps(lock_data))

        # New acquire should succeed because old lock is expired
        assert await locker.acquire("vm-1", "batch-new")
        info = await locker.get_lock_info("vm-1")
        assert info is not None
        assert info.batch_id == "batch-new"

    async def test_is_locked_cleans_expired(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        lock_path = locker._lock_path("vm-1")
        past = datetime.now(tz=timezone.utc) - timedelta(hours=5)
        lock_data = {
            "vm_id": "vm-1", "batch_id": "old",
            "acquired_at": past.isoformat(), "ttl_seconds": 3600,
        }
        lock_path.write_text(json.dumps(lock_data))

        assert not await locker.is_locked("vm-1")
        assert not lock_path.exists()

    async def test_get_lock_info(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A", ttl_seconds=7200)
        info = await locker.get_lock_info("vm-1")
        assert info is not None
        assert info.vm_id == "vm-1"
        assert info.batch_id == "batch-A"
        assert info.ttl_seconds == 7200

    async def test_get_lock_info_nonexistent(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert await locker.get_lock_info("vm-1") is None

    async def test_list_locks(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A")
        await locker.acquire("vm-2", "batch-A")
        await locker.acquire("vm-3", "batch-A")

        locks = await locker.list_locks()
        vm_ids = {l.vm_id for l in locks}
        assert vm_ids == {"vm-1", "vm-2", "vm-3"}

    async def test_list_locks_excludes_expired(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A")

        # Write an expired lock for vm-2
        lock_path = locker._lock_path("vm-2")
        past = datetime.now(tz=timezone.utc) - timedelta(hours=5)
        lock_data = {
            "vm_id": "vm-2", "batch_id": "old",
            "acquired_at": past.isoformat(), "ttl_seconds": 3600,
        }
        lock_path.write_text(json.dumps(lock_data))

        locks = await locker.list_locks()
        assert len(locks) == 1
        assert locks[0].vm_id == "vm-1"
        assert not lock_path.exists()  # expired lock cleaned up

    async def test_force_release(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A")
        assert await locker.force_release("vm-1")
        assert not await locker.is_locked("vm-1")

    async def test_force_release_nonexistent(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        assert not await locker.force_release("vm-1")

    async def test_corrupt_lock_file_cleaned(self, tmp_path: Path) -> None:
        """Corrupt JSON lock file is removed and treated as unlocked."""
        locker = FileLocker(tmp_path / "locks")
        lock_path = locker._lock_path("vm-1")
        lock_path.write_text("not valid json {{{")

        assert not await locker.is_locked("vm-1")
        assert not lock_path.exists()

    async def test_multiple_vms_independent(self, tmp_path: Path) -> None:
        locker = FileLocker(tmp_path / "locks")
        await locker.acquire("vm-1", "batch-A")
        await locker.acquire("vm-2", "batch-A")

        await locker.release("vm-1", "batch-A")
        assert not await locker.is_locked("vm-1")
        assert await locker.is_locked("vm-2")

    async def test_lock_dir_created_if_missing(self, tmp_path: Path) -> None:
        lock_dir = tmp_path / "nested" / "lock" / "dir"
        locker = FileLocker(lock_dir)
        assert lock_dir.exists()
        assert await locker.acquire("vm-1", "batch-A")

    async def test_vm_id_with_slashes(self, tmp_path: Path) -> None:
        """VM IDs like 'production/web-01' work correctly."""
        locker = FileLocker(tmp_path / "locks")
        assert await locker.acquire("production/web-01", "batch-A")
        assert await locker.is_locked("production/web-01")
        assert await locker.release("production/web-01", "batch-A")


# --- Atomic locking tests (Step 3) ---

class TestAtomicLocking:
    """Step 3: lock writes are atomic; race conditions are handled; corrupt files are stale."""

    async def test_atomic_write_leaves_no_tmp_file(self, tmp_path: Path) -> None:
        """After acquire, no .tmp file remains in the lock directory."""
        locker = FileLocker(tmp_path / "locks")
        assert await locker.acquire("vm-1", "batch-A")

        lock_dir = tmp_path / "locks"
        tmp_files = list(lock_dir.glob("*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    async def test_concurrent_acquire_only_one_wins(self, tmp_path: Path) -> None:
        """10 concurrent acquire calls for the same VM: exactly 1 succeeds."""
        import asyncio

        locker = FileLocker(tmp_path / "locks")
        results = await asyncio.gather(
            *[locker.acquire("vm-1", f"batch-{i}") for i in range(10)],
        )
        successful = [r for r in results if r is True]
        assert len(successful) == 1

    async def test_corrupt_lock_file_logged_as_stale(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Corrupt lock file triggers a warning log and acquire succeeds."""
        import logging

        locker = FileLocker(tmp_path / "locks")
        lock_path = locker._lock_path("vm-1")
        lock_path.write_text("not valid json {{{")

        with caplog.at_level(logging.WARNING, logger="errander.safety.locking"):
            acquired = await locker.acquire("vm-1", "batch-A")

        assert acquired is True
        assert any("Corrupt" in r.message or "corrupt" in r.message for r in caplog.records)

    async def test_crash_midwrite_recoverable(self, tmp_path: Path) -> None:
        """Leftover .tmp file from a crashed write does not block next acquire."""
        locker = FileLocker(tmp_path / "locks")
        lock_path = locker._lock_path("vm-1")
        # Simulate a crash that left a .tmp file behind
        tmp_file = lock_path.with_suffix(lock_path.suffix + ".tmp")
        tmp_file.write_text('{"partial": "crash"}')

        # Acquire should succeed despite the leftover .tmp
        assert await locker.acquire("vm-1", "batch-A")
        assert await locker.is_locked("vm-1")

    async def test_o_excl_race_falls_through_to_stale_check(
        self, tmp_path: Path,
    ) -> None:
        """Race: _read_lock returns None but file exists → O_EXCL fails → re-read decides."""
        from unittest.mock import patch

        locker = FileLocker(tmp_path / "locks")

        # Acquire with batch-A to put a real lock file in place
        assert await locker.acquire("vm-1", "batch-A")

        # Simulate the race: first call to _read_lock returns None (pre-race snapshot),
        # but the file actually exists on disk (batch-A's lock).
        original_read_lock = locker._read_lock
        call_count = 0

        def mock_read_lock(lock_path: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # Simulates checking before batch-A wrote the file
            return original_read_lock(lock_path)  # type: ignore[arg-type]

        with patch.object(locker, "_read_lock", side_effect=mock_read_lock):
            result = await locker.acquire("vm-1", "batch-B")

        # batch-A holds an unexpired lock → batch-B is blocked
        assert result is False
