"""Data models for the service_restart action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass
class RestartContext:
    """Context captured around a service restart operation.

    Fields are populated by parse_restart_output() from wrapper stdout.
    Missing sections (e.g. in --snapshot-only mode) are empty strings.
    """

    pre_status: str = ""
    pre_journal: str = ""
    post_active: str = ""
    post_status: str = ""
    post_journal: str = ""


class ServiceRestartState(TypedDict, total=False):
    """State flowing through the service_restart sub-graph."""

    vm_id: str
    batch_id: str
    dry_run: bool

    # SSH connection (populated by vm_graph before invoking sub-graph)
    hostname: str
    username: str
    key_path: str
    os_family: str

    status: str
    error: str | None

    # Which unit to restart (from CLI --unit flag)
    unit_name: str
    # Inventory allowlist for this environment
    restartable_units: list[str]

    # Pre/post context captured from wrapper output
    pre_status: str
    pre_journal: str
    post_active: str
    post_status: str
    post_journal: str
