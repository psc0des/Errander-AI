"""Probe-triggered investigations (detect-and-propose, fable-plan Phase 3).

What makes detect-and-propose genuinely *agentic* rather than a chatbot: the
daily probe's anomaly signals can automatically launch a bounded, read-only
investigation (InvestigationAgent, Phase 2) that *enriches* the Phase 1
detector's proposals with correlated evidence — never bypassing the
detector's dedup, never executing anything itself.

Design decisions (see tasks/fable-plan.md Phase 3):

- **VM-level dedup, not per-signal-kind.** One investigation call per
  affected VM covers all of that VM's flagged signals for the probe (cheaper,
  simpler, one budget/fallback per VM — "an investigation run per affected
  VM" per the plan). The dedup window is therefore keyed on vm_id: a VM
  investigated within ``dedup_hours`` is skipped even if a *new* signal kind
  appeared on it. This is a deliberate simplification over strict
  per-(vm, signal_kind) throttling — documented here and in the plan.
- **Dedup only counts genuine successes.** The marker is
  ``get_decisions(batch_id=f"probe-trigger:{vm_id}", decision_type=
  "investigation_agent")`` filtered to ``outcome == "success"``. A prior
  failure/fallback does not block a retry on the next probe.
- **A cheap no-op fallback, not OperatorAssistant.** D2 says LLM-down leaves
  the Phase 1 template proposal untouched — spending a second, full
  FleetContext-building LLM call as "the fallback" would be wasteful and
  pointless here. :class:`NoOpFallback` returns instantly with an empty
  response, so "nothing to merge" is exactly what happens.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from errander.agent.investigation_agent import (
    InvestigationAgent,
    proposed_work_to_proposals,
)
from errander.models.events import AuditEvent, EventType
from errander.models.proposals import AgentProposal, ProposalEvidence

if TYPE_CHECKING:
    from errander.agent.investigation_tools import ToolRegistry
    from errander.integrations.llm import LLMClient
    from errander.models.analysis import AssistantResponse
    from errander.safety.ai_audit import AIDecisionStore
    from errander.safety.audit import AuditStore
    from errander.safety.proposal_store import ProposalStore

logger = logging.getLogger(__name__)

_BATCH_PREFIX = "probe-trigger:"


class NoOpFallback:
    """Instant, empty fallback — satisfies InvestigationFallback without
    spending an LLM call. See module docstring: D2 for the trigger path."""

    async def investigate(self, question: str = "", **kwargs: Any) -> AssistantResponse:
        # question defaults to "" — the trigger calls this with fallback_kwargs={}
        # (InvestigationAgent._fallback does `fallback.investigate(**fallback_kwargs)`,
        # so the param must be optional here even though the Protocol requires it).
        from errander.models.analysis import AssistantResponse as _Resp
        return _Resp(summary="", findings=[], recommendations=[], risk_level="unknown")


def group_candidates_by_vm(
    stored: list[AgentProposal],
) -> dict[str, list[AgentProposal]]:
    """Group this probe's filed/refreshed proposals by VM, order-preserving.

    Pure — no I/O. Only meaningful for still-pending proposals (the caller
    only ever passes what file_proposals() just touched, which is always
    pending at that point).
    """
    grouped: dict[str, list[AgentProposal]] = {}
    for proposal in stored:
        grouped.setdefault(proposal.vm_id, []).append(proposal)
    return grouped


async def _recently_investigated(
    ai_decision_store: AIDecisionStore | None,
    vm_id: str,
    dedup_hours: int,
) -> bool:
    """True iff a *successful* triggered investigation ran for vm_id recently."""
    if ai_decision_store is None:
        return False
    decisions = await ai_decision_store.get_decisions(
        batch_id=f"{_BATCH_PREFIX}{vm_id}",
        decision_type="investigation_agent",
        limit=1,
    )
    if not decisions or decisions[0].outcome != "success":
        return False
    cutoff = datetime.now(tz=UTC) - timedelta(hours=dedup_hours)
    ts = decisions[0].timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= cutoff


async def select_investigation_targets(
    candidates: dict[str, list[AgentProposal]],
    *,
    ai_decision_store: AIDecisionStore | None,
    dedup_hours: int,
    max_per_probe: int,
) -> list[str]:
    """Return the vm_ids to investigate this probe — deduped and capped."""
    selected: list[str] = []
    for vm_id in candidates:
        if len(selected) >= max_per_probe:
            break
        if await _recently_investigated(ai_decision_store, vm_id, dedup_hours):
            logger.info(
                "Probe-triggered investigation skipped for %s — investigated "
                "within the last %dh", vm_id, dedup_hours,
            )
            continue
        selected.append(vm_id)
    return selected


def _build_question(vm_id: str, proposals: list[AgentProposal]) -> str:
    lines = [
        f"Investigate the following flagged signal(s) on VM {vm_id} and "
        "recommend any warranted LOW-risk work:",
    ]
    for p in proposals:
        lines.append(f"- signal: {p.signal_kind} (proposed: {p.kind.value}:{p.action_key})")
        for ev in p.evidence:
            lines.append(f"  evidence [{ev.source}] {ev.check}: {ev.observation}")
    return "\n".join(lines)


async def _enrich_proposal(
    proposal: AgentProposal,
    response: AssistantResponse,
    *,
    proposal_store: ProposalStore,
    audit_store: AuditStore,
) -> None:
    """Merge investigation findings onto an existing open proposal.

    Refreshes via the SAME dedup path file_proposals() uses
    (create_or_refresh on vm_id+action_key) — never a parallel write path.
    """
    merged_evidence = list(proposal.evidence) + [
        ProposalEvidence(
            source="investigation_agent",
            check="probe-triggered agentic enrichment",
            observation=f.text,
        )
        for f in response.findings
    ]
    confidence = (
        "high" if response.risk_level in ("medium", "high") else proposal.confidence
    )
    enriched = proposal.model_copy(update={
        "evidence": merged_evidence,
        "confidence": confidence,
    })
    stored, _created = await proposal_store.create_or_refresh(enriched)
    await audit_store.log_event(AuditEvent(
        event_type=EventType.PROPOSAL_REFRESHED,
        batch_id=stored.probe_id or "probe-trigger",
        vm_id=stored.vm_id,
        action_type=stored.action_type or None,
        detail=(
            f"proposal {stored.proposal_id} enriched by investigation_agent: "
            f"+{len(response.findings)} finding(s), confidence={confidence}"
        ),
        metadata={"proposal_id": stored.proposal_id, "trigger": True},
    ))


async def run_triggered_investigations(
    stored_this_probe: list[AgentProposal],
    *,
    env_name: str,
    valid_vm_ids: set[str],
    tools: ToolRegistry,
    llm_client: LLMClient,
    proposal_store: ProposalStore,
    audit_store: AuditStore,
    ai_decision_store: AIDecisionStore | None,
    max_investigations_per_probe: int,
    dedup_hours: int,
    max_tool_calls: int,
    timeout_seconds: int,
    probe_id: str = "",
) -> int:
    """Run bounded investigations for affected VMs; enrich their proposals.

    Never raises — a trigger failure must not break the probe digest (mirrors
    the Phase 1 detector's own contract). Returns the number of VMs actually
    investigated.
    """
    candidates = group_candidates_by_vm(stored_this_probe)
    if not candidates:
        return 0

    try:
        targets = await select_investigation_targets(
            candidates,
            ai_decision_store=ai_decision_store,
            dedup_hours=dedup_hours,
            max_per_probe=max_investigations_per_probe,
        )
    except Exception as exc:  # noqa: BLE001 — trigger must never break the probe
        logger.error("Investigation trigger candidate selection failed: %s", exc)
        return 0

    agent = InvestigationAgent(
        max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds,
    )
    investigated = 0
    for vm_id in targets:
        try:
            question = _build_question(vm_id, candidates[vm_id])
            response = await agent.investigate_agentic(
                question,
                tools=tools,
                llm_client=llm_client,
                fallback=NoOpFallback(),
                fallback_kwargs={},
                ai_decision_store=ai_decision_store,
                batch_id=f"{_BATCH_PREFIX}{vm_id}",
            )
            investigated += 1

            if not response.findings and not response.proposed_work:
                continue  # nothing to merge — D2: proposal stands untouched

            for proposal in candidates[vm_id]:
                await _enrich_proposal(
                    proposal, response,
                    proposal_store=proposal_store, audit_store=audit_store,
                )

            if response.proposed_work:
                new_proposals = proposed_work_to_proposals(
                    response, env_name=env_name, valid_vm_ids=valid_vm_ids,
                    probe_id=probe_id,
                )
                for new_proposal in new_proposals:
                    stored, created = await proposal_store.create_or_refresh(
                        new_proposal,
                    )
                    await audit_store.log_event(AuditEvent(
                        event_type=(
                            EventType.PROPOSAL_CREATED if created
                            else EventType.PROPOSAL_REFRESHED
                        ),
                        batch_id=stored.probe_id or "probe-trigger",
                        vm_id=stored.vm_id,
                        action_type=stored.action_type or None,
                        detail=(
                            f"proposal {stored.proposal_id}: {stored.action_key} "
                            "(origin=investigation_agent, probe-triggered)"
                        ),
                        metadata={"proposal_id": stored.proposal_id, "trigger": True},
                    ))
        except Exception as exc:  # noqa: BLE001 — one VM must not kill the loop
            logger.error(
                "Probe-triggered investigation failed for %s: %s", vm_id, exc,
            )
    return investigated
