"""Deterministic proposal detector (detect-and-propose, fable-plan Phase 1).

Turns daily-probe signals into template AgentProposals — **no LLM anywhere
in this module** (design decision D2: the detector is the permanent
fallback; the Phase 2/3 investigation agent only ever *enriches* what these
rules admit). Layer B classification: deterministic Python, read-only, its
output is a suggestion record a named operator must decide in the web UI.

Rules (conservative, mirroring _check_escalation thresholds in probe.py):
- disk growth alert            → ACTION proposal: disk_cleanup
  (+ log_rotation when the growing mountpoint is under /var)
- config drift detected        → REVIEW proposal (surfaces evidence only)
- failed SSH logins > 20 / 24h → REVIEW proposal (surfaces evidence only)

ACTION proposals are filed only for VMs where the action is enabled in
inventory — the detector never proposes work the fleet configuration
forbids.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from errander.models.events import AuditEvent, EventType
from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
)

if TYPE_CHECKING:
    from errander.models.reports import DigestReport, ProbeVMResult
    from errander.safety.audit import AuditStore
    from errander.safety.proposal_store import ProposalStore

logger = logging.getLogger(__name__)

#: Failed-login count (24h window) above which a review proposal is filed.
#: Matches the escalation threshold in probe._check_escalation.
FAILED_LOGIN_THRESHOLD = 20


def _disk_confidence(alert: dict[str, object]) -> str:
    """High when the disk is nearly full or growing fast — else medium."""
    pct = float(str(alert.get("used_pct_end", 0)))
    delta = float(str(alert.get("delta_pct", 0)))
    return "high" if pct >= 90.0 or delta >= 15.0 else "medium"


def _disk_evidence(alert: dict[str, object]) -> ProposalEvidence:
    mount = str(alert.get("mountpoint", "?"))
    pct = float(str(alert.get("used_pct_end", 0)))
    delta = float(str(alert.get("delta_pct", 0)))
    return ProposalEvidence(
        source="probe:disk_history",
        check=f"disk growth trend for {mount} over the probe window",
        observation=f"{mount} at {pct:.0f}% used, +{delta:.1f}% over window",
    )


def _detect_for_vm(
    vm: ProbeVMResult,
    *,
    env_name: str,
    probe_id: str,
    enabled_actions: set[str],
) -> list[AgentProposal]:
    """Apply the detection rules to one VM's probe result."""
    proposals: list[AgentProposal] = []

    # --- disk growth → disk_cleanup (+ log_rotation for /var mounts) ---
    if vm.disk_growth_alerts:
        evidence = [_disk_evidence(a) for a in vm.disk_growth_alerts]
        confidence = max(
            (_disk_confidence(a) for a in vm.disk_growth_alerts),
            key=lambda c: c == "high",
        )
        wanted: list[str] = ["disk_cleanup"]
        if any(
            str(a.get("mountpoint", "")).startswith("/var")
            for a in vm.disk_growth_alerts
        ):
            wanted.append("log_rotation")
        for action_type in wanted:
            if action_type not in enabled_actions:
                logger.info(
                    "Detector: skipping %s proposal for %s — action not enabled "
                    "in inventory",
                    action_type, vm.vm_id,
                )
                continue
            proposals.append(AgentProposal(
                env_name=env_name,
                vm_id=vm.vm_id,
                kind=ProposalKind.ACTION,
                action_type=action_type,
                signal_kind="disk_growth",
                probe_id=probe_id,
                evidence=list(evidence),
                confidence=confidence,
            ))

    # --- config drift → review-only (a human must look; nothing to run) ---
    if vm.drift_changes:
        drift_evidence = [
            ProposalEvidence(
                source="probe:drift_baseline",
                check=(
                    f"baseline comparison: {change.get('kind', '?')}"
                    + (
                        f" ({change.get('scope_key')})"
                        if change.get("scope_key") else ""
                    )
                ),
                observation=str(
                    change.get("unified_diff", "") or "content changed"
                ),
            )
            for change in vm.drift_changes
        ]
        proposals.append(AgentProposal(
            env_name=env_name,
            vm_id=vm.vm_id,
            kind=ProposalKind.REVIEW,
            signal_kind="drift",
            probe_id=probe_id,
            evidence=drift_evidence,
            confidence="high",
        ))

    # --- failed SSH logins above threshold → review-only ---
    summary = vm.failed_login_summary
    if summary is not None:
        total = int(str(summary.get("total_count", 0)))
        if total > FAILED_LOGIN_THRESHOLD:
            top_ips = summary.get("top_source_ips", [])
            proposals.append(AgentProposal(
                env_name=env_name,
                vm_id=vm.vm_id,
                kind=ProposalKind.REVIEW,
                signal_kind="failed_logins",
                probe_id=probe_id,
                evidence=[ProposalEvidence(
                    source="probe:failed_logins",
                    check=(
                        f"failed SSH login count over "
                        f"{summary.get('window_hours', 24)}h window"
                    ),
                    observation=(
                        f"{total} failed logins; top sources: {top_ips}"
                    ),
                )],
                confidence="medium",
            ))

    return proposals


def detect_proposals(
    report: DigestReport,
    *,
    enabled_actions_by_vm: dict[str, set[str]],
) -> list[AgentProposal]:
    """Run the detection rules over a probe digest. Pure — no I/O.

    Args:
        report: The digest from run_env_probe.
        enabled_actions_by_vm: vm_id → action names enabled in inventory.
            ACTION proposals are only emitted for enabled actions.
    """
    proposals: list[AgentProposal] = []
    for vm in report.vm_results:
        if not vm.reachable:
            continue
        proposals.extend(_detect_for_vm(
            vm,
            env_name=report.env_name,
            probe_id=report.probe_id,
            enabled_actions=enabled_actions_by_vm.get(vm.vm_id, set()),
        ))
    return proposals


async def file_proposals(
    proposals: list[AgentProposal],
    *,
    store: ProposalStore,
    audit_store: AuditStore,
) -> tuple[int, int, list[AgentProposal]]:
    """Persist detected proposals (dedup-aware) and audit each transition.

    Returns ``(created, refreshed, stored_proposals)`` — the third element is
    every stored row touched this call (with real, persisted proposal_ids),
    so a caller (e.g. the Phase 3 trigger) knows exactly what to act on
    without a second query.
    """
    created_count = 0
    refreshed_count = 0
    stored_proposals: list[AgentProposal] = []
    for proposal in proposals:
        stored, created = await store.create_or_refresh(proposal)
        stored_proposals.append(stored)
        if created:
            created_count += 1
        else:
            refreshed_count += 1
        await audit_store.log_event(AuditEvent(
            event_type=(
                EventType.PROPOSAL_CREATED if created
                else EventType.PROPOSAL_REFRESHED
            ),
            batch_id=stored.probe_id or proposal.probe_id,
            vm_id=stored.vm_id,
            action_type=stored.action_type or None,
            detail=(
                f"proposal {stored.proposal_id}: {stored.kind.value} "
                f"{stored.action_key} (signal={stored.signal_kind}, "
                f"confidence={stored.confidence}, origin={stored.origin})"
            ),
            metadata={"proposal_id": stored.proposal_id},
        ))
    return created_count, refreshed_count, stored_proposals
