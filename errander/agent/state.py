"""Agent state definitions — see individual graph modules.

State TypedDicts are defined inline in their respective graph modules:
- BatchGraphState: errander.agent.graph
- VMGraphState: errander.agent.vm_graph
- DiskCleanupGraphState: errander.agent.subgraphs.disk_cleanup

This file is intentionally empty. The dataclass-based states that were
originally defined here have been superseded by TypedDicts, which are
LangGraph's native state format.
"""
