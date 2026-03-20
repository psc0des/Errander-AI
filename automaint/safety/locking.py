"""VM-level locking to prevent concurrent maintenance.

v1: File-based locking on the agent VM (simple, single-agent).
v2: Valkey (Redis fork) distributed locking for multi-agent setups.

Lock semantics:
- Lock is per VM ID, not per action
- Lock includes batch_id, timestamp, and optional metadata
- Lock auto-expires after configurable TTL (prevents stale locks)
- Attempting to lock an already-locked VM results in SKIP

Lock files are JSON containing:
  {"vm_id": "...", "batch_id": "...", "acquired_at": "ISO8601", "ttl_seconds": N}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LockInfo:
    """Metadata stored in a lock file.

    Attributes:
        vm_id: The locked VM.
        batch_id: Which batch run holds the lock.
        acquired_at: When the lock was acquired (ISO 8601).
        ttl_seconds: Time-to-live before the lock is considered stale.
    """

    vm_id: str
    batch_id: str
    acquired_at: str
    ttl_seconds: int

    def is_expired(self, now: datetime | None = None) -> bool:
        """Check if the lock has expired based on TTL."""
        if now is None:
            now = datetime.now(tz=timezone.utc)
        acquired = datetime.fromisoformat(self.acquired_at)
        # Ensure acquired has timezone info for comparison
        if acquired.tzinfo is None:
            acquired = acquired.replace(tzinfo=timezone.utc)
        elapsed = (now - acquired).total_seconds()
        return elapsed > self.ttl_seconds


def _sanitize_vm_id(vm_id: str) -> str:
    """Convert vm_id to a safe filename."""
    return re.sub(r"[^\w\-.]", "_", vm_id)


class FileLocker:
    """File-based VM locking.

    Each lock is a JSON file in the lock directory. Lock files are
    named after the VM ID (sanitized for filesystem safety).

    Usage:
        locker = FileLocker(lock_dir=Path("/var/lib/automaint/locks"))
        if await locker.acquire("dev/web-01", "batch-001", ttl_seconds=7200):
            try:
                # ... do maintenance ...
            finally:
                await locker.release("dev/web-01", "batch-001")
    """

    def __init__(self, lock_dir: Path) -> None:
        self._lock_dir = lock_dir
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    def _lock_path(self, vm_id: str) -> Path:
        return self._lock_dir / f"{_sanitize_vm_id(vm_id)}.lock"

    async def acquire(
        self,
        vm_id: str,
        batch_id: str,
        ttl_seconds: int = 7200,
    ) -> bool:
        """Acquire a maintenance lock for a VM.

        If a lock file exists but is expired, it is automatically
        cleaned up and the new lock is acquired.

        Args:
            vm_id: VM to lock.
            batch_id: Current batch run ID.
            ttl_seconds: Lock auto-expire time (default 2 hours).

        Returns:
            True if lock acquired, False if VM is already locked
            by a non-expired lock.
        """
        lock_path = self._lock_path(vm_id)

        # Check existing lock
        existing = self._read_lock(lock_path)
        if existing is not None:
            if existing.is_expired():
                logger.warning(
                    "Stale lock detected for %s (batch %s, acquired %s) — removing",
                    vm_id, existing.batch_id, existing.acquired_at,
                )
                lock_path.unlink(missing_ok=True)
            else:
                logger.info(
                    "VM %s is already locked by batch %s (acquired %s)",
                    vm_id, existing.batch_id, existing.acquired_at,
                )
                return False

        # Write new lock
        lock_info = LockInfo(
            vm_id=vm_id,
            batch_id=batch_id,
            acquired_at=datetime.now(tz=timezone.utc).isoformat(),
            ttl_seconds=ttl_seconds,
        )
        lock_path.write_text(
            json.dumps(asdict(lock_info), indent=2),
            encoding="utf-8",
        )
        logger.info("Lock acquired for %s by batch %s", vm_id, batch_id)
        return True

    async def release(self, vm_id: str, batch_id: str) -> bool:
        """Release a maintenance lock for a VM.

        Only releases if the lock is held by the specified batch_id.

        Args:
            vm_id: VM to unlock.
            batch_id: Batch run ID (must match lock holder).

        Returns:
            True if released, False if lock was held by different batch
            or no lock existed.
        """
        lock_path = self._lock_path(vm_id)
        existing = self._read_lock(lock_path)

        if existing is None:
            logger.warning("No lock to release for %s", vm_id)
            return False

        if existing.batch_id != batch_id:
            logger.warning(
                "Cannot release lock for %s: held by batch %s, not %s",
                vm_id, existing.batch_id, batch_id,
            )
            return False

        lock_path.unlink(missing_ok=True)
        logger.info("Lock released for %s by batch %s", vm_id, batch_id)
        return True

    async def is_locked(self, vm_id: str) -> bool:
        """Check if a VM is currently locked (non-expired).

        Args:
            vm_id: VM to check.

        Returns:
            True if locked and not expired.
        """
        lock_path = self._lock_path(vm_id)
        existing = self._read_lock(lock_path)
        if existing is None:
            return False
        if existing.is_expired():
            # Clean up expired lock
            lock_path.unlink(missing_ok=True)
            return False
        return True

    async def get_lock_info(self, vm_id: str) -> LockInfo | None:
        """Get lock metadata for a VM.

        Returns None if not locked or lock is expired.

        Args:
            vm_id: VM to check.

        Returns:
            LockInfo if locked, None otherwise.
        """
        lock_path = self._lock_path(vm_id)
        existing = self._read_lock(lock_path)
        if existing is None:
            return None
        if existing.is_expired():
            lock_path.unlink(missing_ok=True)
            return None
        return existing

    async def list_locks(self) -> list[LockInfo]:
        """List all active (non-expired) locks.

        Cleans up any expired locks found.

        Returns:
            List of active LockInfo objects.
        """
        active: list[LockInfo] = []
        for lock_path in self._lock_dir.glob("*.lock"):
            info = self._read_lock(lock_path)
            if info is None:
                continue
            if info.is_expired():
                lock_path.unlink(missing_ok=True)
                logger.info("Cleaned up expired lock: %s", lock_path.name)
                continue
            active.append(info)
        return active

    async def force_release(self, vm_id: str) -> bool:
        """Force-release a lock regardless of batch_id.

        For emergency use only.

        Args:
            vm_id: VM to unlock.

        Returns:
            True if a lock was removed, False if none existed.
        """
        lock_path = self._lock_path(vm_id)
        if lock_path.exists():
            lock_path.unlink()
            logger.warning("Force-released lock for %s", vm_id)
            return True
        return False

    def _read_lock(self, lock_path: Path) -> LockInfo | None:
        """Read and parse a lock file. Returns None if not found or corrupt."""
        if not lock_path.exists():
            return None
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            return LockInfo(**data)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("Corrupt lock file %s: %s — removing", lock_path, e)
            lock_path.unlink(missing_ok=True)
            return None
