"""Action sub-graphs (Level 3 of Option C architecture).

Each sub-graph handles the full lifecycle of one action type:
validate → snapshot → execute (or dry-run) → verify → rollback (on failure).

All sub-graphs follow the same structural pattern but contain
action-specific logic for validation, execution, and rollback.
"""
