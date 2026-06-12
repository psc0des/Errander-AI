"""Tests for the durable HygieneApprovalStore (R3 process separation).

Mirrors tests/safety/test_approval_store.py — locks in the same race-safety
contracts for docker_hygiene approvals:
- decide() is atomic — concurrent deciders settle on exactly one winner
- wait_for_decision() owns timeouts and survives in-process decisions
- expire_overdue() marks past-expiry pending rows as timeout
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from errander.models.docker_hygiene import (
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)
from errander.safety.hygiene_store import HygieneApprovalStore


@pytest_asyncio.fixture
async def store() -> HygieneApprovalStore:
    from tests.conftest import make_test_db
    s = HygieneApprovalStore(make_test_db())
    await s.initialize()
    return s


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


async def _create(
    store: HygieneApprovalStore,
    batch_id: str = "batch-h001",
    vm_id: str = "prod/web-01",
    assessment: DockerHygieneAssessment | None = None,
    timeout_seconds: int = 1800,
) -> DockerHygieneAssessment:
    a = assessment if assessment is not None else _assessment((_dangling("sha256:a"),))
    await store.create(batch_id, vm_id, a, signed_token="tok-abc", timeout_seconds=timeout_seconds)
    return a


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------

class TestCreateAndGet:
    @pytest.mark.asyncio
    async def test_create_get_round_trip(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        row = await store.get("batch-h001", "prod/web-01")

        assert row is not None
        assert row.batch_id == "batch-h001"
        assert row.vm_id == "prod/web-01"
        assert row.signed_token == "tok-abc"
        assert row.status == "pending"
        assert row.decided_by is None
        assert row.snapshot_hash is None
        assert row.approved_items_json is None
        assert row.decided_at is None
        assert row.expires_at > row.posted_at
        assert row.assessment().vm_id == a.vm_id
        assert len(row.assessment().findings) == len(a.findings)
        assert row.approved_items() == []

    @pytest.mark.asyncio
    async def test_get_unknown_returns_none(self, store: HygieneApprovalStore) -> None:
        assert await store.get("no-such-batch", "no-such-vm") is None

    @pytest.mark.asyncio
    async def test_list_pending_and_count(self, store: HygieneApprovalStore) -> None:
        await _create(store, "batch-a", "vm-1")
        await _create(store, "batch-b", "vm-2")

        pending = await store.list_pending()
        assert [(p.batch_id, p.vm_id) for p in pending] == [("batch-a", "vm-1"), ("batch-b", "vm-2")]
        assert await store.count_pending() == 2

    @pytest.mark.asyncio
    async def test_recreate_pending_row_updates_assessment(self, store: HygieneApprovalStore) -> None:
        """Crash-restart re-entering the assess node refreshes the pending row."""
        await _create(store, assessment=_assessment((_dangling("sha256:a"),)))
        new_assessment = _assessment((_dangling("sha256:a"), _dangling("sha256:b")))
        await store.create("batch-h001", "prod/web-01", new_assessment, signed_token="tok-new")

        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None
        assert row.signed_token == "tok-new"
        assert len(row.assessment().findings) == 2

    @pytest.mark.asyncio
    async def test_recreate_does_not_resurrect_decided_row(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        await store.decide(
            "batch-h001", "prod/web-01",
            approved=False, decided_by="ui:admin",
            snapshot_hash=compute_assessment_hash(a),
        )

        await store.create("batch-h001", "prod/web-01", a, signed_token="tok-new")
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None and row.status == "rejected"


# ---------------------------------------------------------------------------
# decide — atomic, exactly one winner
# ---------------------------------------------------------------------------

class TestDecide:
    @pytest.mark.asyncio
    async def test_approve_records_decision(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        won = await store.decide(
            "batch-h001", "prod/web-01",
            approved=True, decided_by="ui:alice",
            snapshot_hash=compute_assessment_hash(a),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )

        assert won is True
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None
        assert row.status == "approved"
        assert row.decided_by == "ui:alice"
        assert row.decided_at is not None
        assert row.snapshot_hash == compute_assessment_hash(a)
        assert row.approved_items() == [{"resource_class": "image_dangling", "identity": "sha256:a"}]

    @pytest.mark.asyncio
    async def test_reject_records_decision(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        won = await store.decide(
            "batch-h001", "prod/web-01",
            approved=False, decided_by="ui:admin",
            snapshot_hash=compute_assessment_hash(a),
        )

        assert won is True
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None
        assert row.status == "rejected"
        assert row.approved_items_json is None

    @pytest.mark.asyncio
    async def test_second_decide_loses_and_does_not_overwrite(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        assert await store.decide(
            "batch-h001", "prod/web-01",
            approved=True, decided_by="ui:first",
            snapshot_hash=compute_assessment_hash(a),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )
        lost = await store.decide(
            "batch-h001", "prod/web-01",
            approved=False, decided_by="ui:second",
            snapshot_hash=compute_assessment_hash(a),
        )

        assert lost is False
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None
        assert row.status == "approved"
        assert row.decided_by == "ui:first"

    @pytest.mark.asyncio
    async def test_concurrent_decide_race_has_exactly_one_winner(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        snap = compute_assessment_hash(a)

        results = await asyncio.gather(
            store.decide(
                "batch-h001", "prod/web-01",
                approved=True, decided_by="ui:alice",
                snapshot_hash=snap,
                approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
            ),
            store.decide(
                "batch-h001", "prod/web-01",
                approved=False, decided_by="ui:bob",
                snapshot_hash=snap,
            ),
        )

        assert sorted(results) == [False, True]
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None
        if row.status == "approved":
            assert row.decided_by == "ui:alice"
        else:
            assert row.status == "rejected"
            assert row.decided_by == "ui:bob"

    @pytest.mark.asyncio
    async def test_decide_unknown_row_is_noop(self, store: HygieneApprovalStore) -> None:
        assert await store.decide(
            "no-such", "no-such-vm", approved=True, decided_by="ui:x",
        ) is False


# ---------------------------------------------------------------------------
# expire_overdue
# ---------------------------------------------------------------------------

class TestExpireOverdue:
    @pytest.mark.asyncio
    async def test_expire_overdue_marks_timeout(self, store: HygieneApprovalStore) -> None:
        await _create(store, "batch-old", "vm-old", timeout_seconds=0)
        await _create(store, "batch-new", "vm-new", timeout_seconds=1800)

        expired = await store.expire_overdue()

        assert expired == [("batch-old", "vm-old")]
        old = await store.get("batch-old", "vm-old")
        new = await store.get("batch-new", "vm-new")
        assert old is not None and old.status == "timeout"
        assert new is not None and new.status == "pending"

    @pytest.mark.asyncio
    async def test_expire_overdue_skips_already_decided(self, store: HygieneApprovalStore) -> None:
        a = await _create(store, "batch-decided", "vm-d", timeout_seconds=0)
        await store.decide(
            "batch-decided", "vm-d",
            approved=True, decided_by="ui:admin",
            snapshot_hash=compute_assessment_hash(a),
            approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
        )

        expired = await store.expire_overdue()

        assert expired == []
        row = await store.get("batch-decided", "vm-d")
        assert row is not None and row.status == "approved"


# ---------------------------------------------------------------------------
# wait_for_decision
# ---------------------------------------------------------------------------

class TestWaitForDecision:
    @pytest.mark.asyncio
    async def test_returns_on_in_process_decision(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)

        async def _decide_soon() -> None:
            await asyncio.sleep(0.05)
            await store.decide(
                "batch-h001", "prod/web-01",
                approved=True, decided_by="ui:admin",
                snapshot_hash=compute_assessment_hash(a),
                approved_items=[{"resource_class": "image_dangling", "identity": "sha256:a"}],
            )

        task = asyncio.create_task(_decide_soon())
        started = datetime.now(tz=UTC)
        row = await store.wait_for_decision("batch-h001", "prod/web-01", timeout_seconds=30)
        await task

        assert row is not None
        assert row.status == "approved"
        # In-process event wakeup — must return well before the poll interval.
        assert (datetime.now(tz=UTC) - started).total_seconds() < 5

    @pytest.mark.asyncio
    async def test_timeout_marks_row_and_returns_none(self, store: HygieneApprovalStore) -> None:
        await _create(store)

        result = await store.wait_for_decision("batch-h001", "prod/web-01", timeout_seconds=0)

        assert result is None
        row = await store.get("batch-h001", "prod/web-01")
        assert row is not None and row.status == "timeout"

    @pytest.mark.asyncio
    async def test_already_decided_returns_immediately(self, store: HygieneApprovalStore) -> None:
        a = await _create(store)
        await store.decide(
            "batch-h001", "prod/web-01",
            approved=False, decided_by="ui:admin",
            snapshot_hash=compute_assessment_hash(a),
        )

        row = await store.wait_for_decision("batch-h001", "prod/web-01", timeout_seconds=30)
        assert row is not None and row.status == "rejected"

    @pytest.mark.asyncio
    async def test_unknown_row_returns_none(self, store: HygieneApprovalStore) -> None:
        result = await store.wait_for_decision("no-such-batch", "no-such-vm", timeout_seconds=1)
        assert result is None
