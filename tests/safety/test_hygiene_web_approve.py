"""Tests for the docker_hygiene web approval routes (Session 2b-ii).

GET /ui/docker-hygiene/approve?token=<signed_token>  → render the form
POST /ui/docker-hygiene/approve                       → process submission

Uses aiohttp.test_utils.make_mocked_request to exercise handlers directly
without starting the full Operations Hub (avoids DB / inventory init).

Test methods are sync and drive a fresh event loop per test via
``asyncio.new_event_loop()`` rather than relying on pytest-asyncio. This
isolates each test from runner-state pollution that the full test suite
otherwise propagates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import MultiDict

from errander.integrations.signed_url import make_signed_token
from errander.models.docker_hygiene import (
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)
from errander.safety.hygiene_approval import HygieneApprovalManager
from errander.web.server import (
    handle_hygiene_approve_get,
    handle_hygiene_approve_post,
)

_SECRET = b"web-test-secret-32-bytes-or-longer"

_T = TypeVar("_T")


def _run(coro: Awaitable[_T]) -> _T:  # noqa: UP047  -- TypeVar form needed for compat
    """Drive a coroutine to completion on a fresh, scoped event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- Builders ---

def _dangling(obj_id: str, age: int = 10) -> DockerHygieneFinding:
    return DockerHygieneFinding(
        resource_class=DockerResourceClass.IMAGE_DANGLING,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        object_id=obj_id,
        size_bytes=100 * 1024 * 1024,
        age_days=age,
    )


def _assessment(findings: tuple[DockerHygieneFinding, ...]) -> DockerHygieneAssessment:
    return DockerHygieneAssessment(vm_id="prod/web-01", findings=findings)


def _app_with_manager(manager: HygieneApprovalManager) -> web.Application:
    """Build a minimal app with hygiene_manager in app state — skip startup hooks."""
    app = web.Application()
    app["hygiene_manager"] = manager
    return app


def _mock_get_request(app: web.Application, token: str) -> Any:
    """Mock GET request with token in query string."""
    return make_mocked_request("GET", f"/ui/docker-hygiene/approve?token={token}", app=app)


def _mock_post_request(
    app: web.Application,
    *,
    form_data: dict[str, str],
) -> Any:
    """Mock POST request with form data."""
    req = make_mocked_request("POST", "/ui/docker-hygiene/approve", app=app)

    async def _post() -> MultiDict[str]:
        return MultiDict(form_data)

    req.post = _post  # type: ignore[assignment]
    return req


# ---------------------------------------------------------------------------
# GET handler
# ---------------------------------------------------------------------------

class TestHygieneApproveGet:
    def test_missing_token_returns_400(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        req = make_mocked_request("GET", "/ui/docker-hygiene/approve", app=app)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 400
        assert "Missing token" in resp.text

    def test_tampered_token_returns_400(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        bad_token = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60,
            secret=b"different-secret",
        )
        req = _mock_get_request(app, bad_token)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 400
        assert "Invalid" in resp.text

    def test_no_pending_returns_404(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60,
            secret=_SECRET,
        )
        req = _mock_get_request(app, tok)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 404
        assert "already been resolved" in resp.text or "expired" in resp.text

    def test_renders_form_when_pending(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:abc123def456"),))
        mgr.register("b1", "prod/web-01", a)
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "prod/web-01"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_get_request(app, tok)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 200
        body = resp.text
        assert "Docker hygiene approval" in body
        assert "sha256:abc123def456"[:32] in body
        assert 'name="token"' in body
        assert "Approve selected" in body
        assert "Reject all" in body

    def test_missing_manager_returns_503(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        app = web.Application()
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_get_request(app, tok)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 503


# ---------------------------------------------------------------------------
# POST handler
# ---------------------------------------------------------------------------

class TestHygieneApprovePost:
    def test_reject_resolves_with_empty_approval(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        mgr.register("b1", "v1", a)
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "reject",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 200
        assert "Rejected" in resp.text

        history = mgr.get_history()
        assert len(history) == 1
        assert history[0].approval is not None
        assert history[0].approval.approved_findings == ()

    def test_approve_selected_resolves_with_checked_findings(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        a = _assessment((
            _dangling("sha256:a"),
            _dangling("sha256:b"),
            _dangling("sha256:c"),
        ))
        mgr.register("b1", "v1", a)
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "approve",
            "finding_dangling_1": "on",
            "finding_dangling_3": "on",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 200
        assert "Approved" in resp.text

        history = mgr.get_history()
        assert len(history) == 1
        approval = history[0].approval
        assert approval is not None
        assert len(approval.approved_findings) == 2
        ids = {f.object_id for f in approval.approved_findings}
        assert ids == {"sha256:a", "sha256:c"}
        assert approval.snapshot_hash == compute_assessment_hash(a)

    def test_approve_with_no_checkboxes_yields_empty_approval(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        a = _assessment((_dangling("sha256:a"),))
        mgr.register("b1", "v1", a)
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "approve",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 200

        history = mgr.get_history()
        assert history[0].approval.approved_findings == ()  # type: ignore[union-attr]

    def test_invalid_decision_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "maybe_later",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 400

    def test_tampered_token_rejected_on_post(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defence in depth: POST re-verifies the token, doesn't trust GET."""
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        bad_token = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=b"different",
        )
        req = _mock_post_request(app, form_data={
            "token": bad_token, "decision": "approve",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 400

    def test_post_to_unknown_pending_returns_404(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        mgr = HygieneApprovalManager()
        app = _app_with_manager(mgr)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "reject",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 404
