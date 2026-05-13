"""Per-kind drift check implementations (Phase 2).

Each module implements the DriftCheck protocol from errander.safety.baselines.
The drift orchestration node in vm_graph.py runs all enabled checks via a
single SSH connection.
"""
