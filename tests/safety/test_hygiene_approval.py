"""Tests for the docker_hygiene approval surface (Session 2b-i).

Covers:
- Slack message formatter (format_hygiene_approval_message)
- Slack reply parser (parse_hygiene_reply)
- HygieneApprovalManager (register/resolve/wait)
"""

from __future__ import annotations

import asyncio

import pytest

from errander.models.docker_hygiene import (
    ApprovalSurface,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)
from errander.safety.hygiene_approval import (
    HygieneApprovalManager,
    HygieneReplyError,
    format_hygiene_approval_message,
    parse_hygiene_reply,
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


def _build_cache_report_only() -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.BUILD_CACHE,
        classification=FindingClassification.REPORT_ONLY,
        name="build_cache",
        reclaimable_bytes=5_000_000,
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


# ---------------------------------------------------------------------------
# Slack message formatter
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
        # And the operator-facing short keys for reply syntax
        assert "dangling.1" in msg
        assert "images.1" in msg
        assert "containers.1" in msg

    def test_includes_reply_syntax_block(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(a)
        assert "approve" in msg
        assert "reject all" in msg
        assert "approve all cleanup_candidate" in msg

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
        # No reply syntax encourages operator to act on volumes
        assert "approve volumes" not in msg

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


# ---------------------------------------------------------------------------
# Slack reply parser
# ---------------------------------------------------------------------------

class TestParseReply:
    def test_reject_all_returns_empty_approval(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        approval = parse_hygiene_reply("reject all", a, operator_id="U123")
        assert approval.approved_findings == ()
        assert approval.snapshot_hash == compute_assessment_hash(a)
        assert approval.operator_id == "U123"
        assert approval.surface == ApprovalSurface.SLACK_REPLY

    def test_reject_alone_is_also_rejection(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        approval = parse_hygiene_reply("reject", a, operator_id="U123")
        assert approval.approved_findings == ()

    def test_empty_text_rejected(self) -> None:
        a = _assessment(())
        with pytest.raises(HygieneReplyError, match="empty"):
            parse_hygiene_reply("   ", a, operator_id="U123")

    def test_non_approve_non_reject_rejected(self) -> None:
        a = _assessment(())
        with pytest.raises(HygieneReplyError):
            parse_hygiene_reply("delete everything", a, operator_id="U123")

    def test_approve_all_selects_all_executable_classes(self) -> None:
        a = _assessment((
            _dangling("sha256:a"),
            _container("c1", "worker"),
            _volume("vol1"),  # report-only — must NOT be included
        ))
        approval = parse_hygiene_reply("approve all", a, operator_id="U123")
        ids = {f.identity for f in approval.approved_findings}
        assert "sha256:a" in ids
        assert "c1" in ids
        assert "vol1" not in ids

    def test_approve_all_filtered_by_classification(self) -> None:
        a = _assessment((
            _dangling("sha256:cleanup"),  # cleanup_candidate
            _unused("sha256:recent", age=5),  # report_only (recent)
        ))
        approval = parse_hygiene_reply("approve all cleanup_candidate", a, operator_id="U123")
        ids = {f.identity for f in approval.approved_findings}
        assert ids == {"sha256:cleanup"}

    def test_approve_all_unknown_classification(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        with pytest.raises(HygieneReplyError, match="unknown classification"):
            parse_hygiene_reply("approve all mystery_class", a, operator_id="U123")

    def test_explicit_indices_single_class(self) -> None:
        a = _assessment((_dangling("sha256:a"), _dangling("sha256:b"), _dangling("sha256:c")))
        approval = parse_hygiene_reply("approve dangling 1,3", a, operator_id="U123")
        ids = [f.identity for f in approval.approved_findings]
        assert ids == ["sha256:a", "sha256:c"]

    def test_explicit_indices_multiple_classes(self) -> None:
        a = _assessment((
            _dangling("sha256:a"), _dangling("sha256:b"),
            _container("c1", "worker"), _container("c2", "api"),
        ))
        approval = parse_hygiene_reply(
            "approve dangling 1 containers 2",
            a, operator_id="U123",
        )
        ids = [f.identity for f in approval.approved_findings]
        assert ids == ["sha256:a", "c2"]

    def test_explicit_range_expression(self) -> None:
        a = _assessment(tuple(_dangling(f"sha256:{i}") for i in range(1, 6)))
        approval = parse_hygiene_reply("approve dangling 2-4", a, operator_id="U123")
        ids = [f.identity for f in approval.approved_findings]
        assert ids == ["sha256:2", "sha256:3", "sha256:4"]

    def test_index_out_of_range_rejected(self) -> None:
        a = _assessment((_dangling("sha256:a"),))  # only 1 item
        with pytest.raises(HygieneReplyError, match="out of range"):
            parse_hygiene_reply("approve dangling 5", a, operator_id="U123")

    def test_index_zero_rejected(self) -> None:
        """Indices are 1-based; 0 must be rejected, not silently re-mapped."""
        a = _assessment((_dangling("sha256:a"),))
        with pytest.raises(HygieneReplyError, match="out of range"):
            parse_hygiene_reply("approve dangling 0", a, operator_id="U123")

    def test_reversed_range_rejected(self) -> None:
        a = _assessment((_dangling("sha256:a"), _dangling("sha256:b"),))
        with pytest.raises(HygieneReplyError, match="reversed"):
            parse_hygiene_reply("approve dangling 2-1", a, operator_id="U123")

    def test_malformed_index_expr(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        with pytest.raises(HygieneReplyError, match="malformed"):
            parse_hygiene_reply("approve dangling abc", a, operator_id="U123")

    def test_unknown_class_key(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        with pytest.raises(HygieneReplyError, match="unknown class"):
            parse_hygiene_reply("approve gizmos 1", a, operator_id="U123")

    def test_report_only_volume_cannot_be_approved_by_index(self) -> None:
        """A volume classified report_only cannot be explicitly approved by index."""
        a = _assessment((_volume("vol1"),))
        with pytest.raises(HygieneReplyError, match="report_only"):
            parse_hygiene_reply("approve volumes 1", a, operator_id="U123")

    def test_report_only_finding_in_executable_class_rejected(self) -> None:
        """image_unused with age ≤ 30 is report_only — approval by index must fail."""
        a = _assessment((_unused("sha256:young", age=5),))
        with pytest.raises(HygieneReplyError, match="report_only"):
            parse_hygiene_reply("approve images 1", a, operator_id="U123")

    def test_approve_all_excludes_report_only_unused_images(self) -> None:
        """approve all must not select report_only image_unused findings."""
        old = _unused("sha256:old", age=60)   # cleanup_candidate
        young = _unused("sha256:young", age=5) # report_only
        a = _assessment((old, young))
        approval = parse_hygiene_reply("approve all", a, operator_id="U123")
        ids = {f.identity for f in approval.approved_findings}
        assert ids == {"sha256:old"}

    def test_approve_all_report_only_raises(self) -> None:
        """approve all report_only is never a valid command."""
        a = _assessment((_unused("sha256:young", age=5),))
        with pytest.raises(HygieneReplyError, match="report_only"):
            parse_hygiene_reply("approve all report_only", a, operator_id="U123")

    def test_empty_class_yields_clear_error(self) -> None:
        """Selecting from a class with no findings should fail loud, not silently approve nothing."""
        a = _assessment((_dangling("sha256:a"),))  # no containers
        with pytest.raises(HygieneReplyError, match="no findings"):
            parse_hygiene_reply("approve containers 1", a, operator_id="U123")

    def test_class_without_indices_rejected(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        with pytest.raises(HygieneReplyError, match="must be followed"):
            parse_hygiene_reply("approve dangling", a, operator_id="U123")

    def test_duplicate_indices_deduplicated(self) -> None:
        """Same index listed twice approves only once."""
        a = _assessment((_dangling("sha256:a"), _dangling("sha256:b")))
        approval = parse_hygiene_reply("approve dangling 1,1,2", a, operator_id="U123")
        ids = [f.identity for f in approval.approved_findings]
        assert ids == ["sha256:a", "sha256:b"]

    def test_case_insensitive(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        approval = parse_hygiene_reply("APPROVE Dangling 1", a, operator_id="U123")
        assert len(approval.approved_findings) == 1

    def test_surface_field_set(self) -> None:
        a = _assessment((_dangling("sha256:a"),))
        approval = parse_hygiene_reply(
            "approve dangling 1", a,
            operator_id="U123",
            surface=ApprovalSurface.WEB_PAGE,
        )
        assert approval.surface == ApprovalSurface.WEB_PAGE


# ---------------------------------------------------------------------------
# HygieneApprovalManager
# ---------------------------------------------------------------------------

class TestHygieneApprovalManager:
    def test_register_and_get_pending(self) -> None:
        m = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        pending = m.register("b1", "v1", a)
        assert pending.batch_id == "b1"
        assert pending.vm_id == "v1"
        assert pending.assessment is a
        assert len(m.get_pending()) == 1

    def test_register_with_slack_ts(self) -> None:
        m = HygieneApprovalManager()
        a = _assessment(())
        pending = m.register("b1", "v1", a, slack_message_ts="1234.5678")
        assert pending.slack_message_ts == "1234.5678"

    def test_resolve_moves_to_history(self) -> None:
        m = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        m.register("b1", "v1", a)
        approval = parse_hygiene_reply("approve dangling 1", a, operator_id="U123")
        m.resolve("b1", "v1", approval)
        assert len(m.get_pending()) == 0
        history = m.get_history()
        assert len(history) == 1
        assert history[0].approval is approval

    def test_resolve_idempotent(self) -> None:
        """Second resolve of the same key is a no-op (first wins)."""
        m = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        m.register("b1", "v1", a)
        approval1 = parse_hygiene_reply("approve dangling 1", a, operator_id="U123")
        approval2 = parse_hygiene_reply("reject all", a, operator_id="U456")
        m.resolve("b1", "v1", approval1)
        m.resolve("b1", "v1", approval2)  # should be no-op
        # History has exactly one entry, with the FIRST approval.
        history = m.get_history()
        assert len(history) == 1
        assert history[0].approval is approval1

    @pytest.mark.asyncio
    async def test_wait_for_decision_resolves_immediately(self) -> None:
        m = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        m.register("b1", "v1", a)
        approval = parse_hygiene_reply("approve dangling 1", a, operator_id="U123")

        async def resolve_soon() -> None:
            await asyncio.sleep(0.01)
            m.resolve("b1", "v1", approval)

        asyncio.create_task(resolve_soon())
        result = await m.wait_for_decision("b1", "v1", timeout_seconds=5)
        assert result is approval

    @pytest.mark.asyncio
    async def test_wait_for_decision_timeout_returns_none(self) -> None:
        m = HygieneApprovalManager()
        a = _assessment(())
        m.register("b1", "v1", a)
        # Timeout 0 means wait_for fails immediately; use 0.05 for a tiny real wait.
        result = await m.wait_for_decision("b1", "v1", timeout_seconds=0)
        # wait_for with timeout_seconds=0 raises TimeoutError caught by manager → None
        assert result is None
        # After timeout, pending is cleared so a late resolve doesn't fire stale.
        assert len(m.get_pending()) == 0

    @pytest.mark.asyncio
    async def test_wait_for_unknown_key_raises(self) -> None:
        m = HygieneApprovalManager()
        with pytest.raises(KeyError):
            await m.wait_for_decision("nonexistent", "v1", timeout_seconds=1)

    def test_multiple_vms_in_same_batch(self) -> None:
        """A batch with N VMs gets N independent pending hygiene approvals."""
        m = HygieneApprovalManager()
        a1 = _assessment((_dangling("sha256:a"),))
        a2 = _assessment((_dangling("sha256:b"),))
        m.register("b1", "vm1", a1)
        m.register("b1", "vm2", a2)
        assert len(m.get_pending()) == 2

        approval1 = parse_hygiene_reply("approve dangling 1", a1, operator_id="U")
        m.resolve("b1", "vm1", approval1)
        # vm2 still pending
        pending = m.get_pending()
        assert len(pending) == 1
        assert pending[0].vm_id == "vm2"


# ---------------------------------------------------------------------------
# v1.5 — Volume and build cache approval surface
# ---------------------------------------------------------------------------

class TestVolumeAndBuildCacheApproval:
    """Covers formatter markers, approve-all scope, explicit index, backup context."""

    # --- Formatter markers ---

    def test_volume_report_only_shows_as_report_only(self) -> None:
        a = _assessment((_volume("vol1"),))
        msg = format_hygiene_approval_message(a)
        assert "(report-only)" in msg
        assert "✓" not in msg

    def test_volume_cleanup_candidate_shows_explicit_only_marker(self) -> None:
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a)
        assert "✓ ⚠ explicit-only" in msg

    def test_build_cache_cleanup_candidate_shows_checkmark_not_explicit(self) -> None:
        a = _assessment((_build_cache_candidate(),))
        msg = format_hygiene_approval_message(a)
        assert " ✓" in msg
        assert "explicit-only" not in msg

    # --- approve all scope ---

    def test_approve_all_excludes_volume_cleanup_candidate(self) -> None:
        """'approve all' must NOT select volumes even when they are cleanup_candidate."""
        a = _assessment((_dangling("sha256:a"), _volume_candidate("vol1")))
        approval = parse_hygiene_reply("approve all", a, operator_id="U123")
        ids = {f.object_id for f in approval.approved_findings}
        assert "sha256:a" in ids
        assert not any(f.name == "vol1" for f in approval.approved_findings)

    def test_approve_all_includes_build_cache_cleanup_candidate(self) -> None:
        """'approve all cleanup_candidate' selects build_cache (not explicit-only)."""
        a = _assessment((_dangling("sha256:a"), _build_cache_candidate()))
        approval = parse_hygiene_reply("approve all cleanup_candidate", a, operator_id="U123")
        classes = {f.resource_class for f in approval.approved_findings}
        assert DockerResourceClass.IMAGE_DANGLING in classes
        assert DockerResourceClass.BUILD_CACHE in classes

    # --- Explicit index approval ---

    def test_explicit_approve_volume_cleanup_candidate_succeeds(self) -> None:
        a = _assessment((_volume_candidate("pgdata_old"),))
        approval = parse_hygiene_reply("approve volumes 1", a, operator_id="U123")
        assert len(approval.approved_findings) == 1
        assert approval.approved_findings[0].name == "pgdata_old"

    def test_explicit_approve_volume_report_only_rejected(self) -> None:
        a = _assessment((_volume("young_vol"),))
        with pytest.raises(HygieneReplyError, match="report_only"):
            parse_hygiene_reply("approve volumes 1", a, operator_id="U123")

    def test_explicit_approve_build_cache_cleanup_candidate_succeeds(self) -> None:
        a = _assessment((_build_cache_candidate(),))
        approval = parse_hygiene_reply("approve build_cache 1", a, operator_id="U123")
        assert len(approval.approved_findings) == 1
        assert approval.approved_findings[0].resource_class == DockerResourceClass.BUILD_CACHE

    def test_explicit_approve_build_cache_report_only_rejected(self) -> None:
        a = _assessment((_build_cache_report_only(),))
        with pytest.raises(HygieneReplyError, match="report_only"):
            parse_hygiene_reply("approve build_cache 1", a, operator_id="U123")

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
        """Backup context only appears when there are volume cleanup_candidates."""
        a = _assessment((_dangling("sha256:a"),))
        msg = format_hygiene_approval_message(a, backup_verify_passed=True)
        assert "Backup" not in msg

    def test_backup_context_not_shown_when_backup_verify_passed_is_none(self) -> None:
        """When backup_verify_passed is None (no backup ran), omit context entirely."""
        a = _assessment((_volume_candidate("vol1"),))
        msg = format_hygiene_approval_message(a)  # default: backup_verify_passed=None
        assert "Backup" not in msg
