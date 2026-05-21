"""Tests for the docker_hygiene Slack reply poller (Session 2b-ii).

poll_hygiene_replies_once fetches thread replies via SlackClient.conversations_replies
and resolves the manager on the first parseable non-bot reply.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from errander.models.docker_hygiene import (
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
)
from errander.safety.hygiene_approval import (
    HygieneApprovalManager,
    poll_hygiene_replies_once,
)


def _dangling(obj_id: str) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.IMAGE_DANGLING,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        object_id=obj_id,
    )


def _assessment(findings: tuple[DockerHygieneFinding, ...]) -> DockerHygieneAssessment:
    return DockerHygieneAssessment(vm_id="v1", findings=findings)


def _slack_client_returning(messages: list[dict[str, object]]) -> object:
    """Build a stub SlackClient whose conversations_replies returns ``messages``."""

    class _Stub:
        conversations_replies = AsyncMock(return_value=messages)

    return _Stub()


class TestPollHygieneRepliesOnce:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_slack_ts(self) -> None:
        """A pending registered without a Slack message ts has nothing to poll."""
        mgr = HygieneApprovalManager()
        pending = mgr.register("b1", "v1", _assessment((_dangling("sha256:a"),)))
        # slack_message_ts defaults to None
        client = _slack_client_returning([])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is False
        # No Slack call happened either
        client.conversations_replies.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_true_when_already_resolved(self) -> None:
        """If pending has already been resolved (e.g., by web channel), poller short-circuits."""
        from errander.models.docker_hygiene import (
            ApprovalSurface,
            DockerHygieneApproval,
            compute_assessment_hash,
        )
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        # Simulate web channel resolving first.
        approval = DockerHygieneApproval(
            vm_id="v1", approved_findings=(),
            snapshot_hash=compute_assessment_hash(a),
            surface=ApprovalSurface.WEB_PAGE,
            operator_id="op",
        )
        # Manager.resolve also pops from pending; mimic that the pending object
        # has been decided.
        mgr.resolve("b1", "v1", approval)
        client = _slack_client_returning([])
        # pending.is_decided() short-circuits before the Slack call.
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is True
        client.conversations_replies.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_skips_bot_messages(self) -> None:
        """The thread parent (posted by the bot) must not be parsed as a reply."""
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        # Only message is a bot_message — should be skipped.
        client = _slack_client_returning([
            {"bot_id": "B0123", "text": "approve dangling 1", "user": "U1"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is False
        # Still pending
        assert len(mgr.get_pending()) == 1

    @pytest.mark.asyncio
    async def test_resolves_on_first_parseable_reply(self) -> None:
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"), _dangling("sha256:b")))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"bot_id": "B0", "text": "(bot's thread parent)", "user": "USLACKBOT"},
            {"text": "approve dangling 1,2", "user": "U_OPERATOR", "ts": "9.0"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is True

        history = mgr.get_history()
        assert len(history) == 1
        approval = history[0].approval
        assert approval is not None
        assert len(approval.approved_findings) == 2
        assert approval.operator_id == "U_OPERATOR"

    @pytest.mark.asyncio
    async def test_ignores_unparseable_then_resolves_on_next(self) -> None:
        """An earlier reply that can't be parsed shouldn't block a later valid reply."""
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"bot_id": "B0", "text": "(bot)", "user": "USLACKBOT"},
            {"text": "lgtm 👍", "user": "U1", "ts": "9.1"},  # doesn't match grammar
            {"text": "approve dangling 1", "user": "U2", "ts": "9.2"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is True
        approval = mgr.get_history()[0].approval
        assert approval is not None
        assert approval.operator_id == "U2"
        assert len(approval.approved_findings) == 1

    @pytest.mark.asyncio
    async def test_reject_reply_resolves_with_empty_approval(self) -> None:
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"bot_id": "B0", "text": "(bot)", "user": "USLACKBOT"},
            {"text": "reject all", "user": "U_OPERATOR", "ts": "9.0"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is True
        approval = mgr.get_history()[0].approval
        assert approval is not None
        assert approval.approved_findings == ()

    @pytest.mark.asyncio
    async def test_subtype_bot_message_also_skipped(self) -> None:
        """Some Slack messages signal bot-ness via subtype rather than bot_id."""
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"subtype": "bot_message", "text": "approve dangling 1", "user": "USLACKBOT"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is False
        # Still pending — bot's message wasn't parsed
        assert len(mgr.get_pending()) == 1

    @pytest.mark.asyncio
    async def test_empty_text_skipped(self) -> None:
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"bot_id": "B0", "text": "(bot)", "user": "USLACKBOT"},
            {"text": "", "user": "U1"},
            {"text": "   ", "user": "U2"},
        ])
        resolved = await poll_hygiene_replies_once(client, pending, mgr)
        assert resolved is False

    @pytest.mark.asyncio
    async def test_first_match_wins_subsequent_ignored(self) -> None:
        """If two valid replies exist, only the first wins; manager.resolve is idempotent."""
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"), _dangling("sha256:b")))
        pending = mgr.register("b1", "v1", a, slack_message_ts="1234.5678")
        client = _slack_client_returning([
            {"bot_id": "B0", "text": "(bot)", "user": "USLACKBOT"},
            {"text": "approve dangling 1", "user": "FIRST", "ts": "9.0"},
            {"text": "approve dangling 2", "user": "SECOND", "ts": "9.1"},
        ])
        await poll_hygiene_replies_once(client, pending, mgr)
        approval = mgr.get_history()[0].approval
        assert approval is not None
        # FIRST reply wins — only sha256:a approved, not sha256:b
        ids = {f.object_id for f in approval.approved_findings}
        assert ids == {"sha256:a"}
        assert approval.operator_id == "FIRST"
