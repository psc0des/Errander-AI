"""Batch execution report models.

Symmetric counterpart to models/plans.py (the input artifact).
Each maintenance phase appends its section fields here;
observability/reporting.py renders them into Slack-formatted text.

All list fields default to empty so the report renders cleanly when
individual features are disabled or produce no findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PreflightBlock:
    """A pre-flight gate that prevented an action from running."""

    vm_id: str
    action_type: str
    # 'pkg_lock' | 'maintenance_window' | ...
    reason: str
    holder_pid: int | None
    holder_cmd: str | None


@dataclass(frozen=True)
class VMRebootStatus:
    """A VM that requires a reboot after patching."""

    vm_id: str
    reason: str | None
    pkgs_requiring: tuple[str, ...]
    detected_at: datetime


@dataclass(frozen=True)
class ServiceRegression:
    """A critical service that was active before maintenance and unhealthy after."""

    vm_id: str
    service_name: str
    state_before: str
    state_after: str


@dataclass(frozen=True)
class DiskGrowth:
    """A mountpoint that exceeded the configured growth threshold."""

    vm_id: str
    mountpoint: str
    used_pct_start: float
    used_pct_end: float
    window_start: datetime
    window_end: datetime

    @property
    def delta_pct(self) -> float:
        return self.used_pct_end - self.used_pct_start

    @property
    def window_label(self) -> str:
        delta = self.window_end - self.window_start
        days = delta.days
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{days}d{hours}h"
        return f"{days}d"


@dataclass(frozen=True)
class DriftChange:
    """A per-resource drift change detected on a VM."""

    vm_id: str
    # 'sudoers' | 'authorized_keys' | 'listening_ports' | 'scheduled_jobs'
    kind: str
    # username for authorized_keys; '' for kinds with a single scope
    scope_key: str
    # Truncated to diff_max_lines at emission time (Phase 2.0)
    unified_diff: str


@dataclass(frozen=True)
class FailedLoginSummary:
    """Aggregated failed SSH login summary for a VM (24h window by default)."""

    vm_id: str
    window_hours: int
    total_count: int
    # (username, count) pairs, top-5
    top_users: tuple[tuple[str, int], ...]
    # (ip, count) pairs, top-5
    top_source_ips: tuple[tuple[str, int], ...]


@dataclass
class BatchReport:
    """Structured output of a complete maintenance batch run.

    Section ordering matches the rendered Slack report:
      1. vm_action_results  — existing per-VM action outcomes
      2. preflight_blocks   — highest signal: something was deliberately skipped
      3. service_health_regressions — most operator-urgent finding
      4. reboot_required    — informational, needs human scheduling
      5. drift_changes      — grouped by kind in rendering
      6. disk_growth_alerts — trend data, lower urgency
      7. failed_logins      — security snapshot
    """

    batch_id: str
    generated_at: datetime = field(default_factory=datetime.now)

    # Existing flow — per-VM action results (serialised dicts from vm_graph)
    vm_action_results: list[dict[str, object]] = field(default_factory=list)

    # Phase 1 — Operational Trust
    preflight_blocks: list[PreflightBlock] = field(default_factory=list)
    service_health_regressions: list[ServiceRegression] = field(default_factory=list)
    reboot_required: list[VMRebootStatus] = field(default_factory=list)
    disk_growth_alerts: list[DiskGrowth] = field(default_factory=list)

    # Phase 2 — Security drift signals
    drift_changes: list[DriftChange] = field(default_factory=list)
    failed_logins: list[FailedLoginSummary] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase B — Proactive daily probe models
# ---------------------------------------------------------------------------


@dataclass
class ProbeVMResult:
    """Result of probing a single VM outside a maintenance batch."""

    vm_id: str
    hostname: str
    reachable: bool = True
    disk_growth_alerts: list[dict[str, object]] = field(default_factory=list)
    drift_changes: list[dict[str, object]] = field(default_factory=list)
    failed_login_summary: dict[str, object] | None = None
    error: str | None = None
    prometheus_metrics: list[str] = field(default_factory=list)
    elk_errors: list[str] = field(default_factory=list)
    journal_errors: list[str] = field(default_factory=list)    # journalctl -p err (live only)
    failed_services: list[str] = field(default_factory=list)   # systemctl --failed (live only)


@dataclass
class DigestReport:
    """Aggregated result of a daily probe run across all VMs in an environment."""

    probe_id: str
    env_name: str
    generated_at: datetime
    vm_results: list[ProbeVMResult] = field(default_factory=list)

    @property
    def reachable_count(self) -> int:
        return sum(1 for r in self.vm_results if r.reachable)

    @property
    def all_disk_alerts(self) -> list[dict[str, object]]:
        return [a for r in self.vm_results for a in r.disk_growth_alerts]

    @property
    def all_drift_changes(self) -> list[dict[str, object]]:
        return [c for r in self.vm_results for c in r.drift_changes]

    @property
    def all_failed_logins(self) -> list[dict[str, object]]:
        return [
            r.failed_login_summary
            for r in self.vm_results
            if r.failed_login_summary is not None
        ]

    @property
    def all_prometheus_metrics(self) -> list[tuple[str, str, list[str]]]:
        """(vm_id, hostname, metrics) tuples for VMs that have Prometheus data."""
        return [
            (r.vm_id, r.hostname, r.prometheus_metrics)
            for r in self.vm_results
            if r.prometheus_metrics
        ]
