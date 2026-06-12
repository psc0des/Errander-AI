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
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from unittest.mock import AsyncMock

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
from errander.safety.hygiene_store import HygieneApprovalRow, HygieneApprovalStore
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


def _pending_row(
    batch_id: str,
    vm_id: str,
    assessment: DockerHygieneAssessment,
) -> HygieneApprovalRow:
    """Build a pending HygieneApprovalRow for use in mock store."""
    now = datetime.now(UTC)
    return HygieneApprovalRow(
        id=1,
        batch_id=batch_id,
        vm_id=vm_id,
        assessment_json=assessment.to_json(),
        signed_token="",
        posted_at=now,
        expires_at=now + timedelta(hours=1),
        status="pending",
    )


def _app_with_store(store: HygieneApprovalStore) -> web.Application:
    """Build a minimal app with hygiene_store in app state — skip startup hooks."""
    app = web.Application()
    app["hygiene_store"] = store
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


def _mock_store(*, row: HygieneApprovalRow | None = None) -> AsyncMock:
    """Build an AsyncMock store with get() returning row."""
    store = AsyncMock(spec=HygieneApprovalStore)
    store.get = AsyncMock(return_value=row)
    store.decide = AsyncMock(return_value=True)
    return store


# ---------------------------------------------------------------------------
# GET handler
# ---------------------------------------------------------------------------

class TestHygieneApproveGet:
    def test_missing_token_returns_400(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        app = _app_with_store(_mock_store())
        req = make_mocked_request("GET", "/ui/docker-hygiene/approve", app=app)
        resp = _run(handle_hygiene_approve_get(req))
        assert resp.status == 400
        assert "Missing token" in resp.text

    def test_tampered_token_returns_400(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        app = _app_with_store(_mock_store())
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
        # store.get returns None → no pending row
        app = _app_with_store(_mock_store(row=None))
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
        a = _assessment((_dangling("sha256:abc123def456"),))
        row = _pending_row("b1", "prod/web-01", a)
        app = _app_with_store(_mock_store(row=row))
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

    def test_missing_store_returns_503(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        app = web.Application()  # no hygiene_store key
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
    def test_reject_calls_decide_with_approved_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        a = _assessment((_dangling("sha256:a"),))
        row = _pending_row("b1", "v1", a)
        store = _mock_store(row=row)
        app = _app_with_store(store)
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

        store.decide.assert_called_once()
        call_kwargs = store.decide.call_args.kwargs
        assert call_kwargs["approved"] is False
        assert call_kwargs.get("approved_items") is None

    def test_approve_selected_calls_decide_with_correct_items(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        a = _assessment((
            _dangling("sha256:a"),
            _dangling("sha256:b"),
            _dangling("sha256:c"),
        ))
        row = _pending_row("b1", "v1", a)
        store = _mock_store(row=row)
        app = _app_with_store(store)
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

        store.decide.assert_called_once()
        call_kwargs = store.decide.call_args.kwargs
        assert call_kwargs["approved"] is True
        approved_items = call_kwargs["approved_items"]
        assert approved_items is not None
        assert len(approved_items) == 2
        identities = {item["identity"] for item in approved_items}
        assert identities == {"sha256:a", "sha256:c"}
        # Snapshot hash must match assessment
        assert call_kwargs["snapshot_hash"] == compute_assessment_hash(a)

    def test_approve_with_no_checkboxes_calls_decide_approved_true_no_items(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        a = _assessment((_dangling("sha256:a"),))
        row = _pending_row("b1", "v1", a)
        store = _mock_store(row=row)
        app = _app_with_store(store)
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "approve",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 200

        call_kwargs = store.decide.call_args.kwargs
        assert call_kwargs["approved"] is True
        assert call_kwargs.get("approved_items") is None

    def test_invalid_decision_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", _SECRET.decode())
        app = _app_with_store(_mock_store())
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
        app = _app_with_store(_mock_store())
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
        # store.get returns None → no pending row
        app = _app_with_store(_mock_store(row=None))
        tok = make_signed_token(
            {"batch_id": "b1", "vm_id": "v1"},
            ttl_seconds=60, secret=_SECRET,
        )
        req = _mock_post_request(app, form_data={
            "token": tok, "decision": "reject",
        })
        resp = _run(handle_hygiene_approve_post(req))
        assert resp.status == 404
