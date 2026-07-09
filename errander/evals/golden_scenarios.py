"""Golden fleet scenarios — end-to-end detect-and-propose correctness
(fable-plan Phase 5, the credibility layer).

Distinct from ``replay.py``'s prompt-regression harness (which re-sends a
*stored* prompt to a *new* model and checks response shape). This harness
instead answers a different question: given a synthetic probe digest with a
*known* root cause, does the detector propose the *right* thing — and only
the right thing?

Scored as set-based precision/recall over (vm_id, action_type) pairs:
  - true positive:  expected AND proposed
  - false positive: proposed but NOT expected (the detector over-proposes)
  - false negative: expected but NOT proposed (the detector misses it)

Runs offline by default — pure Python over ``detect_proposals()``, zero I/O,
zero LLM. Scenarios that exercise re-proposal suppression (fable-plan Phase 4)
need a real ``ProposalStore`` to seed rejection history; those are skipped
when no store is supplied (e.g. the CLI runner, which deliberately never
touches a real database — see module docstring in ``main.py``'s
``run_eval_golden_scenarios``). The pytest suite supplies a test-DB-backed
store so suppression scenarios get full coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from errander.agent.proposal_detector import detect_proposals, file_or_suppress_one
from errander.models.proposals import AgentProposal, ProposalKind
from errander.models.reports import DigestReport, ProbeVMResult

if TYPE_CHECKING:
    from errander.safety.audit import AuditStore
    from errander.safety.proposal_store import ProposalStore

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GoldenScenario:
    """One synthetic probe digest with a known-correct detector outcome."""

    name: str
    description: str
    report: DigestReport
    enabled_actions_by_vm: dict[str, set[str]]
    #: (vm_id, action_type) pairs the detector SHOULD file as ACTION proposals.
    expected_action_proposals: set[tuple[str, str]] = field(default_factory=set)
    #: (vm_id, signal_kind) pairs that SHOULD surface as REVIEW-only proposals.
    expected_review_signals: set[tuple[str, str]] = field(default_factory=set)
    #: (vm_id, action_type) pairs to reject twice BEFORE detection runs —
    #: exercises Phase 4 suppression. Requires a real ProposalStore; skipped
    #: in store-less (CLI) mode.
    pre_rejected: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ScenarioResult:
    scenario_name: str
    true_positives: set[tuple[str, str]] = field(default_factory=set)
    false_positives: set[tuple[str, str]] = field(default_factory=set)
    false_negatives: set[tuple[str, str]] = field(default_factory=set)
    review_true_positives: set[tuple[str, str]] = field(default_factory=set)
    review_false_negatives: set[tuple[str, str]] = field(default_factory=set)
    skipped: bool = False

    @property
    def passed(self) -> bool:
        if self.skipped:
            return True
        return not (self.false_positives or self.false_negatives or self.review_false_negatives)


@dataclass
class GoldenEvalSummary:
    results: list[ScenarioResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def precision(self) -> float:
        tp = sum(len(r.true_positives) for r in self.results)
        fp = sum(len(r.false_positives) for r in self.results)
        return tp / (tp + fp) if (tp + fp) else 1.0

    @property
    def recall(self) -> float:
        tp = sum(len(r.true_positives) for r in self.results)
        fn = sum(len(r.false_negatives) for r in self.results)
        return tp / (tp + fn) if (tp + fn) else 1.0


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _report(vm_results: list[ProbeVMResult], *, probe_id: str, env_name: str = "prod") -> DigestReport:
    return DigestReport(probe_id=probe_id, env_name=env_name, generated_at=_NOW, vm_results=vm_results)


def _disk_alert(mountpoint: str = "/", pct: float = 85.0, delta: float = 6.0) -> dict[str, object]:
    return {"mountpoint": mountpoint, "used_pct_end": pct, "delta_pct": delta}


def default_scenarios() -> list[GoldenScenario]:
    """The golden fleet — known root causes, known-correct detector output."""
    return [
        GoldenScenario(
            name="disk_growth_proposes_cleanup",
            description="A VM with a disk-growth alert should get a disk_cleanup proposal.",
            report=_report([ProbeVMResult(
                vm_id="web-01", hostname="10.0.0.1",
                disk_growth_alerts=[_disk_alert()],
            )], probe_id="golden-1"),
            enabled_actions_by_vm={"web-01": {"disk_cleanup", "log_rotation"}},
            expected_action_proposals={("web-01", "disk_cleanup")},
        ),
        GoldenScenario(
            name="var_growth_proposes_cleanup_and_log_rotation",
            description="Growth under /var should additionally propose log_rotation.",
            report=_report([ProbeVMResult(
                vm_id="web-02", hostname="10.0.0.2",
                disk_growth_alerts=[_disk_alert(mountpoint="/var/log")],
            )], probe_id="golden-2"),
            enabled_actions_by_vm={"web-02": {"disk_cleanup", "log_rotation"}},
            expected_action_proposals={
                ("web-02", "disk_cleanup"), ("web-02", "log_rotation"),
            },
        ),
        GoldenScenario(
            name="disabled_action_never_proposed",
            description=(
                "A disk-growth signal on a VM where disk_cleanup is disabled in "
                "inventory must yield NO action proposal — the detector must never "
                "propose work the fleet configuration forbids."
            ),
            report=_report([ProbeVMResult(
                vm_id="db-01", hostname="10.0.0.3",
                disk_growth_alerts=[_disk_alert()],
            )], probe_id="golden-3"),
            enabled_actions_by_vm={"db-01": {"patching"}},  # disk_cleanup NOT enabled
            expected_action_proposals=set(),
        ),
        GoldenScenario(
            name="drift_is_review_only",
            description="Config drift must surface as review-only — never an action.",
            report=_report([ProbeVMResult(
                vm_id="web-03", hostname="10.0.0.4",
                drift_changes=[{"kind": "sudoers", "scope_key": "", "unified_diff": "+bob ALL=(ALL)"}],
            )], probe_id="golden-4"),
            enabled_actions_by_vm={"web-03": {"disk_cleanup", "log_rotation"}},
            expected_action_proposals=set(),
            expected_review_signals={("web-03", "drift")},
        ),
        GoldenScenario(
            name="failed_logins_is_review_only",
            description="A failed-SSH-login spike must surface as review-only.",
            report=_report([ProbeVMResult(
                vm_id="web-04", hostname="10.0.0.5",
                failed_login_summary={
                    "total_count": 50, "window_hours": 24,
                    "top_source_ips": [["1.2.3.4", 30]],
                },
            )], probe_id="golden-5"),
            enabled_actions_by_vm={"web-04": {"disk_cleanup"}},
            expected_action_proposals=set(),
            expected_review_signals={("web-04", "failed_logins")},
        ),
        GoldenScenario(
            name="unreachable_vm_yields_nothing",
            description=(
                "An unreachable VM must never get a proposal, even if a signal "
                "would otherwise trigger one — stale/partial data isn't actionable."
            ),
            report=_report([ProbeVMResult(
                vm_id="web-05", hostname="10.0.0.6", reachable=False,
                disk_growth_alerts=[_disk_alert()],
            )], probe_id="golden-6"),
            enabled_actions_by_vm={"web-05": {"disk_cleanup"}},
            expected_action_proposals=set(),
        ),
        GoldenScenario(
            name="quiet_probe_yields_nothing",
            description="A VM with no flagged signals must get no proposals at all.",
            report=_report([ProbeVMResult(
                vm_id="web-06", hostname="10.0.0.7",
            )], probe_id="golden-7"),
            enabled_actions_by_vm={"web-06": {"disk_cleanup", "log_rotation"}},
            expected_action_proposals=set(),
        ),
        GoldenScenario(
            name="suppressed_pair_not_reproposed",
            description=(
                "A (vm, action) pair rejected twice must NOT be re-proposed by "
                "the next detection cycle (fable-plan Phase 4). Store-dependent — "
                "skipped when no ProposalStore is supplied."
            ),
            report=_report([ProbeVMResult(
                vm_id="web-07", hostname="10.0.0.8",
                disk_growth_alerts=[_disk_alert()],
            )], probe_id="golden-8"),
            enabled_actions_by_vm={"web-07": {"disk_cleanup"}},
            expected_action_proposals=set(),  # suppressed -> nothing filed
            pre_rejected=[("web-07", "disk_cleanup")],
        ),
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _seed_rejections(
    scenario: GoldenScenario, *, proposal_store: ProposalStore,
) -> None:
    """Reject each pre_rejected pair twice, matching the suppression threshold."""
    for vm_id, action_type in scenario.pre_rejected:
        for _ in range(2):
            candidate = AgentProposal(
                env_name=scenario.report.env_name, vm_id=vm_id,
                kind=ProposalKind.ACTION, action_type=action_type,
                signal_kind="disk_growth",
            )
            stored, _ = await proposal_store.create_or_refresh(candidate)
            await proposal_store.decide(
                stored.proposal_id, approved=False, decided_by="ui:golden-eval",
            )


async def run_golden_scenarios(
    scenarios: list[GoldenScenario],
    *,
    proposal_store: ProposalStore | None = None,
    audit_store: AuditStore | None = None,
) -> GoldenEvalSummary:
    """Run each scenario through the detector and score the outcome.

    Store-less (default): pure ``detect_proposals()`` — safe to run against
    any environment, zero database writes. Scenarios that need suppression
    state (``pre_rejected``) are skipped in this mode.

    With a ``proposal_store`` (+ ``audit_store``): full pipeline including
    Phase 4 suppression via ``file_or_suppress_one`` — intended for the test
    suite against an isolated test database, never production.
    """
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        if scenario.pre_rejected and (proposal_store is None or audit_store is None):
            results.append(ScenarioResult(scenario.name, skipped=True))
            continue

        detected = detect_proposals(
            scenario.report, enabled_actions_by_vm=scenario.enabled_actions_by_vm,
        )

        if scenario.pre_rejected:
            assert proposal_store is not None and audit_store is not None
            await _seed_rejections(scenario, proposal_store=proposal_store)
            actual_actions: set[tuple[str, str]] = set()
            for proposal in detected:
                if proposal.kind != ProposalKind.ACTION:
                    continue
                _stored, outcome = await file_or_suppress_one(
                    proposal, store=proposal_store, audit_store=audit_store,
                    suppression_threshold=2, suppression_window_days=14,
                )
                if outcome in ("created", "refreshed"):
                    actual_actions.add((proposal.vm_id, proposal.action_type))
        else:
            actual_actions = {
                (p.vm_id, p.action_type) for p in detected if p.kind == ProposalKind.ACTION
            }

        actual_review = {
            (p.vm_id, p.signal_kind) for p in detected if p.kind == ProposalKind.REVIEW
        }

        results.append(ScenarioResult(
            scenario_name=scenario.name,
            true_positives=actual_actions & scenario.expected_action_proposals,
            false_positives=actual_actions - scenario.expected_action_proposals,
            false_negatives=scenario.expected_action_proposals - actual_actions,
            review_true_positives=actual_review & scenario.expected_review_signals,
            review_false_negatives=scenario.expected_review_signals - actual_review,
        ))
    return GoldenEvalSummary(results)
