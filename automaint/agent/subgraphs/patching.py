"""Patching sub-graph — non-kernel OS package updates.

Lifecycle:
1. Validate: Check package manager available, exclude kernel packages.
2. Snapshot: Record installed package versions for rollback.
3. Execute: Run package update (apt/dnf) or simulate in dry-run mode.
4. Verify: Confirm packages updated to expected versions.
5. Rollback: Reinstall previous package versions on failure.

Risk tier: Medium (log + notify).
Rollback strategy: Full — snapshot package list, batch rollback to previous versions.

IMPORTANT: Kernel packages (linux-*, kernel-*) are ALWAYS excluded.
"""

from __future__ import annotations


def build_patching_subgraph():  # noqa: ANN201
    """Construct and return the compiled patching sub-graph.

    Returns:
        CompiledGraph: The patching action sub-graph.
    """
    raise NotImplementedError("Patching sub-graph not yet implemented")
