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
    DockerHygieneGraphState,
    _classify_image,
    _classify_stopped_container,
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

    def test_route_after_assess_always_ends_in_session1(self) -> None:
        from langgraph.graph import END
        assert route_after_assess(_base_state()) == END


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
