"""Backup verification sub-graph — confirm backups exist and are recent.

Lifecycle:
1. Validate: Check backup paths are configured for this VM.
2. Snapshot: Record current backup file metadata.
3. Execute: Verify each backup exists, is recent, and has expected size.
4. Verify: Report stale, missing, or suspicious backups.
5. Rollback: N/A — this is a read-only verification action.

Risk tier: High (human approval required).
Rollback strategy: N/A — no state changes made.
"""

from __future__ import annotations


def build_backup_verify_subgraph():  # noqa: ANN201
    """Construct and return the compiled backup verification sub-graph.

    Returns:
        CompiledGraph: The backup verification action sub-graph.
    """
    raise NotImplementedError("Backup verification sub-graph not yet implemented")
