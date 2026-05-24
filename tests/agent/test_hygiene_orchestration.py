"""Tests for Session 2b-iii: docker_hygiene batch orchestration wiring.

Covers _run_docker_hygiene's assess → Slack post → wait → execute loop,
including approval, rejection, timeout, and short-circuit paths.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.models.actions import ActionStatus, ActionType
from errander.models.docker_hygiene import (
    ApprovalSurface,
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)
from errander.safety.hygiene_approval import HygieneApprovalManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assessment(n_cleanup: int = 2) -> DockerHygieneAssessment:
    """Build an assessment with n_cleanup IMAGE_DANGLING cleanup candidates."""
    findings = [
        DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id=f"sha256:{'a' * (64 - len(str(i)))}{i}",
            size_bytes=10_000_000,
            age_days=30,
        )
        for i in range(n_cleanup)
    ]
    return DockerHygieneAssessment(vm_id="test-vm", findings=findings)


def _assess_state(assessment: DockerHygieneAssessment) -> dict[str, object]:
    return {
        "status": ActionStatus.SKIPPED.value,
        "assessment": assessment,
        "removal_results": None,
        "error": None,
    }


def _exec_state(assessment: DockerHygieneAssessment) -> dict[str, object]:
    return {
        "status": "completed",
        "assessment": assessment,
        "removal_results": (),
        "error": None,
    }


def _vm_state(
    *,
    dry_run: bool = False,
    action_params: dict[str, object] | None = None,
    docker_command_mode: str = "wrapper",
) -> dict[str, object]:
    return {
        "vm_id": "test-vm",
        "batch_id": "batch-001",
        "dry_run": dry_run,
        "hostname": "10.0.0.1",
        "ssh_user": "errander",
        "ssh_key_path": "/home/errander/.ssh/id_ed25519",
        "os_family": "ubuntu",
        "docker_command_mode": docker_command_mode,
        "vm_info": {"docker_available": True},
        "planned_actions": [
            {
                "action_type": ActionType.DOCKER_HYGIENE.value,
                "risk_tier": "medium",
                "params": action_params or {},
            }
        ],
        "current_action_index": 0,
        "results": [],
    }


def _make_approval(assessment: DockerHygieneAssessment) -> DockerHygieneApproval:
    return DockerHygieneApproval(
        vm_id="test-vm",
        approved_findings=tuple(assessment.cleanup_candidates()),
        snapshot_hash=compute_assessment_hash(assessment),
        surface=ApprovalSurface.SLACK_REPLY,
        operator_id="tester",
    )


def _make_rejection(assessment: DockerHygieneAssessment) -> DockerHygieneApproval:
    return DockerHygieneApproval(
        vm_id="test-vm",
        approved_findings=(),
        snapshot_hash=compute_assessment_hash(assessment),
        surface=ApprovalSurface.SLACK_REPLY,
        operator_id="tester",
    )


# ---------------------------------------------------------------------------
# TestRunDockerHygieneOrchestration
# ---------------------------------------------------------------------------

class TestRunDockerHygieneOrchestration:
    """Tests for the full assess → approve → execute loop in _run_docker_hygiene."""

    @pytest.mark.asyncio
    async def test_approve_path_posts_slack_waits_then_executes(self) -> None:
        """Happy path: assessment has cleanup candidates → Slack message posted,
        manager registers, operator approves, sub-graph re-invoked with approval."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=2)
        state = _vm_state(dry_run=False)
        manager = HygieneApprovalManager()

        slack = AsyncMock()
        slack.post_message = AsyncMock(return_value="1716300000.000001")

        invoke_calls: list[dict[str, object]] = []
        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            has_approval = "approval" in sub_state
            invoke_calls.append({"has_approval": has_approval})
            if not has_approval:
                return _assess_state(assessment)
            return _exec_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        # Schedule resolution after a tiny delay so wait_for_decision doesn't
        # block forever.
        approval = _make_approval(assessment)

        async def _resolve_soon() -> None:
            await asyncio.sleep(0.05)
            manager.resolve("batch-001", "test-vm", approval)

        with patch("errander.safety.hygiene_approval.poll_hygiene_replies_once", new_callable=AsyncMock):
            resolve_task = asyncio.create_task(_resolve_soon())
            result = await _run_docker_hygiene(
                state,  # type: ignore[arg-type]
                compiled,
                hygiene_manager=manager,
                slack_client=slack,
                approval_timeout_seconds=10,
                approval_poll_interval_seconds=60,
            )
            await resolve_task

        # Slack message was posted
        slack.post_message.assert_called_once()

        # Sub-graph invoked twice (assess + execute)
        assert len(invoke_calls) == 2
        assert invoke_calls[0]["has_approval"] is False
        assert invoke_calls[1]["has_approval"] is True

        assert result["action_type"] == ActionType.DOCKER_HYGIENE.value
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_rejection_path_returns_skipped_no_execute(self) -> None:
        """Operator rejects → status=SKIPPED, sub-graph not re-invoked for execute."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=2)
        state = _vm_state(dry_run=False)
        manager = HygieneApprovalManager()

        slack = AsyncMock()
        slack.post_message = AsyncMock(return_value="1716300000.000002")

        invoke_count = 0
        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            nonlocal invoke_count
            invoke_count += 1
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        rejection = _make_rejection(assessment)

        async def _reject_soon() -> None:
            await asyncio.sleep(0.05)
            manager.resolve("batch-001", "test-vm", rejection)

        with patch("errander.safety.hygiene_approval.poll_hygiene_replies_once", new_callable=AsyncMock):
            reject_task = asyncio.create_task(_reject_soon())
            result = await _run_docker_hygiene(
                state,  # type: ignore[arg-type]
                compiled,
                hygiene_manager=manager,
                slack_client=slack,
                approval_timeout_seconds=10,
                approval_poll_interval_seconds=60,
            )
            await reject_task

        assert result["status"] == ActionStatus.SKIPPED.value
        assert "rejected" in str(result["detail"])
        assert invoke_count == 1  # only assess, no execute

    @pytest.mark.asyncio
    async def test_timeout_path_returns_skipped(self) -> None:
        """No decision within timeout → status=SKIPPED with 'approval timeout'."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=1)
        state = _vm_state(dry_run=False)
        manager = HygieneApprovalManager()

        slack = AsyncMock()
        slack.post_message = AsyncMock(return_value="1716300000.000003")

        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        with patch("errander.safety.hygiene_approval.poll_hygiene_replies_once", new_callable=AsyncMock):
            result = await _run_docker_hygiene(
                state,  # type: ignore[arg-type]
                compiled,
                hygiene_manager=manager,
                slack_client=slack,
                approval_timeout_seconds=0,  # immediate timeout
                approval_poll_interval_seconds=60,
            )

        assert result["status"] == ActionStatus.SKIPPED.value
        assert "timeout" in str(result["detail"])

    @pytest.mark.asyncio
    async def test_nothing_to_surface_skips_approval(self) -> None:
        """Assessment with no cleanup candidates → returns assess result, no Slack."""
        from errander.agent.vm_graph import _run_docker_hygiene

        # All findings are INVESTIGATE — no cleanup_candidates
        investigation_finding = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_UNUSED,
            classification=FindingClassification.INVESTIGATE,
            object_id="sha256:" + "b" * 64,
            size_bytes=5_000_000,
            age_days=5,
        )
        assessment = DockerHygieneAssessment(vm_id="test-vm", findings=[investigation_finding])
        state = _vm_state(dry_run=False)
        manager = HygieneApprovalManager()

        slack = AsyncMock()

        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        result = await _run_docker_hygiene(
            state,  # type: ignore[arg-type]
            compiled,
            hygiene_manager=manager,
            slack_client=slack,
            approval_timeout_seconds=10,
        )

        slack.post_message.assert_not_called()
        assert result["status"] == ActionStatus.SKIPPED.value

    @pytest.mark.asyncio
    async def test_dry_run_skips_approval(self) -> None:
        """dry_run=True → assess only, no Slack message, no approval wait."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=3)
        state = _vm_state(dry_run=True)
        manager = HygieneApprovalManager()

        slack = AsyncMock()

        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        result = await _run_docker_hygiene(
            state,  # type: ignore[arg-type]
            compiled,
            hygiene_manager=manager,
            slack_client=slack,
            approval_timeout_seconds=10,
        )

        slack.post_message.assert_not_called()
        assert result["status"] == ActionStatus.SKIPPED.value

    @pytest.mark.asyncio
    async def test_no_manager_skips_execution(self) -> None:
        """hygiene_manager=None → no approval possible → SKIPPED with detail."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=2)
        state = _vm_state(dry_run=False)

        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        result = await _run_docker_hygiene(
            state,  # type: ignore[arg-type]
            compiled,
            hygiene_manager=None,  # explicit None
            approval_timeout_seconds=10,
        )

        assert result["status"] == ActionStatus.SKIPPED.value
        assert "no approval manager" in str(result["detail"])

    @pytest.mark.asyncio
    async def test_pre_injected_approval_fast_path(self) -> None:
        """Approval pre-injected in action_params (test / replay) → direct execute."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=1)
        pre_approval = _make_approval(assessment)

        state = _vm_state(
            dry_run=False,
            action_params={"approval": pre_approval},
        )
        manager = HygieneApprovalManager()
        slack = AsyncMock()

        invoke_calls: list[dict[str, object]] = []
        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            invoke_calls.append({"has_approval": "approval" in sub_state})
            return _exec_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        result = await _run_docker_hygiene(
            state,  # type: ignore[arg-type]
            compiled,
            hygiene_manager=manager,
            slack_client=slack,
            approval_timeout_seconds=10,
        )

        # Slack NOT posted (fast path bypasses approval gate)
        slack.post_message.assert_not_called()

        # Only one invoke call, which includes the pre-injected approval
        assert len(invoke_calls) == 1
        assert invoke_calls[0]["has_approval"] is True

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_signed_url_included_in_slack_message(self) -> None:
        """When web_base_url is set and signing secret available, Slack message
        contains the web approval URL."""
        from errander.agent.vm_graph import _run_docker_hygiene

        assessment = _make_assessment(n_cleanup=1)
        state = _vm_state(dry_run=False)
        manager = HygieneApprovalManager()

        captured_text: list[str] = []
        slack = AsyncMock()
        slack.post_message = AsyncMock(side_effect=lambda text: (captured_text.append(text) or "ts.001"))

        async def _fake_invoke(sub_state: dict[str, object]) -> dict[str, object]:
            return _assess_state(assessment)

        compiled = MagicMock()
        compiled.ainvoke = _fake_invoke

        # Resolve approval immediately
        async def _resolve() -> None:
            await asyncio.sleep(0.02)
            manager.resolve("batch-001", "test-vm", _make_approval(assessment))

        with (
            patch("errander.safety.hygiene_approval.poll_hygiene_replies_once", new_callable=AsyncMock),
            patch.dict("os.environ", {"ERRANDER_SIGNING_SECRET": "a" * 44}),
        ):
            resolve_task = asyncio.create_task(_resolve())
            await _run_docker_hygiene(
                state,  # type: ignore[arg-type]
                compiled,
                hygiene_manager=manager,
                slack_client=slack,
                web_base_url="http://10.0.0.5:9090",
                approval_timeout_seconds=10,
                approval_poll_interval_seconds=60,
            )
            await resolve_task

        assert captured_text, "Slack post_message was not called"
        assert "/ui/docker-hygiene/approve?token=" in captured_text[0]
