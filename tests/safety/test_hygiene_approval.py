"""Tests for the docker_hygiene approval surface.

Covers:
- Slack notification formatter (format_hygiene_approval_message) — since R2
  this is notify-and-link: it must point at the web approval page and must
  NOT carry reply-command instructions.
- HygieneApprovalManager (register/resolve/wait) — resolved only by the web
  approval handler.

The pre-R2 Slack reply parser (parse_hygiene_reply) was removed with the
Slack decision channel; its tests went with it.
"""

from __future__ import annotations

import asyncio

import pytest

from errander.models.docker_hygiene import (
    ApprovalSurface,
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)
from errander.safety.hygiene_approval import (
    HygieneApprovalManager,
    format_hygiene_approval_message,
)

# --- Builders ---

def _dangling(obj_id: str, size: int = 100, age: int = 10) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.IMAGE_DANGLING,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        object_id=obj_id,
        size_bytes=size,
        age_days=age,
    )


def _unused(obj_id: str, age: int = 90, tag: str = "img:v1") -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.IMAGE_UNUSED,
        classification=(
            FindingClassification.CLEANUP_CANDIDATE if age > 30 else FindingClassification.REPORT_ONLY
        ),
        object_id=obj_id,
        size_bytes=1000,
        age_days=age,
        last_tag=tag,
    )


def _container(obj_id: str, name: str, exit_code: int = 0, age_hours: int = 200) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.CONTAINER_STOPPED,
        classification=(
            FindingClassification.CLEANUP_CANDIDATE
            if exit_code == 0 and age_hours > 168
            else FindingClassification.INVESTIGATE if exit_code in (137, 139)
            else FindingClassification.REPORT_ONLY
        ),
        object_id=obj_id,
        name=name,
        exit_code=exit_code,
        stopped_age_hours=age_hours,
    )


def _volume(name: str) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.VOLUME_UNREFERENCED,
        classification=FindingClassification.REPORT_ONLY,
        name=name,
        size_bytes=1024,
        last_mount_days=30,
    )


def _volume_candidate(name: str, last_mount_days: int = 120) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.VOLUME_UNREFERENCED,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        name=name,
        size_bytes=1024,
        last_mount_days=last_mount_days,
    )


def _build_cache_candidate(reclaimable: int = 5_000_000) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.BUILD_CACHE,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        name="build_cache",
        reclaimable_bytes=reclaimable,
    )


def _assessment(findings: tuple[DockerHygieneFinding, ...]) -> DockerHygieneAssessment:
    return DockerHygieneAssessment(
        vm_id="prod/web-01",
        findings=findings,
    )


def _web_approval(
    a: DockerHygieneAssessment,
    findings: tuple[DockerHygieneFinding, ...],
    operator_id: str = "ui:tester",
) -> DockerHygieneApproval:
    """Build the approval artifact the web handler produces."""
    return DockerHygieneApproval(
        vm_id=a.vm_id,
        approved_findings=findings,
        snapshot_hash=compute_assessment_hash(a),
        surface=ApprovalSurface.WEB_PAGE,
        operator_id=operator_id,
    )


# ---------------------------------------------------------------------------
# Slack notification formatter (notify-and-link)
# ---------------------------------------------------------------------------

class TestFormatMessage:
    def test_empty_assessment_says_no_findings(self) -> None:
        msg = format_hygiene_approval_message(_assessment(()))
        assert "No findings" in msg

    def test_lists_findings_with_indices(self) -> None:
        a = _assessment((_dangling("sha256:abc"), _dangling("sha256:def")))
        msg = format_hygiene_approval_message(a)
        assert "dangling.1" in msg
        assert "dangling.2" in msg
        assert "image_dangling" in msg

    def test_groups_by_class(self) -> None:
        a = _assessment((
            _dangling("sha256:a"),
            _container("c1", "worker"),
            _unused("sha256:u"),
        ))
        msg = format_hygiene_approval_message(a)
        # Each class header appears
        assert "image_dangling" in msg
        assert "image_unused" in msg
        assert "container_stopped" in msg
        # And the operator-facing short keys mirrored by the web form
        assert "dangling.1" in msg
        assert "images.1" in msg
        assert "containers.1" in msg

    def test_no_reply_command_instructions(self) -> None:
        """R2: Slack is notify-and-link — no reply syntax that implies authority."""
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(a)
        assert "Reply with" not in msg
        assert "approve <class>" not in msg
        assert "reject all" not in msg

    def test_points_at_web_approval(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(a)
        assert "Approval required" in msg

    def test_includes_web_url_when_provided(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(
            a,
            web_approval_url="https://errander.internal/ui/approve?token=xyz",
        )
        assert "https://errander.internal/ui/approve?token=xyz" in msg

    def test_report_only_classes_marked(self) -> None:
        a = _assessment((_volume("pgdata_old"),))
        msg = format_hygiene_approval_message(a)
        assert "report-only" in msg.lower()

    def test_size_human_formatted(self) -> None:
        a = _assessment((_dangling("sha256:a", size=1_200_000_000),))  # ~1.1GB
        msg = format_hygiene_approval_message(a)
        # Either GB or MB depending on rounding, but not raw bytes
        assert "1200000000" not in msg

    def test_cleanup_candidate_unused_image_marked_executable(self) -> None:
        """IMAGE_UNUSED with age > 30 (cleanup_candidate) shows ✓."""
        a = _assessment((_unused("sha256:old", age=60),))
        msg = format_hygiene_approval_message(a)
        assert "✓" in msg
        assert "(report-only)" not in msg

    def test_report_only_unused_image_not_marked_executable(self) -> None:
        """IMAGE_UNUSED with age ≤ 30 (report_only) shows (report-only), not ✓."""
        a = _assessment((_unused("sha256:young", age=5),))
        msg = format_hygiene_approval_message(a)
        assert "(report-only)" in msg
        assert "✓" not in msg

    def test_volume_cleanup_candidate_marked_web_report_only(self) -> None:
        """Volumes lost their (reply-channel-only) approval path in R2 — the
        message must say so instead of advertising a checkmark."""
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a)
        assert "report-only in web UI" in msg

    def test_build_cache_cleanup_candidate_shows_checkmark(self) -> None:
        a = _assessment((_build_cache_candidate(),))
        msg = format_hygiene_approval_message(a)
        assert " ✓" in msg
        assert "report-only in web UI" not in msg

    # --- Backup verify context ---

    def test_backup_context_shown_when_volumes_present_and_passed(self) -> None:
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a, backup_verify_passed=True)
        assert "Backup status: Verified" in msg

    def test_backup_context_shown_when_volumes_present_and_failed(self) -> None:
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a, backup_verify_passed=False)
        assert "Backup verify: not run or failed" in msg

    def test_backup_context_not_shown_when_no_volume_candidates(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(a, backup_verify_passed=True)
        assert "Backup" not in msg

    def test_backup_context_not_shown_when_backup_verify_passed_is_none(self) -> None:
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a)  # default: backup_verify_passed=None
        assert "Backup" not in msg


# ---------------------------------------------------------------------------
# Reply parser removed (R2)
# ---------------------------------------------------------------------------

class TestReplyChannelRemoved:
    def test_parser_and_poller_gone(self) -> None:
        import errander.safety.hygiene_approval as mod

        assert not hasattr(mod, "parse_hygiene_reply")
        assert not hasattr(mod, "poll_hygiene_replies_once")
        assert not hasattr(mod, "HygieneReplyError")

    def test_slack_reply_surface_retained_for_audit_readback(self) -> None:
        # Mirrors the LEGACY_ACTION_TYPES precedent: old audit rows must
        # still deserialize.
        assert ApprovalSurface.SLACK_REPLY.value == "slack_reply"


# ---------------------------------------------------------------------------
# HygieneApprovalManager (DB-backed facade — R3)
# ---------------------------------------------------------------------------

async def _make_manager():
    """Create a DB-backed HygieneApprovalManager for tests."""
    from errander.safety.hygiene_store import HygieneApprovalStore
    from tests.conftest import make_test_db
    store = HygieneApprovalStore(make_test_db())
    await store.initialize()
    return HygieneApprovalManager(store), store


class TestHygieneApprovalManager:
    @pytest.mark.asyncio
    async def test_register_and_get_pending(self) -> None:
        manager, _ = await _make_manager()
        a = _assessment((_dangling("sha256:a"),))
        await manager.register("b1", "v1", a)
        pending = await manager.get_pending()
        assert len(pending) == 1
        assert pending[0].batch_id == "b1"
        assert pending[0].vm_id == "v1"

    @pytest.mark.asyncio
    async def test_register_with_slack_ts_accepted(self) -> None:
        """slack_message_ts is accepted (not stored, but no error)."""
        manager, _ = await _make_manager()
        a = _assessment(())
        await manager.register("b1", "v1", a, slack_message_ts="1234.5678")
        pending = await manager.get_pending()
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_decide_moves_from_pending(self) -> None:
        manager, store = await _make_manager()
        a = _assessment((_dangling("sha256:a"),))
        await manager.register("b1", "v1", a)
        await store.decide(
            "b1", "v1",
            approved=True,
            decided_by="ui:tester",
            snapshot_hash=compute_assessment_hash(a),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )
        pending = await manager.get_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_decide_idempotent_first_wins(self) -> None:
        """Second decide on the same row is a no-op (WHERE status='pending' guard)."""
        manager, store = await _make_manager()
        a = _assessment((_dangling("sha256:a"),))
        await manager.register("b1", "v1", a)
        won1 = await store.decide(
            "b1", "v1", approved=True, decided_by="ui:first",
            snapshot_hash=compute_assessment_hash(a),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )
        won2 = await store.decide(
            "b1", "v1", approved=False, decided_by="ui:second",
            snapshot_hash=compute_assessment_hash(a),
        )
        assert won1 is True
        assert won2 is False
        row = await store.get("b1", "v1")
        assert row is not None
        assert row.decided_by == "ui:first"

    @pytest.mark.asyncio
    async def test_wait_for_decision_resolves_when_decided(self) -> None:
        manager, store = await _make_manager()
        a = _assessment((_dangling("sha256:a"),))
        await manager.register("b1", "v1", a)

        async def decide_soon() -> None:
            await asyncio.sleep(0.05)
            await store.decide(
                "b1", "v1",
                approved=True,
                decided_by="ui:tester",
                snapshot_hash=compute_assessment_hash(a),
                approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
            )

        asyncio.create_task(decide_soon())
        result = await manager.wait_for_decision("b1", "v1", timeout_seconds=5)
        assert result is not None
        assert len(result.approved_findings) == 1
        assert result.approved_findings[0].identity == "sha256:a"

    @pytest.mark.asyncio
    async def test_wait_for_decision_timeout_returns_none(self) -> None:
        manager, _ = await _make_manager()
        a = _assessment(())
        await manager.register("b1", "v1", a)
        result = await manager.wait_for_decision("b1", "v1", timeout_seconds=0)
        assert result is None
        pending = await manager.get_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_row_returns_none(self) -> None:
        """No row in DB → wait_for_decision returns None immediately."""
        manager, _ = await _make_manager()
        result = await manager.wait_for_decision("nonexistent", "v1", timeout_seconds=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_vms_in_same_batch(self) -> None:
        """A batch with N VMs gets N independent pending hygiene approvals."""
        manager, store = await _make_manager()
        a1 = _assessment((_dangling("sha256:a"),))
        a2 = _assessment((_dangling("sha256:b"),))
        await manager.register("b1", "vm1", a1)
        await manager.register("b1", "vm2", a2)
        pending = await manager.get_pending()
        assert len(pending) == 2

        await store.decide(
            "b1", "vm1",
            approved=True,
            decided_by="ui:tester",
            snapshot_hash=compute_assessment_hash(a1),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )
        pending = await manager.get_pending()
        assert len(pending) == 1
        assert pending[0].vm_id == "vm2"
