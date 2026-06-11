"""Tests for audit logging to SQLite."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from errander.models.events import AuditEvent, EventType
from errander.safety.audit import AuditStore, AuditWriteError
from tests.conftest import make_test_db


def _make_event(
    event_type: EventType = EventType.ACTION_STARTED,
    batch_id: str = "batch-001",
    vm_id: str | None = "dev/web-01",
    action_type: str | None = "disk_cleanup",
    detail: str = "Starting disk cleanup",
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        batch_id=batch_id,
        vm_id=vm_id,
        action_type=action_type,
        detail=detail,
        timestamp=datetime.now(tz=UTC),
        metadata=metadata or {},
    )


class TestAuditStoreLifecycle:
    """Tests for AuditStore connection management."""

    async def test_context_manager(self) -> None:
        db = make_test_db()
        async with AuditStore(db) as store:
            assert store._db is db

    async def test_manual_init_close(self) -> None:
        db = make_test_db()
        store = AuditStore(db)
        await store.initialize()
        assert store._db is db
        await store.close()

    async def test_write_failure_in_strict_mode_raises_audit_error(self) -> None:
        """A failing database write in strict mode raises AuditWriteError."""
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        store = AuditStore(make_test_db(), strict_mode=True)

        @asynccontextmanager
        async def _fail():
            raise OperationalError(None, None, Exception("connection lost"))
            yield  # noqa: B901

        with patch.object(store._db, "begin", side_effect=lambda: _fail()), \
                pytest.raises(AuditWriteError):
            await store.log_event(_make_event())

    async def test_double_close_is_safe(self) -> None:
        store = AuditStore(make_test_db())
        await store.initialize()
        await store.close()
        await store.close()  # should not raise


class TestAuditStoreWrite:
    """Tests for writing audit events."""

    async def test_log_single_event(self) -> None:
        async with AuditStore(make_test_db()) as store:
            event = _make_event()
            await store.log_event(event)
            events = await store.get_events()
            assert len(events) == 1
            assert events[0].event_type == EventType.ACTION_STARTED
            assert events[0].batch_id == "batch-001"

    async def test_log_event_with_none_vm_id(self) -> None:
        async with AuditStore(make_test_db()) as store:
            event = _make_event(
                event_type=EventType.BATCH_STARTED,
                vm_id=None,
                action_type=None,
            )
            await store.log_event(event)
            events = await store.get_events()
            assert events[0].vm_id is None
            assert events[0].action_type is None

    async def test_log_event_preserves_metadata(self) -> None:
        async with AuditStore(make_test_db()) as store:
            event = _make_event(
                metadata={"packages": ["nginx", "curl"], "disk_freed_mb": 512},
            )
            await store.log_event(event)
            events = await store.get_events()
            assert events[0].metadata["packages"] == ["nginx", "curl"]
            assert events[0].metadata["disk_freed_mb"] == 512

    async def test_log_event_preserves_timestamp(self) -> None:
        async with AuditStore(make_test_db()) as store:
            ts = datetime(2026, 3, 21, 14, 30, 0, tzinfo=UTC)
            event = _make_event()
            event = AuditEvent(
                event_type=EventType.ACTION_COMPLETED,
                batch_id="batch-001",
                detail="done",
                timestamp=ts,
            )
            await store.log_event(event)
            events = await store.get_events()
            assert events[0].timestamp.year == 2026
            assert events[0].timestamp.month == 3
            assert events[0].timestamp.hour == 14

    async def test_log_multiple_events(self) -> None:
        async with AuditStore(make_test_db()) as store:
            for i in range(5):
                await store.log_event(_make_event(batch_id=f"batch-{i:03d}"))
            events = await store.get_events()
            assert len(events) == 5

    async def test_all_event_types_stored(self) -> None:
        all_types = list(EventType)
        async with AuditStore(make_test_db()) as store:
            for et in all_types:
                await store.log_event(_make_event(event_type=et))
            events = await store.get_events(limit=len(all_types) + 5)
            stored_types = {e.event_type for e in events}
            assert stored_types == set(EventType)


class TestAuditStoreQuery:
    """Tests for querying audit events."""

    async def test_filter_by_batch_id(self) -> None:
        async with AuditStore(make_test_db()) as store:
            await store.log_event(_make_event(batch_id="batch-A"))
            await store.log_event(_make_event(batch_id="batch-B"))
            await store.log_event(_make_event(batch_id="batch-A"))

            events = await store.get_events(batch_id="batch-A")
            assert len(events) == 2
            assert all(e.batch_id == "batch-A" for e in events)

    async def test_filter_by_vm_id(self) -> None:
        async with AuditStore(make_test_db()) as store:
            await store.log_event(_make_event(vm_id="dev/web-01"))
            await store.log_event(_make_event(vm_id="prod/db-01"))
            await store.log_event(_make_event(vm_id="dev/web-01"))

            events = await store.get_events(vm_id="dev/web-01")
            assert len(events) == 2

    async def test_filter_by_event_type(self) -> None:
        async with AuditStore(make_test_db()) as store:
            await store.log_event(_make_event(event_type=EventType.ACTION_STARTED))
            await store.log_event(_make_event(event_type=EventType.ACTION_COMPLETED))
            await store.log_event(_make_event(event_type=EventType.ACTION_FAILED))

            events = await store.get_events(event_type=EventType.ACTION_COMPLETED)
            assert len(events) == 1
            assert events[0].event_type == EventType.ACTION_COMPLETED

    async def test_combined_filters(self) -> None:
        async with AuditStore(make_test_db()) as store:
            await store.log_event(
                _make_event(batch_id="batch-A", vm_id="dev/web-01"),
            )
            await store.log_event(
                _make_event(batch_id="batch-A", vm_id="prod/db-01"),
            )
            await store.log_event(
                _make_event(batch_id="batch-B", vm_id="dev/web-01"),
            )

            events = await store.get_events(batch_id="batch-A", vm_id="dev/web-01")
            assert len(events) == 1

    async def test_limit_results(self) -> None:
        async with AuditStore(make_test_db()) as store:
            for i in range(10):
                await store.log_event(_make_event(batch_id=f"batch-{i:03d}"))

            events = await store.get_events(limit=3)
            assert len(events) == 3

    async def test_results_ordered_most_recent_first(self) -> None:
        async with AuditStore(make_test_db()) as store:
            ts1 = datetime(2026, 1, 1, tzinfo=UTC)
            ts2 = datetime(2026, 6, 1, tzinfo=UTC)
            ts3 = datetime(2026, 12, 1, tzinfo=UTC)

            await store.log_event(AuditEvent(
                event_type=EventType.ACTION_STARTED,
                batch_id="b", detail="first", timestamp=ts1,
            ))
            await store.log_event(AuditEvent(
                event_type=EventType.ACTION_STARTED,
                batch_id="b", detail="second", timestamp=ts3,
            ))
            await store.log_event(AuditEvent(
                event_type=EventType.ACTION_STARTED,
                batch_id="b", detail="third", timestamp=ts2,
            ))

            events = await store.get_events()
            assert events[0].detail == "second"  # Dec (most recent)
            assert events[1].detail == "third"   # Jun
            assert events[2].detail == "first"   # Jan

    async def test_empty_result(self) -> None:
        async with AuditStore(make_test_db()) as store:
            events = await store.get_events(batch_id="nonexistent")
            assert events == []


class TestAuditStoreCount:
    """Tests for counting audit events."""

    async def test_count_all(self) -> None:
        async with AuditStore(make_test_db()) as store:
            for _ in range(5):
                await store.log_event(_make_event())
            assert await store.count_events() == 5

    async def test_count_filtered(self) -> None:
        async with AuditStore(make_test_db()) as store:
            await store.log_event(_make_event(batch_id="A"))
            await store.log_event(_make_event(batch_id="B"))
            await store.log_event(_make_event(batch_id="A"))
            assert await store.count_events(batch_id="A") == 2

    async def test_count_empty(self) -> None:
        async with AuditStore(make_test_db()) as store:
            assert await store.count_events() == 0


# --- Resilience tests (Step 2) ---

class TestAuditStoreResilience:
    """log_event must retry on OperationalError and swallow persistent failures."""

    async def test_log_event_retries_on_operational_error(self) -> None:
        """First begin() raises OperationalError; second succeeds; no exception raised."""
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(make_test_db()) as store:
            real_begin = store._db.begin
            call_count = 0

            def patched_begin():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    @asynccontextmanager
                    async def _fail():
                        raise SAOperErr(None, None, Exception("locked"))
                        yield  # noqa: B901
                    return _fail()
                return real_begin()

            with patch.object(store._db, "begin", side_effect=patched_begin):
                await store.log_event(_make_event(), dry_run=True)

    async def test_log_event_swallows_persistent_error(self) -> None:
        """Both retry attempts raise OperationalError; best-effort mode swallows."""
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError as SAOperErr

        async with AuditStore(make_test_db()) as store:
            @asynccontextmanager
            async def _always_fail():
                raise SAOperErr(None, None, Exception("disk full"))
                yield  # noqa: B901

            with patch.object(store._db, "begin", side_effect=lambda: _always_fail()):
                await store.log_event(_make_event(), dry_run=True)

    async def test_log_event_swallows_generic_sqla_error(self) -> None:
        """Generic SQLAlchemyError is swallowed in dry_run/best-effort mode."""
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        async with AuditStore(make_test_db()) as store:
            @asynccontextmanager
            async def _always_fail():
                raise SQLAlchemyError("schema mismatch")
                yield  # noqa: B901

            with patch.object(store._db, "begin", side_effect=lambda: _always_fail()):
                await store.log_event(_make_event(), dry_run=True)
