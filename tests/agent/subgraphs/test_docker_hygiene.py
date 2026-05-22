"""Tests for the docker_hygiene sub-graph (v1.1 Session 1).

Session 1 scope: validate node, assess node, parser, classification rules,
graph builder. Execution (remove + per-object audit) lands in Session 2 and
is not exercised here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from errander.agent.subgraphs.docker_hygiene import (
    INVESTIGATE_CONTAINER_EXIT_CODES,
    STOPPED_CONTAINER_CLEANUP_AGE_HOURS,
    UNUSED_IMAGE_CLEANUP_AGE_DAYS,
    VOLUME_LAST_MOUNT_AGE_DAYS,
    DockerHygieneGraphState,
    _classify_build_cache,
    _classify_image,
    _classify_stopped_container,
    _classify_volume,
    assess_node,
    build_docker_hygiene_subgraph,
    parse_assess_v2_output,
    route_after_assess,
    route_after_validate,
    validate_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus
from errander.models.docker_hygiene import (
    DockerHygieneAssessment,
    DockerResourceClass,
    FindingClassification,
)

# --- Helpers ---

def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _base_state(**overrides: object) -> DockerHygieneGraphState:
    defaults: DockerHygieneGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "docker_available": True,
        "docker_command_mode": "wrapper",
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


# --- Validate node ---

class TestValidateNode:
    def test_wrapper_mode_passes(self) -> None:
        state = _base_state(docker_command_mode="wrapper", docker_available=True)
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value

    def test_disabled_mode_skips(self) -> None:
        state = _base_state(docker_command_mode="disabled")
        result = validate_node(state)
        assert result["status"] == ActionStatus.SKIPPED.value
        assert "disabled" in result["error"]

    def test_direct_sudo_rejected_with_clear_reason(self) -> None:
        """docker_hygiene refuses direct_sudo — wrapper required for per-object validation."""
        state = _base_state(docker_command_mode="direct_sudo")
        result = validate_node(state)
        assert result["status"] == ActionStatus.SKIPPED.value
        assert "wrapper" in result["error"]
        assert "Per-object validation" in result["error"]

    def test_docker_not_available_skips(self) -> None:
        state = _base_state(docker_available=False)
        result = validate_node(state)
        assert result["status"] == ActionStatus.SKIPPED.value
        assert "not installed" in result["error"] or "not running" in result["error"]


# --- Parser ---

class TestParseAssessV2Output:
    def test_reachable_false_short_circuits(self) -> None:
        output = "reachable=no\nerror=docker daemon not reachable\n"
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.reachable is False
        assert a.error == "docker daemon not reachable"
        assert a.findings == ()

    def test_empty_block_yields_no_findings(self) -> None:
        output = "reachable=yes\nerror=\ndocker_hygiene_begin\ndocker_hygiene_end\n"
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.reachable is True
        assert a.findings == ()
        assert a.nothing_to_surface() is True

    def test_dangling_image_parsed(self) -> None:
        output = (
            "reachable=yes\nerror=\n"
            "docker_hygiene_begin\n"
            "class=image_dangling\n"
            "  id=sha256:abc123 size_bytes=1288490188 age_days=23 last_tag=<none>\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert len(a.findings) == 1
        f = a.findings[0]
        assert f.resource_class == DockerResourceClass.IMAGE_DANGLING
        assert f.object_id == "sha256:abc123"
        assert f.size_bytes == 1288490188
        assert f.age_days == 23
        assert f.last_tag is None  # <none> normalized to None
        assert f.classification == FindingClassification.CLEANUP_CANDIDATE

    def test_unused_image_old_is_cleanup_candidate(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_unused\n"
            "  id=sha256:def size_bytes=500 age_days=90 last_tag=api:v1\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings[0].classification == FindingClassification.CLEANUP_CANDIDATE
        assert a.findings[0].last_tag == "api:v1"

    def test_unused_image_recent_is_report_only(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_unused\n"
            "  id=sha256:def size_bytes=500 age_days=3 last_tag=api:rc\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings[0].classification == FindingClassification.REPORT_ONLY

    def test_stopped_container_old_clean_exit_is_cleanup(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=container_stopped\n"
            "  id=cont1 name=worker exit_code=0 stopped_age_hours=300\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        f = a.findings[0]
        assert f.classification == FindingClassification.CLEANUP_CANDIDATE
        assert f.name == "worker"
        assert f.exit_code == 0
        assert f.stopped_age_hours == 300

    def test_stopped_container_oom_is_investigate(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=container_stopped\n"
            "  id=cont2 name=api exit_code=137 stopped_age_hours=2\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings[0].classification == FindingClassification.INVESTIGATE

    def test_stopped_container_recent_clean_exit_is_report_only(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=container_stopped\n"
            "  id=cont3 name=oneshot exit_code=0 stopped_age_hours=5\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings[0].classification == FindingClassification.REPORT_ONLY

    def test_volume_unreferenced_is_report_only(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=volume_unreferenced\n"
            "  name=pgdata_old size_bytes=12884901888 last_mount_days=47\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        f = a.findings[0]
        assert f.resource_class == DockerResourceClass.VOLUME_UNREFERENCED
        assert f.name == "pgdata_old"
        assert f.size_bytes == 12884901888
        assert f.last_mount_days == 47
        assert f.classification == FindingClassification.REPORT_ONLY

    def test_build_cache_is_report_only(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=build_cache\n"
            "  reclaimable_bytes=8589934592\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        f = a.findings[0]
        assert f.resource_class == DockerResourceClass.BUILD_CACHE
        assert f.reclaimable_bytes == 8589934592
        assert f.classification == FindingClassification.REPORT_ONLY

    def test_build_cache_zero_reclaimable_omitted(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=build_cache\n"
            "  reclaimable_bytes=0\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings == ()

    def test_multiple_classes_in_one_assessment(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_dangling\n"
            "  id=sha256:a size_bytes=100 age_days=10 last_tag=<none>\n"
            "  id=sha256:b size_bytes=200 age_days=5 last_tag=<none>\n"
            "class=container_stopped\n"
            "  id=c1 name=foo exit_code=137 stopped_age_hours=1\n"
            "class=volume_unreferenced\n"
            "  name=v1 size_bytes=1024 last_mount_days=10\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert len(a.findings) == 4
        by_class = a.by_class()
        assert len(by_class[DockerResourceClass.IMAGE_DANGLING]) == 2
        assert len(by_class[DockerResourceClass.CONTAINER_STOPPED]) == 1
        assert len(by_class[DockerResourceClass.VOLUME_UNREFERENCED]) == 1
        assert len(a.cleanup_candidates()) == 2  # both dangling images
        assert len(a.investigate()) == 1  # OOM container

    def test_unknown_class_warned_and_skipped(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=mystery_class\n"
            "  id=x size_bytes=1 age_days=1\n"
            "class=image_dangling\n"
            "  id=sha256:keep size_bytes=1 age_days=1 last_tag=<none>\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        # Unknown class entries skipped; subsequent known class still parsed.
        assert len(a.findings) == 1
        assert a.findings[0].object_id == "sha256:keep"

    def test_missing_required_id_skips_finding(self) -> None:
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_dangling\n"
            "  size_bytes=100 age_days=10\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings == ()

    def test_repo_tag_with_equals_preserved(self) -> None:
        """last_tag values containing '=' must not be truncated."""
        output = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_unused\n"
            "  id=sha256:abc size_bytes=1 age_days=100 last_tag=repo/x:v1=preview\n"
            "docker_hygiene_end\n"
        )
        a = parse_assess_v2_output(output, vm_id="vm1")
        assert a.findings[0].last_tag == "repo/x:v1=preview"


# --- Classification helpers ---

class TestClassifyImage:
    def test_dangling_always_cleanup(self) -> None:
        assert (
            _classify_image(DockerResourceClass.IMAGE_DANGLING, age_days=0)
            == FindingClassification.CLEANUP_CANDIDATE
        )
        assert (
            _classify_image(DockerResourceClass.IMAGE_DANGLING, age_days=1000)
            == FindingClassification.CLEANUP_CANDIDATE
        )

    def test_unused_old_is_cleanup(self) -> None:
        assert (
            _classify_image(
                DockerResourceClass.IMAGE_UNUSED,
                age_days=UNUSED_IMAGE_CLEANUP_AGE_DAYS + 1,
            )
            == FindingClassification.CLEANUP_CANDIDATE
        )

    def test_unused_threshold_boundary_is_report_only(self) -> None:
        # Exactly at threshold = not cleanup (rule is > 30, not >= 30)
        assert (
            _classify_image(
                DockerResourceClass.IMAGE_UNUSED,
                age_days=UNUSED_IMAGE_CLEANUP_AGE_DAYS,
            )
            == FindingClassification.REPORT_ONLY
        )

    def test_unused_recent_is_report_only(self) -> None:
        assert (
            _classify_image(DockerResourceClass.IMAGE_UNUSED, age_days=5)
            == FindingClassification.REPORT_ONLY
        )

    def test_unused_age_none_is_report_only(self) -> None:
        assert (
            _classify_image(DockerResourceClass.IMAGE_UNUSED, age_days=None)
            == FindingClassification.REPORT_ONLY
        )


class TestClassifyStoppedContainer:
    def test_oom_kill_is_investigate(self) -> None:
        assert (
            _classify_stopped_container(exit_code=137, stopped_age_hours=1)
            == FindingClassification.INVESTIGATE
        )

    def test_sigsegv_is_investigate(self) -> None:
        assert (
            _classify_stopped_container(exit_code=139, stopped_age_hours=1)
            == FindingClassification.INVESTIGATE
        )

    def test_sigterm_is_not_investigate(self) -> None:
        """143 (SIGTERM) is NOT in the investigate set — ordinary graceful stop."""
        assert 143 not in INVESTIGATE_CONTAINER_EXIT_CODES
        assert (
            _classify_stopped_container(exit_code=143, stopped_age_hours=1)
            == FindingClassification.REPORT_ONLY
        )

    def test_clean_exit_old_is_cleanup(self) -> None:
        assert (
            _classify_stopped_container(
                exit_code=0,
                stopped_age_hours=STOPPED_CONTAINER_CLEANUP_AGE_HOURS + 1,
            )
            == FindingClassification.CLEANUP_CANDIDATE
        )

    def test_clean_exit_at_threshold_is_report_only(self) -> None:
        # Boundary: rule is > 168, not >= 168
        assert (
            _classify_stopped_container(
                exit_code=0,
                stopped_age_hours=STOPPED_CONTAINER_CLEANUP_AGE_HOURS,
            )
            == FindingClassification.REPORT_ONLY
        )

    def test_clean_exit_recent_is_report_only(self) -> None:
        assert (
            _classify_stopped_container(exit_code=0, stopped_age_hours=5)
            == FindingClassification.REPORT_ONLY
        )

    def test_unknown_nonzero_exit_is_report_only(self) -> None:
        """Non-OOM/SEGV non-zero exit isn't auto-investigate. Operator decides."""
        assert (
            _classify_stopped_container(exit_code=1, stopped_age_hours=100)
            == FindingClassification.REPORT_ONLY
        )


# --- Assess node ---

class TestAssessNode:
    @pytest.mark.asyncio
    async def test_happy_path_with_findings(self) -> None:
        executor = _make_executor()
        stdout = (
            "reachable=yes\nerror=\ndocker_hygiene_begin\n"
            "class=image_dangling\n"
            "  id=sha256:a size_bytes=100 age_days=10 last_tag=<none>\n"
            "  id=sha256:b size_bytes=200 age_days=5 last_tag=<none>\n"
            "class=container_stopped\n"
            "  id=c1 name=worker exit_code=0 stopped_age_hours=200\n"
            "docker_hygiene_end\n"
        )

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await assess_node(_base_state(), executor=executor)

        assert result["nothing_to_do"] is False
        assessment = result["assessment"]
        assert isinstance(assessment, DockerHygieneAssessment)
        assert len(assessment.findings) == 3
        assert len(assessment.cleanup_candidates()) == 3  # 2 dangling + 1 old container

    @pytest.mark.asyncio
    async def test_nothing_to_do_when_empty_block(self) -> None:
        executor = _make_executor()
        stdout = "reachable=yes\nerror=\ndocker_hygiene_begin\ndocker_hygiene_end\n"

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await assess_node(_base_state(), executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value

    @pytest.mark.asyncio
    async def test_unreachable_docker_skips(self) -> None:
        executor = _make_executor()
        stdout = "reachable=no\nerror=docker daemon not reachable\n"

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await assess_node(_base_state(), executor=executor)

        assert result["nothing_to_do"] is True
        assert result["status"] == ActionStatus.SKIPPED.value
        assert "not reachable" in result["error"]

    @pytest.mark.asyncio
    async def test_wrapper_failure_is_failed(self) -> None:
        executor = _make_executor()

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(stdout="", exit_code=1)

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await assess_node(_base_state(), executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert result["nothing_to_do"] is True


# --- Routing ---

class TestRouting:
    def test_route_after_validate_continues_on_pending(self) -> None:
        assert route_after_validate(_base_state()) == "assess"

    def test_route_after_validate_aborts_on_skipped(self) -> None:
        state = _base_state(status=ActionStatus.SKIPPED.value)
        from langgraph.graph import END
        assert route_after_validate(state) == END

    def test_route_after_assess_ends_when_nothing_to_do(self) -> None:
        from langgraph.graph import END
        assert route_after_assess(_base_state(nothing_to_do=True)) == END

    def test_route_after_assess_ends_when_no_approval(self) -> None:
        from langgraph.graph import END
        # Assessment ran, has findings, but no approval injected → report-only path
        assert route_after_assess(_base_state(nothing_to_do=False)) == END

    def test_route_after_assess_executes_when_approval_present(self) -> None:
        from errander.models.docker_hygiene import (
            ApprovalSurface,
            DockerHygieneApproval,
        )
        approval = DockerHygieneApproval(
            vm_id="dev/web-01",
            approved_findings=(),
            snapshot_hash="abc123",
            surface=ApprovalSurface.TEST_INJECT,
            operator_id="test",
        )
        state = _base_state(nothing_to_do=False, approval=approval)  # type: ignore[call-arg]
        assert route_after_assess(state) == "execute"


# --- Graph builder smoke ---

class TestGraphBuilder:
    def test_compiles(self) -> None:
        executor = _make_executor()
        compiled = build_docker_hygiene_subgraph(executor).compile()
        assert compiled is not None

    @pytest.mark.asyncio
    async def test_end_to_end_disabled_skips_without_calling_wrapper(self) -> None:
        executor = _make_executor()

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            pytest.fail("wrapper should not be invoked when mode=disabled")

        compiled = build_docker_hygiene_subgraph(executor).compile()
        with patch.object(executor, "execute", side_effect=mock_execute):
            final = await compiled.ainvoke(_base_state(docker_command_mode="disabled"))
        assert final["status"] == ActionStatus.SKIPPED.value


# --- Session 2a: parse_remove_v2_output ---

class TestParseRemoveV2Output:
    def _finding(self, klass: DockerResourceClass, obj_id: str) -> object:
        from errander.models.docker_hygiene import DockerHygieneFinding
        return DockerHygieneFinding(
            resource_class=klass,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id=obj_id,
        )

    def test_all_removed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (
            self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:a"),
            self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:b"),
        )
        stdout = (
            "result class=image_dangling id=sha256:a status=removed reason=\n"
            "result class=image_dangling id=sha256:b status=removed reason=\n"
        )
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 2
        assert all(r.status == RemovalStatus.REMOVED for r in results)

    def test_drift_skipped_carries_reason(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:a"),)
        stdout = "result class=image_dangling id=sha256:a status=drift_skipped reason=image_re_tagged\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 1
        assert results[0].status == RemovalStatus.DRIFT_SKIPPED
        assert results[0].drift_reason == "image_re_tagged"
        assert results[0].error is None

    def test_failed_carries_error(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._finding(DockerResourceClass.CONTAINER_STOPPED, "cont1"),)
        stdout = "result class=container_stopped id=cont1 status=failed reason=permission_denied\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert results[0].status == RemovalStatus.FAILED
        assert results[0].error == "permission_denied"
        assert results[0].drift_reason is None

    def test_missing_result_becomes_failed(self) -> None:
        """If wrapper returns no result for an approved object, it MUST be flagged failed."""
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (
            self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:a"),
            self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:gone"),
        )
        stdout = "result class=image_dangling id=sha256:a status=removed reason=\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 2
        statuses = {r.finding.object_id: r.status for r in results}
        assert statuses["sha256:a"] == RemovalStatus.REMOVED
        assert statuses["sha256:gone"] == RemovalStatus.FAILED

    def test_unknown_status_becomes_failed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:a"),)
        stdout = "result class=image_dangling id=sha256:a status=teleported reason=alien\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert results[0].status == RemovalStatus.FAILED
        assert "teleported" in (results[0].error or "")

    def test_extra_result_for_unapproved_object_is_dropped(self) -> None:
        """Wrapper hallucinating a result for an unapproved object — must NOT be acted on."""
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        approved = (self._finding(DockerResourceClass.IMAGE_DANGLING, "sha256:a"),)
        stdout = (
            "result class=image_dangling id=sha256:a status=removed reason=\n"
            "result class=image_dangling id=sha256:hallucinated status=removed reason=\n"
        )
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        ids = {r.finding.object_id for r in results}
        assert ids == {"sha256:a"}


# --- Session 2a: execute_node ---

class TestExecuteNode:
    def _approval(
        self,
        findings: tuple = (),
        snapshot_hash: str = "abc123",
    ) -> object:
        from errander.models.docker_hygiene import (
            ApprovalSurface,
            DockerHygieneApproval,
        )
        return DockerHygieneApproval(
            vm_id="dev/web-01",
            approved_findings=findings,
            snapshot_hash=snapshot_hash,
            surface=ApprovalSurface.TEST_INJECT,
            operator_id="test-user",
        )

    def _finding(self, obj_id: str) -> object:
        from errander.models.docker_hygiene import DockerHygieneFinding
        return DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id=obj_id,
            size_bytes=100,
            age_days=10,
        )

    @pytest.mark.asyncio
    async def test_no_approval_skips(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        executor = _make_executor()

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            pytest.fail("execute_node must not call wrapper without an approval")

        with patch.object(executor, "execute", side_effect=mock_execute):
            result = await execute_node(_base_state(), executor=executor)

        assert result["status"] == ActionStatus.SKIPPED.value
        assert result["removal_results"] == ()
        assert "without an approval" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_approval_skips(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        executor = _make_executor()
        approval = self._approval(findings=())

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            pytest.fail("wrapper must not be called when approval is empty (rejection)")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SKIPPED.value
        assert result["removal_results"] == ()

    @pytest.mark.asyncio
    async def test_dry_run_synthesises_results_without_wrapper(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import RemovalStatus
        executor = _make_executor()
        approval = self._approval(findings=(self._finding("sha256:a"),))

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            pytest.fail("wrapper must NOT be invoked in dry-run mode")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=True)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.DRY_RUN_OK.value
        assert len(result["removal_results"]) == 1
        assert result["removal_results"][0].status == RemovalStatus.REMOVED

    @pytest.mark.asyncio
    async def test_live_run_invokes_wrapper_and_parses_results(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import RemovalStatus
        executor = _make_executor()
        approval = self._approval(findings=(
            self._finding("sha256:a"),
            self._finding("sha256:b"),
        ))
        wrapper_stdout = (
            "result class=image_dangling id=sha256:a status=removed reason=\n"
            "result class=image_dangling id=sha256:b status=drift_skipped reason=image_re_tagged\n"
        )

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(wrapper_stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=False)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        # Aggregate status: at least one REMOVED → SUCCESS
        assert result["status"] == ActionStatus.SUCCESS.value
        statuses = {r.finding.object_id: r.status for r in result["removal_results"]}
        assert statuses["sha256:a"] == RemovalStatus.REMOVED
        assert statuses["sha256:b"] == RemovalStatus.DRIFT_SKIPPED

    @pytest.mark.asyncio
    async def test_all_drift_aggregates_to_skipped(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        executor = _make_executor()
        approval = self._approval(findings=(self._finding("sha256:a"),))
        wrapper_stdout = "result class=image_dangling id=sha256:a status=drift_skipped reason=image_re_tagged\n"

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(wrapper_stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=False)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SKIPPED.value

    @pytest.mark.asyncio
    async def test_any_failed_aggregates_to_failed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        executor = _make_executor()
        approval = self._approval(findings=(
            self._finding("sha256:a"),
            self._finding("sha256:b"),
        ))
        wrapper_stdout = (
            "result class=image_dangling id=sha256:a status=removed reason=\n"
            "result class=image_dangling id=sha256:b status=failed reason=permission_denied\n"
        )

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(wrapper_stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=False)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_snapshot_hash_mismatch_refuses_execution(self) -> None:
        """Approval against a stale assessment must be refused, even in live mode."""
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import (
            DockerHygieneAssessment,
            compute_assessment_hash,
        )
        executor = _make_executor()

        # Build an assessment, compute its real hash, then mutate the approval
        # to carry a stale hash.
        finding = self._finding("sha256:a")
        assessment = DockerHygieneAssessment(
            vm_id="dev/web-01",
            findings=(finding,),  # type: ignore[arg-type]
        )
        real_hash = compute_assessment_hash(assessment)
        approval = self._approval(
            findings=(finding,),  # type: ignore[arg-type]
            snapshot_hash="deadbeefdeadbeef",  # NOT the real hash
        )

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            pytest.fail("wrapper must NOT be called when snapshot hash drifted")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(
                approval=approval,  # type: ignore[call-arg]
                assessment=assessment,  # type: ignore[call-arg]
                dry_run=False,
            )
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SKIPPED.value
        assert "drifted" in result["error"]
        # Sanity: the real_hash is different from the stale one
        assert real_hash != "deadbeefdeadbeef"

    @pytest.mark.asyncio
    async def test_wrapper_failure_aggregates_to_failed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        executor = _make_executor()
        approval = self._approval(findings=(self._finding("sha256:a"),))

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return SSHResult(exit_code=1, stdout="", stderr="sudo denied", command="mocked")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=False)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.FAILED.value
        assert "sudo denied" in result["error"]

    @pytest.mark.asyncio
    async def test_unused_image_cleanup_candidate_execute_path(self) -> None:
        """IMAGE_UNUSED cleanup candidate (age > 30) goes through the execute path."""
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import (
            ApprovalSurface,
            DockerHygieneApproval,
            DockerHygieneFinding,
            RemovalStatus,
        )
        executor = _make_executor()
        unused_finding = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_UNUSED,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:unused-old",
            age_days=60,
        )
        approval = DockerHygieneApproval(
            vm_id="dev/web-01",
            approved_findings=(unused_finding,),
            snapshot_hash="abc123",
            surface=ApprovalSurface.TEST_INJECT,
            operator_id="test-user",
        )
        wrapper_stdout = "result class=image_unused id=sha256:unused-old status=removed reason=\n"

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result(wrapper_stdout)

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(approval=approval, dry_run=False)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value
        assert len(result["removal_results"]) == 1
        assert result["removal_results"][0].status == RemovalStatus.REMOVED
        assert result["removal_results"][0].finding.object_id == "sha256:unused-old"


# --- Session 2a: compute_assessment_hash ---

class TestComputeAssessmentHash:
    def test_deterministic_across_runs(self) -> None:
        from errander.models.docker_hygiene import (
            DockerHygieneAssessment,
            DockerHygieneFinding,
            compute_assessment_hash,
        )
        f = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:abc",
            size_bytes=100,
            age_days=5,
        )
        a1 = DockerHygieneAssessment(vm_id="v1", findings=(f,))
        a2 = DockerHygieneAssessment(vm_id="v1", findings=(f,))
        assert compute_assessment_hash(a1) == compute_assessment_hash(a2)

    def test_changes_when_finding_added(self) -> None:
        from errander.models.docker_hygiene import (
            DockerHygieneAssessment,
            DockerHygieneFinding,
            compute_assessment_hash,
        )
        f1 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:a",
        )
        f2 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:b",
        )
        h1 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f1,)))
        h2 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f1, f2)))
        assert h1 != h2

    def test_unaffected_by_volatile_size(self) -> None:
        """size_bytes can fluctuate between probes; it must NOT change the hash."""
        from errander.models.docker_hygiene import (
            DockerHygieneAssessment,
            DockerHygieneFinding,
            compute_assessment_hash,
        )
        f1 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:a",
            size_bytes=100,
        )
        f2 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:a",
            size_bytes=200,
        )
        h1 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f1,)))
        h2 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f2,)))
        assert h1 == h2

    def test_order_independence(self) -> None:
        from errander.models.docker_hygiene import (
            DockerHygieneAssessment,
            DockerHygieneFinding,
            compute_assessment_hash,
        )
        f1 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:a",
        )
        f2 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:b",
        )
        h12 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f1, f2)))
        h21 = compute_assessment_hash(DockerHygieneAssessment(vm_id="v1", findings=(f2, f1)))
        assert h12 == h21


# --- Session 2a: vm_graph dispatch wiring ---

class TestVmGraphDispatch:
    """Sanity that docker_hygiene is reachable through vm_graph.dispatch_action_node."""

    @pytest.mark.asyncio
    async def test_dispatch_routes_docker_hygiene_to_runner(self) -> None:
        """Verify ActionType.DOCKER_HYGIENE goes to _run_docker_hygiene, not the unknown branch."""
        from errander.agent.vm_graph import dispatch_action_node
        from errander.models.actions import ActionType

        called = {"hygiene": False}

        async def fake_runner(state: object, compiled: object, **_: object) -> dict[str, object]:
            called["hygiene"] = True
            return {
                "action_type": ActionType.DOCKER_HYGIENE.value,
                "status": ActionStatus.SKIPPED.value,
                "vm_id": "v1",
                "started_at": "2026-05-22T00:00:00+00:00",
                "completed_at": "2026-05-22T00:00:00+00:00",
                "detail": "fake",
            }

        with patch(
            "errander.agent.vm_graph._run_docker_hygiene",
            side_effect=fake_runner,
        ):
            vm_state: dict[str, object] = {
                "vm_id": "v1",
                "os_family": "ubuntu",
                "planned_actions": [{
                    "action_type": ActionType.DOCKER_HYGIENE.value,
                    "risk_tier": "medium",
                    "params": {},
                }],
                "current_action_index": 0,
                "results": [],
            }
            result = await dispatch_action_node(
                vm_state,  # type: ignore[arg-type]
                executor=_make_executor(),
                docker_hygiene_compiled=object(),  # sentinel — runner is mocked
            )

        assert called["hygiene"] is True
        assert result["current_action_index"] == 1
        assert len(result["results"]) == 1


# --- Session 2a: per-object audit hook ---

class TestPerObjectAuditHook:
    @pytest.mark.asyncio
    async def test_writes_one_audit_event_per_removal_result(self) -> None:
        from errander.agent.vm_graph import _write_docker_hygiene_per_object_audit
        from errander.models.actions import ActionType
        from errander.models.docker_hygiene import (
            DockerHygieneFinding,
            DockerHygieneRemovalResult,
            RemovalStatus,
        )
        from errander.models.events import EventType

        events: list[object] = []

        class _FakeAuditStore:
            async def log_event(self, evt: object) -> None:
                events.append(evt)

        f1 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:a",
            size_bytes=100,
        )
        f2 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:b",
        )
        f3 = DockerHygieneFinding(
            resource_class=DockerResourceClass.IMAGE_DANGLING,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            object_id="sha256:c",
        )
        result_dict = {
            "action_type": ActionType.DOCKER_HYGIENE.value,
            "removal_results": (
                DockerHygieneRemovalResult(finding=f1, status=RemovalStatus.REMOVED),
                DockerHygieneRemovalResult(
                    finding=f2,
                    status=RemovalStatus.DRIFT_SKIPPED,
                    drift_reason="image_re_tagged",
                ),
                DockerHygieneRemovalResult(
                    finding=f3,
                    status=RemovalStatus.FAILED,
                    error="permission_denied",
                ),
            ),
        }

        await _write_docker_hygiene_per_object_audit(
            _FakeAuditStore(),  # type: ignore[arg-type]
            batch_id="b1",
            vm_id="v1",
            result_dict=result_dict,
        )

        assert len(events) == 3
        types = [e.event_type for e in events]  # type: ignore[attr-defined]
        assert EventType.DOCKER_HYGIENE_OBJECT_REMOVED in types
        assert EventType.DOCKER_HYGIENE_OBJECT_DRIFT_SKIPPED in types
        assert EventType.DOCKER_HYGIENE_OBJECT_REMOVE_FAILED in types
        # Each event carries the exact object_id in metadata
        ids = {e.metadata.get("object_id") for e in events}  # type: ignore[attr-defined]
        assert ids == {"sha256:a", "sha256:b", "sha256:c"}


# ---------------------------------------------------------------------------
# v1.5 — Volume and build cache classifiers
# ---------------------------------------------------------------------------

class TestClassifyVolume:
    def test_below_threshold_is_report_only(self) -> None:
        assert _classify_volume(80, enabled=True) == FindingClassification.REPORT_ONLY

    def test_at_threshold_boundary_is_report_only(self) -> None:
        # Rule is strictly greater-than — at the threshold is still report_only.
        assert _classify_volume(VOLUME_LAST_MOUNT_AGE_DAYS, enabled=True) == FindingClassification.REPORT_ONLY

    def test_above_threshold_is_cleanup_candidate(self) -> None:
        assert _classify_volume(VOLUME_LAST_MOUNT_AGE_DAYS + 1, enabled=True) == FindingClassification.CLEANUP_CANDIDATE

    def test_none_age_is_report_only(self) -> None:
        assert _classify_volume(None, enabled=True) == FindingClassification.REPORT_ONLY

    def test_disabled_always_report_only(self) -> None:
        assert _classify_volume(365, enabled=False) == FindingClassification.REPORT_ONLY


class TestClassifyBuildCache:
    def test_reclaimable_is_cleanup_candidate(self) -> None:
        assert _classify_build_cache(1_000_000, enabled=True) == FindingClassification.CLEANUP_CANDIDATE

    def test_zero_bytes_is_report_only(self) -> None:
        assert _classify_build_cache(0, enabled=True) == FindingClassification.REPORT_ONLY

    def test_none_bytes_is_report_only(self) -> None:
        assert _classify_build_cache(None, enabled=True) == FindingClassification.REPORT_ONLY

    def test_disabled_always_report_only(self) -> None:
        assert _classify_build_cache(10_000_000, enabled=False) == FindingClassification.REPORT_ONLY


class TestParseAssessV2OutputV15:
    """Parser correctly classifies volumes and build cache when deletion is enabled."""

    def _stdout(self) -> str:
        return (
            "reachable=yes\n"
            "docker_hygiene_begin\n"
            "class=volume_unreferenced\n"
            "  name=pgdata_old size_bytes=1073741824 last_mount_days=120\n"
            "  name=recent_vol size_bytes=1024 last_mount_days=30\n"
            "class=build_cache\n"
            "  reclaimable_bytes=5000000\n"
            "docker_hygiene_end\n"
        )

    def test_volume_above_threshold_when_enabled_is_cleanup_candidate(self) -> None:
        result = parse_assess_v2_output(
            self._stdout(),
            vm_id="vm-01",
            volume_deletion_enabled=True,
            volume_last_mount_days_threshold=90,
        )
        vol = next(f for f in result.findings if f.name == "pgdata_old")
        assert vol.classification == FindingClassification.CLEANUP_CANDIDATE

    def test_volume_below_threshold_when_enabled_is_report_only(self) -> None:
        result = parse_assess_v2_output(
            self._stdout(),
            vm_id="vm-01",
            volume_deletion_enabled=True,
            volume_last_mount_days_threshold=90,
        )
        vol = next(f for f in result.findings if f.name == "recent_vol")
        assert vol.classification == FindingClassification.REPORT_ONLY

    def test_volume_at_threshold_boundary_is_report_only(self) -> None:
        stdout = (
            "reachable=yes\n"
            "docker_hygiene_begin\n"
            "class=volume_unreferenced\n"
            "  name=boundary_vol size_bytes=1024 last_mount_days=90\n"
            "docker_hygiene_end\n"
        )
        result = parse_assess_v2_output(
            stdout, vm_id="vm-01", volume_deletion_enabled=True, volume_last_mount_days_threshold=90
        )
        assert result.findings[0].classification == FindingClassification.REPORT_ONLY

    def test_build_cache_when_enabled_is_cleanup_candidate(self) -> None:
        result = parse_assess_v2_output(
            self._stdout(), vm_id="vm-01", build_cache_deletion_enabled=True
        )
        cache = next(f for f in result.findings if f.resource_class == DockerResourceClass.BUILD_CACHE)
        assert cache.classification == FindingClassification.CLEANUP_CANDIDATE

    def test_defaults_keep_volumes_report_only(self) -> None:
        # Without enabled flags, nothing changes from prior behaviour.
        result = parse_assess_v2_output(self._stdout(), vm_id="vm-01")
        for f in result.findings:
            assert f.classification == FindingClassification.REPORT_ONLY


class TestParseRemoveV2OutputV15:
    """parse_remove_v2_output handles volume and build_cache result lines."""

    def _volume_finding(self, name: str) -> object:
        from errander.models.docker_hygiene import DockerHygieneFinding
        return DockerHygieneFinding(
            resource_class=DockerResourceClass.VOLUME_UNREFERENCED,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            name=name,
            size_bytes=1024,
            last_mount_days=120,
        )

    def _build_cache_finding(self) -> object:
        from errander.models.docker_hygiene import DockerHygieneFinding
        return DockerHygieneFinding(
            resource_class=DockerResourceClass.BUILD_CACHE,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            name="build_cache",
            reclaimable_bytes=5_000_000,
        )

    def test_volume_removed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._volume_finding("pgdata_old"),)  # type: ignore[arg-type]
        stdout = "result class=volume_unreferenced id=pgdata_old status=removed reason=\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 1
        assert results[0].status == RemovalStatus.REMOVED

    def test_volume_drift_skipped(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._volume_finding("pgdata_old"),)  # type: ignore[arg-type]
        stdout = "result class=volume_unreferenced id=pgdata_old status=drift_skipped reason=volume_now_referenced\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert results[0].status == RemovalStatus.DRIFT_SKIPPED
        assert results[0].drift_reason == "volume_now_referenced"

    def test_build_cache_removed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._build_cache_finding(),)  # type: ignore[arg-type]
        stdout = "result class=build_cache id=build_cache status=removed reason=\n"
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 1
        assert results[0].status == RemovalStatus.REMOVED

    def test_missing_volume_result_synthesized_failed(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import parse_remove_v2_output
        from errander.models.docker_hygiene import RemovalStatus
        approved = (self._volume_finding("pgdata_old"),)  # type: ignore[arg-type]
        stdout = ""  # wrapper returned nothing
        results = parse_remove_v2_output(stdout, approved)  # type: ignore[arg-type]
        assert len(results) == 1
        assert results[0].status == RemovalStatus.FAILED
        assert results[0].error == "no_result_from_wrapper"


class TestExecuteNodeV15:
    """Execute node correctly handles volume and build_cache approval."""

    def _approval(self, findings: tuple, snapshot_hash: str = "abc123") -> object:
        from errander.models.docker_hygiene import ApprovalSurface, DockerHygieneApproval
        return DockerHygieneApproval(
            vm_id="dev/web-01",
            approved_findings=findings,
            snapshot_hash=snapshot_hash,
            surface=ApprovalSurface.TEST_INJECT,
            operator_id="test-user",
        )

    @pytest.mark.asyncio
    async def test_volume_approved_calls_wrapper(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import (
            DockerHygieneFinding,
            DockerHygieneAssessment,
            RemovalStatus,
            compute_assessment_hash,
        )
        vol = DockerHygieneFinding(
            resource_class=DockerResourceClass.VOLUME_UNREFERENCED,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            name="pgdata_old",
            size_bytes=1024,
            last_mount_days=120,
        )
        assessment = DockerHygieneAssessment(
            vm_id="dev/web-01",
            findings=(vol,),
            raw_output="",
            reachable=True,
            error=None,
        )
        approval = self._approval(
            findings=(vol,),
            snapshot_hash=compute_assessment_hash(assessment),
        )
        captured_commands: list[str] = []

        executor = _make_executor()

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            cmd = str(kwargs.get("command", args[4] if len(args) > 4 else ""))
            captured_commands.append(cmd)
            return _make_result("result class=volume_unreferenced id=pgdata_old status=removed reason=")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(dry_run=False, assessment=assessment, approval=approval)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value
        assert captured_commands, "wrapper must have been called"
        # Allowlist is embedded in the command via printf — check it includes the volume
        assert "class=volume_unreferenced" in captured_commands[0]
        assert "id=pgdata_old" in captured_commands[0]

    @pytest.mark.asyncio
    async def test_build_cache_approved_calls_wrapper(self) -> None:
        from errander.agent.subgraphs.docker_hygiene import execute_node
        from errander.models.docker_hygiene import (
            DockerHygieneFinding,
            DockerHygieneAssessment,
            compute_assessment_hash,
        )
        cache = DockerHygieneFinding(
            resource_class=DockerResourceClass.BUILD_CACHE,
            classification=FindingClassification.CLEANUP_CANDIDATE,
            name="build_cache",
            reclaimable_bytes=5_000_000,
        )
        assessment = DockerHygieneAssessment(
            vm_id="dev/web-01",
            findings=(cache,),
            raw_output="",
            reachable=True,
            error=None,
        )
        approval = self._approval(
            findings=(cache,),
            snapshot_hash=compute_assessment_hash(assessment),
        )

        executor = _make_executor()

        async def mock_execute(*args: object, **kwargs: object) -> SSHResult:
            return _make_result("result class=build_cache id=build_cache status=removed reason=")

        with patch.object(executor, "execute", side_effect=mock_execute):
            state = _base_state(dry_run=False, assessment=assessment, approval=approval)  # type: ignore[call-arg]
            result = await execute_node(state, executor=executor)

        assert result["status"] == ActionStatus.SUCCESS.value
