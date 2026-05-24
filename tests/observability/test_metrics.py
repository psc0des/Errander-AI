"""Tests for Prometheus metrics and HTTP server."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import Counter, Histogram

from errander.models.actions import ActionResult, ActionStatus, ActionType
from errander.observability.metrics import (
    ACTIONS_TOTAL,
    LLM_REQUESTS_TOTAL,
    REGISTRY,
    SSH_ERRORS_TOTAL,
    _health_handler,
    _metrics_handler,
    start_metrics_server,
)
from errander.observability.tracking import (
    record_action_result,
    record_llm_outcome,
    record_ssh_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    action_type: ActionType = ActionType.DISK_CLEANUP,
    status: ActionStatus = ActionStatus.SUCCESS,
    vm_id: str = "vm-001",
    duration_seconds: float | None = None,
) -> ActionResult:
    started = datetime(2026, 4, 3, 10, 0, 0, tzinfo=UTC)
    completed = None
    if duration_seconds is not None:
        from datetime import timedelta
        completed = started + timedelta(seconds=duration_seconds)
    return ActionResult(
        action_type=action_type,
        status=status,
        vm_id=vm_id,
        started_at=started,
        completed_at=completed,
    )


def _sample_value(metric: Counter | Histogram, labels: dict[str, str]) -> float:
    """Read the current value of a labeled counter from the REGISTRY."""
    for m in REGISTRY.collect():
        if m.name == metric._name:  # type: ignore[attr-defined]
            for sample in m.samples:
                if (
                    all(sample.labels.get(k) == v for k, v in labels.items())
                    and (sample.name.endswith("_total") or not sample.name.endswith(("_sum", "_bucket", "_count")))
                ):
                        return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Metric registration
# ---------------------------------------------------------------------------

class TestMetricDefinitions:
    def test_all_metrics_registered(self) -> None:
        names = {m.name for m in REGISTRY.collect()}
        assert "errander_actions" in names
        assert "errander_action_duration_seconds" in names
        assert "errander_batch_duration_seconds" in names
        assert "errander_ssh_errors" in names
        assert "errander_llm_requests" in names
        assert "errander_approval_wait_seconds" in names
        assert "errander_vm_lock_held_seconds" in names

    def test_actions_total_has_correct_labels(self) -> None:
        # Ensure labels are correct (this would raise if mislabeled)
        ACTIONS_TOTAL.labels(
            action_type="disk_cleanup",
            status="success",
            vm_id="test-vm",
        )

    def test_ssh_errors_has_correct_labels(self) -> None:
        SSH_ERRORS_TOTAL.labels(vm_id="test-vm", reason="timeout")

    def test_llm_requests_has_correct_labels(self) -> None:
        LLM_REQUESTS_TOTAL.labels(outcome="success")


# ---------------------------------------------------------------------------
# record_action_result
# ---------------------------------------------------------------------------

class TestRecordActionResult:
    def test_increments_actions_total(self) -> None:
        result = _make_result(
            action_type=ActionType.DISK_CLEANUP,
            status=ActionStatus.SUCCESS,
            vm_id="vm-track-001",
        )
        before = _sample_value(
            ACTIONS_TOTAL,
            {"action_type": "disk_cleanup", "status": "success", "vm_id": "vm-track-001"},
        )
        record_action_result(result)
        after = _sample_value(
            ACTIONS_TOTAL,
            {"action_type": "disk_cleanup", "status": "success", "vm_id": "vm-track-001"},
        )
        assert after == before + 1

    def test_records_duration_when_completed_at_set(self) -> None:
        result = _make_result(
            action_type=ActionType.LOG_ROTATION,
            status=ActionStatus.SUCCESS,
            vm_id="vm-dur-001",
            duration_seconds=42.0,
        )
        # Just verify it doesn't raise — histogram values aren't easily sampled by label alone
        record_action_result(result)

    def test_skips_duration_when_no_completed_at(self) -> None:
        result = _make_result(
            action_type=ActionType.DOCKER_HYGIENE,
            status=ActionStatus.FAILED,
            vm_id="vm-nodur-001",
            duration_seconds=None,
        )
        # Should not raise — just increments counter
        record_action_result(result)

    def test_works_with_all_action_types(self) -> None:
        for action_type in ActionType:
            result = _make_result(
                action_type=action_type,
                status=ActionStatus.DRY_RUN_OK,
                vm_id="vm-all-types",
            )
            record_action_result(result)  # no raise

    def test_works_with_all_statuses(self) -> None:
        for status in ActionStatus:
            result = _make_result(
                action_type=ActionType.DISK_CLEANUP,
                status=status,
                vm_id="vm-all-statuses",
            )
            record_action_result(result)  # no raise


# ---------------------------------------------------------------------------
# record_ssh_error
# ---------------------------------------------------------------------------

class TestRecordSSHError:
    def test_increments_ssh_error_counter(self) -> None:
        before = _sample_value(
            SSH_ERRORS_TOTAL,
            {"vm_id": "vm-ssh-test", "reason": "connection_failed"},
        )
        record_ssh_error("vm-ssh-test", "connection_failed")
        after = _sample_value(
            SSH_ERRORS_TOTAL,
            {"vm_id": "vm-ssh-test", "reason": "connection_failed"},
        )
        assert after == before + 1

    def test_different_reasons_tracked_separately(self) -> None:
        record_ssh_error("vm-ssh-reasons", "timeout")
        record_ssh_error("vm-ssh-reasons", "auth_error")
        timeout_val = _sample_value(
            SSH_ERRORS_TOTAL,
            {"vm_id": "vm-ssh-reasons", "reason": "timeout"},
        )
        auth_val = _sample_value(
            SSH_ERRORS_TOTAL,
            {"vm_id": "vm-ssh-reasons", "reason": "auth_error"},
        )
        assert timeout_val >= 1
        assert auth_val >= 1


# ---------------------------------------------------------------------------
# record_llm_outcome
# ---------------------------------------------------------------------------

class TestRecordLLMOutcome:
    def test_increments_llm_counter(self) -> None:
        before = _sample_value(LLM_REQUESTS_TOTAL, {"outcome": "success"})
        record_llm_outcome("success")
        after = _sample_value(LLM_REQUESTS_TOTAL, {"outcome": "success"})
        assert after == before + 1

    def test_tracks_fallback_separately(self) -> None:
        before = _sample_value(LLM_REQUESTS_TOTAL, {"outcome": "fallback"})
        record_llm_outcome("fallback")
        after = _sample_value(LLM_REQUESTS_TOTAL, {"outcome": "fallback"})
        assert after == before + 1

    def test_all_outcomes_accepted(self) -> None:
        for outcome in ("success", "fallback", "timeout", "error"):
            record_llm_outcome(outcome)  # no raise


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

class TestMetricsHandler:
    @pytest.mark.asyncio
    async def test_returns_200(self) -> None:
        request = MagicMock()
        response = await _metrics_handler(request)
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_content_type_is_prometheus(self) -> None:
        request = MagicMock()
        response = await _metrics_handler(request)
        assert "text/plain" in response.content_type

    @pytest.mark.asyncio
    async def test_body_contains_metric_names(self) -> None:
        request = MagicMock()
        response = await _metrics_handler(request)
        body = response.body.decode()
        assert "errander_actions_total" in body


class TestHealthHandler:
    @pytest.mark.asyncio
    async def test_returns_200(self) -> None:
        request = MagicMock()
        response = await _health_handler(request)
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_body_contains_ok(self) -> None:
        request = MagicMock()
        response = await _health_handler(request)
        import json
        data = json.loads(response.body)
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# start_metrics_server
# ---------------------------------------------------------------------------

class TestStartMetricsServer:
    @pytest.mark.asyncio
    async def test_starts_and_returns_runner(self) -> None:
        """start_metrics_server should return an AppRunner without error."""
        with patch("errander.observability.metrics.web.AppRunner") as mock_runner_cls:
            mock_runner = AsyncMock()
            mock_runner.setup = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            with patch("errander.observability.metrics.web.TCPSite") as mock_site_cls:
                mock_site = AsyncMock()
                mock_site.start = AsyncMock()
                mock_site_cls.return_value = mock_site

                runner = await start_metrics_server(port=19090)

        mock_runner.setup.assert_called_once()
        mock_site.start.assert_called_once()
        assert runner is mock_runner
