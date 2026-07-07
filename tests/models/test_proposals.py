"""Tests for AgentProposal models — validation is the guardrail (fable-plan §5.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from errander.models.proposals import (
    PROPOSABLE_ACTIONS,
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
    ProposalStatus,
)


def _action_proposal(**overrides: object) -> AgentProposal:
    defaults: dict[str, object] = {
        "env_name": "prod",
        "vm_id": "web-01",
        "kind": ProposalKind.ACTION,
        "action_type": "disk_cleanup",
        "signal_kind": "disk_growth",
    }
    defaults.update(overrides)
    return AgentProposal(**defaults)  # type: ignore[arg-type]


class TestActionTypeValidation:
    def test_proposable_action_accepted(self) -> None:
        p = _action_proposal(action_type="log_rotation")
        assert p.action_type == "log_rotation"

    def test_destructive_action_rejected(self) -> None:
        """docker_hygiene is NOT proposable — object-level approval only."""
        with pytest.raises(ValidationError, match="proposable action set"):
            _action_proposal(action_type="docker_hygiene")

    def test_free_text_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _action_proposal(action_type="rm -rf /")

    def test_proposable_set_is_low_risk_only(self) -> None:
        assert {"disk_cleanup", "log_rotation"} == PROPOSABLE_ACTIONS


class TestIdentifierValidation:
    @pytest.mark.parametrize("bad", ["web 01", "web;01", "vm`x`", "../etc", ""])
    def test_shell_metacharacters_rejected_in_vm_id(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            _action_proposal(vm_id=bad)

    def test_env_name_validated(self) -> None:
        with pytest.raises(ValidationError):
            _action_proposal(env_name="prod|staging")


class TestKindConsistency:
    def test_action_requires_action_type(self) -> None:
        with pytest.raises(ValidationError, match="require an action_type"):
            AgentProposal(
                env_name="prod", vm_id="web-01",
                kind=ProposalKind.ACTION, signal_kind="disk_growth",
            )

    def test_review_must_not_carry_action_type(self) -> None:
        with pytest.raises(ValidationError, match="must not carry"):
            AgentProposal(
                env_name="prod", vm_id="web-01",
                kind=ProposalKind.REVIEW, action_type="disk_cleanup",
                signal_kind="drift",
            )


class TestActionKeyAndProperties:
    def test_action_key_for_action(self) -> None:
        assert _action_proposal().action_key == "disk_cleanup"

    def test_action_key_for_review_includes_signal(self) -> None:
        p = AgentProposal(
            env_name="prod", vm_id="web-01",
            kind=ProposalKind.REVIEW, signal_kind="drift",
        )
        assert p.action_key == "review:drift"
        assert p.is_actionable is False

    def test_action_is_actionable(self) -> None:
        assert _action_proposal().is_actionable is True

    def test_defaults(self) -> None:
        p = _action_proposal()
        assert p.status == ProposalStatus.PENDING
        assert p.origin == "probe_detector"
        assert p.proposal_id  # uuid assigned


class TestEvidence:
    def test_long_observation_truncated(self) -> None:
        ev = ProposalEvidence(
            source="probe:disk_history", check="c", observation="x" * 2000,
        )
        assert len(ev.observation) == 500
        assert ev.observation.endswith("…")
