"""Per-VM maintenance graph.

Level 2 of the Option C architecture. This graph runs once per VM
(dispatched via Send() from the parent orchestrator) and:
1. Acquires VM-level lock
2. Discovers system state via SSH
3. Plans actions (LLM prioritization with hardcoded fallback)
4. Dispatches to action sub-graphs sequentially (low-risk first)
5. Audits all results
6. Releases lock

Nodes:
    acquire_lock: File-based lock (v1) / Valkey lock (v2).
    discover: SSH into VM, gather OS, disk, packages, docker, logs.
    plan_actions: Use LLM to prioritize actions based on system state.
    dispatch_action: Route to the appropriate action sub-graph.
    check_more_actions: Conditional edge — loop or proceed to audit.
    audit_results: Record all results to audit trail.
    release_lock: Release VM lock.
"""

from __future__ import annotations


def build_vm_graph():  # noqa: ANN201
    """Construct and return the compiled per-VM maintenance graph.

    Returns:
        CompiledGraph: The LangGraph per-VM graph, ready to invoke.
    """
    raise NotImplementedError("Per-VM maintenance graph not yet implemented")
