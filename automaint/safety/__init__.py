"""Safety architecture — validators, rollback, approval, locking, and audit.

All safety modules enforce the principle: fail loud, fail fast, fail safe.
No action executes without passing through the safety layer.
"""
