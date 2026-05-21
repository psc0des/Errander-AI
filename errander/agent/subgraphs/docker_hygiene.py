"""Docker hygiene sub-graph — rich, object-level Docker assessment.

Replaces the bulk ``docker_prune`` action with a per-object workflow that
satisfies the Exact-Object Approval invariant (CLAUDE.md → AI Safety
Invariant). v1.1 lifecycle phases:

1. Validate — Docker available, wrapper mode is not disabled.
2. Assess (this session) — enumerate dangling images, unused images,
   stopped containers, unreferenced volumes, and build cache; classify
   each finding as ``cleanup_candidate`` / ``investigate`` / ``report_only``.
3. Approve (Session 2) — dual surface (Slack structured reply + web page),
   operator picks exact objects, artifact stored in ``ai_decisions``.
4. Execute (Session 2) — per-object remove wrapper with state re-validation,
   one audit row per removed object.

Risk tier: Medium (assessment is LOW, removal is MEDIUM — manifest reflects
the worst case).
Rollback strategy: Re-pull / re-create — removed Docker objects are gone.
This sub-graph never auto-removes; the operator picks objects explicitly.

This file in Session 1 implements validate + assess only. Execute lands in
Session 2 alongside the dual approval surface.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from errander.execution.privilege import privileged
from errander.models.actions import ActionStatus
from errander.models.docker_hygiene import (
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerHygieneRemovalResult,
    DockerResourceClass,
    FindingClassification,
    RemovalStatus,
    compute_assessment_hash,
)
from errander.models.manifest import ActionManifest

if TYPE_CHECKING:
    from errander.execution.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


# --- Classification thresholds (deterministic, no LLM) ---

UNUSED_IMAGE_CLEANUP_AGE_DAYS = 30
"""Unused (non-dangling) images older than this are cleanup candidates."""

STOPPED_CONTAINER_CLEANUP_AGE_HOURS = 168
"""Stopped containers (exit 0) older than this (7 days) are cleanup candidates."""

INVESTIGATE_CONTAINER_EXIT_CODES = frozenset({137, 139})
"""Exit codes that always flag a stopped container for investigation.

137 = SIGKILL (typically OOM kill via cgroup); 139 = SIGSEGV. SIGTERM (143)
is deliberately *not* here — it's an ordinary stop signal used by Docker
itself for graceful shutdowns.
"""


# --- Manifest ---

MANIFEST = ActionManifest(
    name="docker_hygiene",
    default_enabled=False,
    risk_tier="MEDIUM",
    command_modes=("disabled", "wrapper"),
    required_binaries=("/usr/bin/docker",),
    required_wrappers=(
        "/usr/local/sbin/errander-docker-assess-v2",
        # remove-v2 wrapper is required at runtime in Session 2 but listed
        # now so --check-targets surfaces missing installs immediately.
        "/usr/local/sbin/errander-docker-remove-v2",
    ),
    setup_doc="SETUP.md#docker-hygiene",
)


# --- State ---

class DockerHygieneGraphState(TypedDict, total=False):
    """State flowing through the docker_hygiene sub-graph."""

    vm_id: str
    os_family: str
    dry_run: bool
    status: str
    error: str | None

    docker_available: bool
    docker_command_mode: str  # "wrapper" | "disabled" (no direct_sudo for hygiene)

    assessment: DockerHygieneAssessment
    nothing_to_do: bool

    # Approval artifact injected by the parent VM graph after operator approval.
    # When absent, the sub-graph ends after assess (Slack/web surface didn't
    # produce an approval yet, or the operator rejected). When present and
    # nothing_approved(), the sub-graph also ends without executing.
    approval: DockerHygieneApproval | None

    # Execution results — one DockerHygieneRemovalResult per approved object.
    removal_results: tuple[DockerHygieneRemovalResult, ...]


# --- Node functions ---

def validate_node(state: DockerHygieneGraphState) -> dict[str, Any]:
    """Validate that Docker is available and mode is not disabled.

    docker_hygiene only supports ``wrapper`` mode — ``direct_sudo`` cannot
    satisfy the per-object validation requirement of the remove wrapper.
    """
    mode = state.get("docker_command_mode", "wrapper")
    if mode == "disabled":
        logger.info(
            "docker_hygiene disabled for %s (command_mode=disabled)",
            state.get("vm_id", "unknown"),
        )
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "docker_hygiene command_mode=disabled",
        }
    if mode != "wrapper":
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": (
                f"docker_hygiene requires command_mode=wrapper, got '{mode}'. "
                "Per-object validation cannot be satisfied without the wrapper."
            ),
        }

    if not state.get("docker_available", True):
        logger.info(
            "Docker not available on %s — skipping hygiene assessment",
            state.get("vm_id", "unknown"),
        )
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "Docker not installed or not running",
        }

    return {"status": ActionStatus.PENDING.value}


async def assess_node(
    state: DockerHygieneGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Run the assess-v2 wrapper, parse output, classify each finding.

    Idempotent: if the wrapper reports no objects across any class, the
    sub-graph ends with ``nothing_to_do=True``.
    """
    vm_id = state["vm_id"]
    target = _get_connection_params(state)

    result = await executor.execute(
        vm_id,
        target["hostname"],
        target["username"],
        target["key_path"],
        command=privileged("/usr/local/sbin/errander-docker-assess-v2"),
        dry_run=False,
    )
    if not result.success:
        return {
            "status": ActionStatus.FAILED.value,
            "error": "docker assess-v2 wrapper failed",
            "nothing_to_do": True,
        }

    assessment = parse_assess_v2_output(result.stdout, vm_id=vm_id)

    if not assessment.reachable:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": assessment.error or "Docker daemon not reachable",
            "assessment": assessment,
            "nothing_to_do": True,
        }

    if assessment.nothing_to_surface():
        logger.info("docker_hygiene found no objects on %s — nothing to do", vm_id)
        return {
            "assessment": assessment,
            "nothing_to_do": True,
            "status": ActionStatus.SKIPPED.value,
        }

    by_class = assessment.by_class()
    logger.info(
        "docker_hygiene on %s found %d findings across %d classes "
        "(%d cleanup candidates, %d investigate)",
        vm_id,
        len(assessment.findings),
        len(by_class),
        len(assessment.cleanup_candidates()),
        len(assessment.investigate()),
    )
    return {
        "assessment": assessment,
        "nothing_to_do": False,
    }


# --- Parser ---

def parse_assess_v2_output(stdout: str, *, vm_id: str) -> DockerHygieneAssessment:
    """Parse the errander-docker-assess-v2 wrapper output.

    Expected format::

        reachable=yes|no
        error=<optional>
        docker_hygiene_begin
        class=image_dangling
          id=sha256:abc... size_bytes=N age_days=N last_tag=tag
          id=sha256:def... size_bytes=N age_days=N last_tag=<none>
        class=image_unused
          id=sha256:ghi... size_bytes=N age_days=N last_tag=tag
        class=container_stopped
          id=abc123 name=foo exit_code=N stopped_age_hours=N
        class=volume_unreferenced
          name=vol_a size_bytes=N last_mount_days=N
        class=build_cache
          reclaimable_bytes=N
        docker_hygiene_end

    Whitespace around values is tolerated. Unknown keys per finding are
    ignored. Missing required fields produce a finding with None for those
    fields (classification still applies to whatever is present).
    """
    reachable = True
    error: str | None = None
    findings: list[DockerHygieneFinding] = []
    current_class: DockerResourceClass | None = None
    in_block = False

    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "docker_hygiene_begin":
            in_block = True
            continue
        if stripped == "docker_hygiene_end":
            in_block = False
            continue
        if not in_block:
            # Pre-block header fields: reachable=, error=
            key, sep, value = stripped.partition("=")
            if not sep:
                continue
            key, value = key.strip(), value.strip()
            if key == "reachable":
                reachable = value.lower() in ("yes", "true", "1")
            elif key == "error":
                error = value or None
            continue

        # Inside docker_hygiene block
        if stripped.startswith("class="):
            class_value = stripped.split("=", 1)[1].strip()
            try:
                current_class = DockerResourceClass(class_value)
            except ValueError:
                logger.warning("unknown docker_hygiene class: %r", class_value)
                current_class = None
            continue
        if current_class is None:
            continue
        kv = _parse_kv_line(stripped)
        if not kv:
            continue
        finding = _build_finding(current_class, kv)
        if finding is not None:
            findings.append(finding)

    return DockerHygieneAssessment(
        vm_id=vm_id,
        findings=tuple(findings),
        raw_output=stdout,
        reachable=reachable,
        error=error,
    )


def _parse_kv_line(line: str) -> dict[str, str]:
    """Parse a whitespace-separated key=value line. Bare tokens are skipped.

    Values containing '=' (e.g. ``last_tag=repo/x:v1=preview``) keep their
    full value — we split each token only on the *first* ``=``.
    """
    out: dict[str, str] = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if not sep:
            continue
        out[key.strip()] = value.strip()
    return out


def _build_finding(
    resource_class: DockerResourceClass,
    kv: dict[str, str],
) -> DockerHygieneFinding | None:
    """Construct a finding from a parsed key-value line + classify it.

    Returns None when the line cannot be parsed into a meaningful finding
    (e.g., build_cache without reclaimable_bytes, image without id).
    """
    if resource_class == DockerResourceClass.BUILD_CACHE:
        reclaimable = _safe_int(kv.get("reclaimable_bytes"))
        if reclaimable is None or reclaimable == 0:
            return None
        return DockerHygieneFinding(
            resource_class=resource_class,
            classification=FindingClassification.REPORT_ONLY,
            reclaimable_bytes=reclaimable,
        )

    if resource_class == DockerResourceClass.VOLUME_UNREFERENCED:
        name = kv.get("name")
        if not name:
            return None
        return DockerHygieneFinding(
            resource_class=resource_class,
            classification=FindingClassification.REPORT_ONLY,
            name=name,
            size_bytes=_safe_int(kv.get("size_bytes")),
            last_mount_days=_safe_int(kv.get("last_mount_days")),
        )

    if resource_class == DockerResourceClass.CONTAINER_STOPPED:
        object_id = kv.get("id")
        if not object_id:
            return None
        exit_code = _safe_int(kv.get("exit_code"))
        stopped_age_hours = _safe_int(kv.get("stopped_age_hours"))
        classification = _classify_stopped_container(exit_code, stopped_age_hours)
        return DockerHygieneFinding(
            resource_class=resource_class,
            classification=classification,
            object_id=object_id,
            name=kv.get("name"),
            exit_code=exit_code,
            stopped_age_hours=stopped_age_hours,
        )

    # IMAGE_DANGLING or IMAGE_UNUSED
    object_id = kv.get("id")
    if not object_id:
        return None
    age_days = _safe_int(kv.get("age_days"))
    last_tag = kv.get("last_tag")
    if last_tag == "<none>":
        last_tag = None
    classification = _classify_image(resource_class, age_days)
    return DockerHygieneFinding(
        resource_class=resource_class,
        classification=classification,
        object_id=object_id,
        size_bytes=_safe_int(kv.get("size_bytes")),
        age_days=age_days,
        last_tag=last_tag,
    )


# --- Classification rules (deterministic Python — no LLM) ---

def _classify_image(
    resource_class: DockerResourceClass,
    age_days: int | None,
) -> FindingClassification:
    """Classify an image finding.

    - IMAGE_DANGLING: always ``cleanup_candidate``.
    - IMAGE_UNUSED: ``cleanup_candidate`` if age_days > 30; otherwise ``report_only``.
      (Note: actual removal of unused images is v1.2 scope. v1.1 only acts on
      dangling. But classification surfaces both for visibility.)
    """
    if resource_class == DockerResourceClass.IMAGE_DANGLING:
        return FindingClassification.CLEANUP_CANDIDATE
    if (
        resource_class == DockerResourceClass.IMAGE_UNUSED
        and age_days is not None
        and age_days > UNUSED_IMAGE_CLEANUP_AGE_DAYS
    ):
        return FindingClassification.CLEANUP_CANDIDATE
    return FindingClassification.REPORT_ONLY


def _classify_stopped_container(
    exit_code: int | None,
    stopped_age_hours: int | None,
) -> FindingClassification:
    """Classify a stopped container finding.

    - exit_code in {137, 139} → ``investigate`` (OOM kill or SIGSEGV).
    - exit_code == 0 AND stopped_age_hours > 168 → ``cleanup_candidate``.
    - Anything else → ``report_only`` (recent, unknown signal, or middle-aged).
    """
    if exit_code is not None and exit_code in INVESTIGATE_CONTAINER_EXIT_CODES:
        return FindingClassification.INVESTIGATE
    if (
        exit_code == 0
        and stopped_age_hours is not None
        and stopped_age_hours > STOPPED_CONTAINER_CLEANUP_AGE_HOURS
    ):
        return FindingClassification.CLEANUP_CANDIDATE
    return FindingClassification.REPORT_ONLY


# --- Helpers ---

def _safe_int(s: str | None) -> int | None:
    """Best-effort int parsing — returns None for empty / non-numeric."""
    if s is None or s == "":
        return None
    with contextlib.suppress(ValueError):
        return int(s)
    return None


def _get_connection_params(state: DockerHygieneGraphState) -> dict[str, str]:
    """Extract SSH connection params from state.

    These keys aren't part of DockerHygieneGraphState but flow through from the
    parent VM graph at dispatch time (Session 2). With total=False we can read
    them via .get() without TypedDict complaints.
    """
    raw: dict[str, object] = dict(state)
    return {
        "hostname": str(raw.get("hostname", "")),
        "username": str(raw.get("username", "")),
        "key_path": str(raw.get("key_path", "")),
    }


# --- Execute node (Session 2a) ---

async def execute_node(
    state: DockerHygieneGraphState,
    *,
    executor: SandboxExecutor,
) -> dict[str, Any]:
    """Invoke the remove-v2 wrapper for each approved object and parse results.

    Drift handling lives in the wrapper itself: it re-queries each object's
    current state and skips drifted ones with ``status=drift_skipped``. This
    function parses the per-object results into ``DockerHygieneRemovalResult``
    instances. The caller is responsible for writing one audit row per result
    (see :func:`_run_docker_hygiene` in ``vm_graph.py``).

    Dry-run: skip execution, return an empty results tuple with status
    DRY_RUN_OK. The audit log records the dry-run intent at the action level
    (via vm_graph.py).
    """
    approval = state.get("approval")
    if approval is None:
        # No approval injected — should not happen if routing is correct.
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "execute_node reached without an approval artifact in state",
            "removal_results": (),
        }
    if approval.nothing_approved():
        return {
            "status": ActionStatus.SKIPPED.value,
            "removal_results": (),
        }

    # Drift gate: refuse to honor an approval against a stale assessment.
    assessment = state.get("assessment")
    if assessment is not None:
        current_hash = compute_assessment_hash(assessment)
        if current_hash != approval.snapshot_hash:
            logger.warning(
                "docker_hygiene approval snapshot mismatch on %s "
                "(approval=%s, current=%s) — refusing to execute",
                state.get("vm_id", "unknown"),
                approval.snapshot_hash,
                current_hash,
            )
            return {
                "status": ActionStatus.SKIPPED.value,
                "error": "assessment drifted between approval and execution",
                "removal_results": (),
            }

    if state.get("dry_run", True):
        # Dry-run path: don't actually invoke the wrapper. Synthesise "would
        # remove" results for visibility but mark status DRY_RUN_OK.
        synthetic = tuple(
            DockerHygieneRemovalResult(finding=f, status=RemovalStatus.REMOVED)
            for f in approval.approved_findings
        )
        return {
            "status": ActionStatus.DRY_RUN_OK.value,
            "removal_results": synthetic,
        }

    vm_id = state["vm_id"]
    target = _get_connection_params(state)
    allowlist_text = _build_allowlist(approval.approved_findings)

    # printf %s <quoted-allowlist> | sudo -n /usr/local/sbin/errander-docker-remove-v2
    command = (
        f"printf %s {shlex.quote(allowlist_text)} | "
        f"{privileged('/usr/local/sbin/errander-docker-remove-v2')}"
    )

    result = await executor.execute(
        vm_id,
        target["hostname"],
        target["username"],
        target["key_path"],
        command=command,
        dry_run=False,
    )

    if not result.success:
        return {
            "status": ActionStatus.FAILED.value,
            "error": f"remove-v2 wrapper failed: {result.stderr.strip()[:200]}",
            "removal_results": (),
        }

    results = parse_remove_v2_output(result.stdout, approval.approved_findings)

    # Status reflects the aggregate outcome:
    # - any FAILED → FAILED
    # - any REMOVED → SUCCESS
    # - all DRIFT_SKIPPED or SKIPPED_NOT_FOUND → SKIPPED
    if any(r.status == RemovalStatus.FAILED for r in results):
        agg_status = ActionStatus.FAILED.value
    elif any(r.status == RemovalStatus.REMOVED for r in results):
        agg_status = ActionStatus.SUCCESS.value
    else:
        agg_status = ActionStatus.SKIPPED.value

    return {
        "status": agg_status,
        "removal_results": results,
    }


def _build_allowlist(findings: tuple[DockerHygieneFinding, ...]) -> str:
    """Render the approved findings as the remove-v2 wrapper's stdin format."""
    lines = [
        f"class={f.resource_class.value} id={f.identity} expected={f.classification.value}"
        for f in findings
    ]
    # Trailing newline so the wrapper's `while read` loop processes the last line.
    return "\n".join(lines) + "\n"


# --- Remove-v2 output parser ---

def parse_remove_v2_output(
    stdout: str,
    approved: tuple[DockerHygieneFinding, ...],
) -> tuple[DockerHygieneRemovalResult, ...]:
    """Parse the per-object output of the remove-v2 wrapper.

    Expected format (one line per object)::

        result class=<class> id=<id> status=<status> reason=<text>

    Each result is matched back to its DockerHygieneFinding by (class, identity).
    Findings that have no matching result line are recorded as FAILED with
    reason="no_result_from_wrapper" — never silently dropped.
    """
    by_key: dict[tuple[str, str], DockerHygieneFinding] = {
        (f.resource_class.value, f.identity): f for f in approved
    }
    seen: set[tuple[str, str]] = set()
    results: list[DockerHygieneRemovalResult] = []

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("result "):
            continue
        kv = _parse_kv_line(line[len("result "):])
        obj_class = kv.get("class", "")
        obj_id = kv.get("id", "")
        status_str = kv.get("status", "")
        reason = kv.get("reason", "") or None

        key = (obj_class, obj_id)
        finding = by_key.get(key)
        if finding is None:
            # The wrapper returned a result for an object we didn't approve.
            # Log loudly — this should never happen.
            logger.error(
                "remove-v2 returned result for un-approved object: class=%s id=%s",
                obj_class, obj_id,
            )
            continue

        try:
            status = RemovalStatus(status_str)
        except ValueError:
            status = RemovalStatus.FAILED
            reason = f"unknown_status:{status_str}"

        drift_reason = reason if status == RemovalStatus.DRIFT_SKIPPED else None
        error = reason if status == RemovalStatus.FAILED else None
        results.append(DockerHygieneRemovalResult(
            finding=finding,
            status=status,
            drift_reason=drift_reason,
            error=error,
        ))
        seen.add(key)

    # Any approved finding without a result → FAILED (never silently dropped)
    for key, finding in by_key.items():
        if key not in seen:
            results.append(DockerHygieneRemovalResult(
                finding=finding,
                status=RemovalStatus.FAILED,
                error="no_result_from_wrapper",
            ))

    return tuple(results)


# --- Routing ---

def route_after_validate(state: DockerHygieneGraphState) -> str:
    """Route after validation: continue to assess or abort."""
    if state.get("status") in (ActionStatus.FAILED.value, ActionStatus.SKIPPED.value):
        return END
    return "assess"


def route_after_assess(state: DockerHygieneGraphState) -> str:
    """Route after assessment.

    - nothing_to_do → END (idempotent — no findings to surface).
    - approval present → execute (operator approved exact objects).
    - approval absent → END (report-only path; Session 2b will inject an
      approval after Slack/web surfaces decide). vm_graph.py is responsible
      for re-invoking the sub-graph with an approval injected when one arrives.
    """
    if state.get("nothing_to_do"):
        return END
    if state.get("approval") is not None:
        return "execute"
    return END


# --- Graph builder ---

def build_docker_hygiene_subgraph(
    executor: SandboxExecutor,
) -> StateGraph[DockerHygieneGraphState]:
    """Construct the docker_hygiene sub-graph (validate → assess → execute → END).

    Execute node is reached only when an approval artifact is injected into
    state. Without an approval, the graph ends after assess — the assessment
    becomes input for the Slack/web approval surfaces (Session 2b).
    """
    builder: StateGraph[DockerHygieneGraphState] = StateGraph(DockerHygieneGraphState)

    async def _assess(state: DockerHygieneGraphState) -> dict[str, Any]:
        return await assess_node(state, executor=executor)

    async def _execute(state: DockerHygieneGraphState) -> dict[str, Any]:
        return await execute_node(state, executor=executor)

    builder.add_node("validate", validate_node)
    builder.add_node("assess", _assess)
    builder.add_node("execute", _execute)

    builder.set_entry_point("validate")

    builder.add_conditional_edges("validate", route_after_validate, ["assess", END])
    builder.add_conditional_edges("assess", route_after_assess, ["execute", END])
    builder.add_edge("execute", END)

    return builder
