"""VM-level locking to prevent concurrent maintenance.

v1: File-based locking on the agent VM (simple, single-agent).
v2: Valkey (Redis fork) distributed locking for multi-agent setups.

Lock semantics:
- Lock is per VM ID, not per action
- Lock includes batch_id and timestamp for debugging
- Lock auto-expires after configurable timeout (prevents stale locks)
- Attempting to lock an already-locked VM results in SKIP
"""

from __future__ import annotations


async def acquire_lock(vm_id: str, batch_id: str, ttl_seconds: int = 3600) -> bool:
    """Acquire a maintenance lock for a VM.

    Args:
        vm_id: VM to lock.
        batch_id: Current batch run ID (stored in lock metadata).
        ttl_seconds: Lock auto-expire time (default 1 hour).

    Returns:
        True if lock acquired, False if VM is already locked.
    """
    raise NotImplementedError("Lock acquisition not yet implemented")


async def release_lock(vm_id: str, batch_id: str) -> bool:
    """Release a maintenance lock for a VM.

    Args:
        vm_id: VM to unlock.
        batch_id: Batch run ID (must match lock holder).

    Returns:
        True if released, False if lock was held by different batch.
    """
    raise NotImplementedError("Lock release not yet implemented")
