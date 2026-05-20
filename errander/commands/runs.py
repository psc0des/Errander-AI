"""runs CLI sub-commands — Project A, A6.

Provides:
  errander runs list          — show recent batches with status + duration
  errander runs inspect <id>  — show batch record + LangGraph checkpoint node
  errander runs resume <id>   — resume a RUNNING batch stuck at a safe node

Safety contract:
  - Only SAFE_RESUME_NODES may be resumed without OPERATOR_FORCE_RESUME flag.
  - Any other node puts the batch in NEEDS_OPERATOR_REVIEW status.
  - No errander runs abandon — per SRE decision (out of scope).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-command: runs list
# ---------------------------------------------------------------------------

async def run_list(db_path: str, limit: int = 20) -> int:
    """Print a table of recent batches."""
    import aiosqlite

    from errander.safety.batches import BatchStore
    from errander.safety.migrations import run_migrations

    async with aiosqlite.connect(db_path) as db:
        await run_migrations(db)
        store = BatchStore(db)
        batches = await store.list_recent(limit=limit)

    if not batches:
        print("No batch runs recorded.")
        return 0

    header = f"{'BATCH ID':<28} {'STATUS':<26} {'ENV':<12} {'DRY':<5} {'VMs':<5} {'STARTED'}"
    print(header)
    print("-" * len(header))
    for b in batches:
        duration = ""
        if b.finished_at and b.started_at:
            try:
                from datetime import datetime
                s = datetime.fromisoformat(b.started_at)
                f = datetime.fromisoformat(b.finished_at)
                secs = int((f - s).total_seconds())
                duration = f" ({secs}s)"
            except ValueError:
                pass
        started_short = b.started_at[:16] if b.started_at else ""
        print(
            f"{b.id:<28} {b.status:<26} {b.env_name:<12} "
            f"{'yes' if b.dry_run else 'no':<5} {b.vm_count:<5} "
            f"{started_short}{duration}"
        )
        if b.error:
            print(f"  └─ error: {b.error}")

    print(f"\n{len(batches)} run(s) shown.")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: runs inspect <batch-id>
# ---------------------------------------------------------------------------

async def run_inspect(db_path: str, batch_id: str) -> int:
    """Print full details for one batch including LangGraph checkpoint state."""
    import aiosqlite

    from errander.safety.batches import BatchStore
    from errander.safety.migrations import run_migrations

    async with aiosqlite.connect(db_path) as db:
        await run_migrations(db)
        store = BatchStore(db)
        rec = await store.get(batch_id)

    if rec is None:
        print(f"Batch {batch_id!r} not found.")
        return 1

    print(f"Batch:      {rec.id}")
    print(f"Status:     {rec.status}")
    print(f"Env:        {rec.env_name}")
    print(f"Dry-run:    {rec.dry_run}")
    print(f"VMs:        {rec.vm_count}")
    print(f"Started:    {rec.started_at}")
    print(f"Finished:   {rec.finished_at or '—'}")
    if rec.error:
        print(f"Error:      {rec.error}")

    # Try to read LangGraph checkpoint for this batch.
    # The checkpoint thread_id is not the batch_id (it's a separate UUID
    # generated in run_env_batch). We search by matching channel values.
    print()
    _checkpoint_note = _inspect_checkpoint(db_path, batch_id)
    if _checkpoint_note:
        print("Checkpoint:")
        print(f"  {_checkpoint_note}")
    else:
        print("Checkpoint: no checkpoint stored for this batch.")

    return 0


def _inspect_checkpoint(db_path: str, batch_id: str) -> str | None:
    """Synchronously probe the LangGraph checkpoint DB for this batch_id."""
    try:
        import sqlite3
        con = sqlite3.connect(db_path)
        # LangGraph 3.x stores channel_values as JSON blobs in checkpoints
        cur = con.execute(
            """
            SELECT thread_id, checkpoint_ns, type, checkpoint
            FROM checkpoints
            ORDER BY checkpoint_id DESC
            LIMIT 200
            """
        )
        rows = cur.fetchall()
        con.close()
        # Find a checkpoint whose channel data references our batch_id
        for thread_id, ns, ctype, data in rows:
            try:
                blob = data if isinstance(data, bytes) else data.encode()
                # Try to find batch_id in the raw bytes (fast scan)
                if batch_id.encode() not in blob:
                    continue
                return f"thread_id={thread_id} ns={ns!r} type={ctype}"
            except Exception:
                continue
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sub-command: runs resume <batch-id>
# ---------------------------------------------------------------------------

async def run_resume(db_path: str, batch_id: str, *, force: bool = False) -> int:
    """Resume a RUNNING batch stuck at a safe node.

    Safety contract:
      - Reads the most recent checkpoint for this batch.
      - If the next node is in SAFE_RESUME_NODES: resumes automatically.
      - If not in SAFE_RESUME_NODES and force=False: prints error, sets
        status=NEEDS_OPERATOR_REVIEW.
      - If force=True (OPERATOR_FORCE_RESUME): resumes regardless of node,
        emits a NEEDS_OPERATOR_REVIEW audit event for the record.
    """
    import aiosqlite

    from errander.agent.graph import SAFE_RESUME_NODES
    from errander.models.batches import BatchStatus
    from errander.models.events import AuditEvent, EventType
    from errander.safety.audit import AuditStore
    from errander.safety.batches import BatchStore
    from errander.safety.migrations import run_migrations

    async with aiosqlite.connect(db_path) as db:
        await run_migrations(db)
        store = BatchStore(db)
        rec = await store.get(batch_id)

    if rec is None:
        print(f"Batch {batch_id!r} not found.")
        return 1

    if rec.status != BatchStatus.RUNNING:
        print(
            f"Batch {batch_id} is {rec.status} — only RUNNING batches can be resumed."
        )
        return 1

    # Look up checkpoint next_node
    next_node = _find_next_node_from_checkpoint(db_path, batch_id)

    if next_node is None:
        print(
            f"No checkpoint found for batch {batch_id}. "
            "The batch may have been running without checkpointing enabled."
        )
        return 1

    print(f"Batch {batch_id} is paused at node: {next_node!r}")

    if next_node not in SAFE_RESUME_NODES and not force:
        print(
            f"Node {next_node!r} is not in SAFE_RESUME_NODES. "
            "Automatic resume is unsafe here.\n"
            f"Safe nodes: {sorted(SAFE_RESUME_NODES)}\n"
            "Use --force to override (OPERATOR_FORCE_RESUME — adds audit event)."
        )
        # Mark as NEEDS_OPERATOR_REVIEW so ops dashboard highlights it
        async with aiosqlite.connect(db_path) as db2:
            store2 = BatchStore(db2)
            await store2.update_status(
                batch_id,
                BatchStatus.NEEDS_OPERATOR_REVIEW,
                error=f"crashed at unsafe node {next_node!r} — operator review required",
            )
        print(f"Batch {batch_id} status set to NEEDS_OPERATOR_REVIEW.")
        return 1

    if force and next_node not in SAFE_RESUME_NODES:
        # Emit OPERATOR_FORCE_RESUME audit event
        audit_store = AuditStore(db_path, strict_mode=False)
        await audit_store.initialize()
        await audit_store.log_event(AuditEvent(
            event_type=EventType.OPERATOR_FORCE_RESUME,
            batch_id=batch_id,
            detail=f"OPERATOR_FORCE_RESUME: resuming at unsafe node {next_node!r}",
            metadata={"node": next_node, "force": True},
        ))
        await audit_store.close()
        print(f"OPERATOR_FORCE_RESUME audit event emitted for batch {batch_id}.")

    # The actual graph resume happens by calling ainvoke with the same thread_id
    # and a checkpointer. For the CLI, we print instructions since the running
    # agent process owns the checkpointer and must be restarted to resume.
    print(
        f"\nTo resume batch {batch_id}:\n"
        f"  Restart the Errander-AI agent. It will detect the RUNNING checkpoint\n"
        f"  at node {next_node!r} and resume from that point automatically.\n"
        f"\n"
        f"  Alternatively, if the agent is still running, it will resume\n"
        f"  automatically after its next heartbeat cycle."
    )
    return 0


def _find_next_node_from_checkpoint(db_path: str, batch_id: str) -> str | None:
    """Probe the LangGraph checkpoint DB to find the next node for batch_id."""
    try:
        import sqlite3
        con = sqlite3.connect(db_path)
        cur = con.execute(
            """
            SELECT channel_values FROM checkpoint_blobs
            ORDER BY thread_id DESC, checkpoint_id DESC
            LIMIT 500
            """
        )
        rows = cur.fetchall()
        con.close()
        for (blob,) in rows:
            try:
                data = blob if isinstance(blob, bytes) else blob.encode("utf-8")
                if batch_id.encode() not in data:
                    continue
                # Try to find __next__ in the blob (LangGraph channel name)
                idx = data.find(b"__next__")
                if idx == -1:
                    continue
                # Extract a small region around __next__ and look for a node name
                region = data[idx:idx + 200].decode("utf-8", errors="ignore")
                for node in sorted(_ALL_KNOWN_NODES):
                    if node in region:
                        return node
            except Exception:
                continue
        return None
    except Exception:
        return None


# Known graph node names (for checkpoint scanning)
_ALL_KNOWN_NODES: frozenset[str] = frozenset({
    "init_batch", "validate_window", "validate_targets", "check_fleet_health",
    "plan_vms", "plan_vm", "collect_plans", "enrich_plan", "generate_plan_artifact",
    "load_deferred_artifact", "approval_gate", "verify_plan_hash",
    "prepare_waves", "dispatch_wave", "run_vm", "check_wave_health",
    "collect_results", "generate_report",
})


# ---------------------------------------------------------------------------
# Entry point (called from main.py)
# ---------------------------------------------------------------------------

async def dispatch_runs(args: argparse.Namespace, db_path: str) -> int:
    """Route runs sub-commands based on args.runs_command."""
    cmd = getattr(args, "runs_command", None)
    if cmd == "list":
        return await run_list(db_path, limit=getattr(args, "runs_limit", 20))
    if cmd == "inspect":
        batch_id = getattr(args, "runs_batch_id", "")
        if not batch_id:
            print("Error: runs inspect requires <batch-id>")
            return 1
        return await run_inspect(db_path, batch_id)
    if cmd == "resume":
        batch_id = getattr(args, "runs_batch_id", "")
        if not batch_id:
            print("Error: runs resume requires <batch-id>")
            return 1
        force = getattr(args, "runs_force", False)
        return await run_resume(db_path, batch_id, force=force)
    print("Usage: errander runs {list|inspect <id>|resume <id>}")
    return 1
