"""Execution layer — SSH, OS-specific commands, and sandbox mode.

This layer sits between the agent graphs and actual VM operations.
All commands flow through here, enabling dry-run simulation and
OS-specific command abstraction.
"""
