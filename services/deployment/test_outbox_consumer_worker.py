"""
Unit tests for the deployment saga outbox consumer worker.

Acceptance (LOOP-AUTO-DEP-001):
- Deployment outbox events are consumed durably.
- Duplicate outbox events are idempotent (status=duplicate is not an error).
- Consumer exposes health: last success, last failure.
"""
from __future__ import annotations

import importlib
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Isolate the worker module from any live service
# ---------------------------------------------------------------------------

_WORKER_MODULE = "services.deployment.outbox_consumer_worker"


def _load_worker():
    if _WORKER_MODULE in sys.modules:
        return importlib.import_module(_WORKER_MODULE)
    return importlib.import_module(_WORKER_MODULE)


@pytest.fixture()
def worker():
    return _load_worker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outbox_record(event_id: str, sequence_no: int = 1) -> dict[str, Any]:
    return {
        "owner_service": "deployment",
        "event": {
            "event_id": event_id,
            "event_type": "deployment_plan_approved",
            "aggregate_type": "deployment_plan",
            "aggregate_id": "plan-001",
            "sequence_no": sequence_no,
            "causal_parent_id": None,
            "event_time": "2026-06-27T10:00:00Z",
            "emitted_at": "2026-06-27T10:00:00Z",
            "trace_id": f"trace-{event_id}",
            "idempotency_key": f"idem-{event_id}",
            "payload": {},
        },
        "status": "pending",
        "delivery_attempts": 0,
        "published_at": None,
        "last_error": None,
    }


def _inbox_receipt(event_id: str, *, status: str = "applied") -> dict[str, Any]:
    return {
        "consumer_name": "deployment-outbox-consumer",
        "event_id": event_id,
        "idempotency_key": f"idem-{event_id}",
        "aggregate_type": "deployment_plan",
        "aggregate_id": "plan-001",
        "sequence_no": 1,
        "trace_id": f"trace-{event_id}",
        "status": status,
        "processed_at": "2026-06-27T10:00:01Z",
        "notes": None,
    }


def _applied_receipt(sequence_no: int) -> dict[str, Any]:
    receipt = _inbox_receipt(f"evt-seq-{sequence_no}", status="applied")
    receipt["aggregate_id"] = "saga-001"
    receipt["sequence_no"] = sequence_no
    return receipt


def _completed_saga(saga: dict[str, Any]) -> dict[str, Any]:
    return {**saga, "status": "completed", "current_step": "runtime_active"}


def _success_projection(
    saga: dict[str, Any], binding: dict[str, Any]
) -> dict[str, Any]:
    return {
        "projection_contract": "DEP-003",
        "plan_id": saga["plan_id"],
        "artifact_id": saga["artifact_id"],
        "artifact_version": saga["artifact_version"],
        "capital_pool_id": saga["capital_pool_id"],
        "target_stage": saga["target_stage"],
        "actual_stage": saga["target_stage"],
        "plan_status": "executed",
        "runtime_binding_id": binding["binding_id"],
        "runtime_id": binding["runtime_id"],
        "runtime_status": "active",
        "deployment_saga_id": saga["saga_id"],
        "deployment_saga_status": "completed",
        "source_status": {
            "deployment_plan": "canonical",
            "runtime_binding": "canonical",
            "deployment_saga": "canonical",
        },
        "plan": {"binding_id": binding["binding_id"]},
    }


def _compensating_saga(command: str) -> dict[str, Any]:
    return {
        "saga_id": "saga-001",
        "plan_id": "plan-001",
        "approval_decision_id": "approval-001",
        "strategy_id": "strategy-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-001",
        "target_stage": "paper",
        "binding_id": None if command == "abort_plan" else "rb-001",
        "runtime_id": None if command == "abort_plan" else "rt-001",
        "trace_id": "trace-saga-001",
        "status": "compensating",
        "current_step": "compensation_requested",
        "compensation": {
            "command_type": command,
            "reason": "test compensation",
        },
    }


def _runtime_binding(**overrides: Any) -> dict[str, Any]:
    binding = {
        "binding_id": "rb-001",
        "plan_id": "plan-001",
        "runtime_id": "rt-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "deployment_mode": "paper",
        "execution_mode": "paper",
        "effective_at": "2026-07-14T08:00:00Z",
        "status": "active",
        "metadata": {"allowed_deployment_scope": "paper"},
    }
    binding.update(overrides)
    return binding


def _compensation_projection(
    saga: dict[str, Any], *, plan_status: str
) -> dict[str, Any]:
    return {
        "projection_contract": "DEP-003",
        "plan_id": saga["plan_id"],
        "artifact_id": saga["artifact_id"],
        "artifact_version": saga["artifact_version"],
        "capital_pool_id": saga["capital_pool_id"],
        "plan_status": plan_status,
        "deployment_saga_id": saga["saga_id"],
        "deployment_saga_status": saga["status"],
        "source_status": {
            "deployment_plan": "canonical",
            "deployment_saga": "canonical",
        },
    }


# ---------------------------------------------------------------------------
# fetch_pending_outbox
# ---------------------------------------------------------------------------


class TestFetchPendingOutbox:
    def test_returns_empty_when_no_pending(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            result = worker.fetch_pending_outbox(api_url="http://localhost:8095")

        assert result == []

    def test_returns_pending_records(self, worker):
        records = [_outbox_record("evt-001"), _outbox_record("evt-002")]
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(records).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.fetch_pending_outbox(api_url="http://localhost:8095")

        assert len(result) == 2
        assert result[0]["event"]["event_id"] == "evt-001"

    def test_url_includes_pending_filter(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            worker.fetch_pending_outbox(api_url="http://localhost:8095")

        called_url = mock_urlopen.call_args[0][0].full_url
        assert "status=pending" in called_url


# ---------------------------------------------------------------------------
# consume_event
# ---------------------------------------------------------------------------


class TestConsumeEvent:
    def test_returns_receipt_on_applied(self, worker):
        receipt = _inbox_receipt("evt-001", status="applied")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(receipt).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="deployment-outbox-consumer",
            )

        assert result["status"] == "applied"
        assert result["event_id"] == "evt-001"

    def test_duplicate_receipt_returned_without_error(self, worker):
        receipt = _inbox_receipt("evt-001", status="duplicate")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(receipt).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="deployment-outbox-consumer",
            )

        assert result["status"] == "duplicate"

    def test_posts_to_correct_url(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(_inbox_receipt("evt-42")).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-42",
                consumer_name="test-consumer",
            )

        called_url = mock_urlopen.call_args[0][0].full_url
        assert "/api/deployment/outbox/evt-42/consume" in called_url


# ---------------------------------------------------------------------------
# run_poll — idempotency and error handling
# ---------------------------------------------------------------------------


class TestRunPoll:
    def test_no_events_returns_zero_consumed(self, worker):
        with patch.object(worker, "fetch_pending_outbox", return_value=[]):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result == {
            "events_found": 0,
            "consumed": 0,
            "duplicates": 0,
            "skipped_not_due": 0,
            "retry_scheduled": 0,
            "dead_lettered": 0,
            "errors": [],
        }

    def test_new_event_counts_as_consumed(self, worker):
        records = [_outbox_record("evt-001")]
        receipt = _inbox_receipt("evt-001", status="applied")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 1
        assert result["duplicates"] == 0
        assert result["errors"] == []

    def test_duplicate_event_counts_as_duplicate_not_error(self, worker):
        records = [_outbox_record("evt-001")]
        receipt = _inbox_receipt("evt-001", status="duplicate")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 0
        assert result["duplicates"] == 1
        assert result["errors"] == []

    def test_out_of_order_event_counts_as_error_not_consumed(self, worker):
        records = [_outbox_record("evt-002", sequence_no=2)]
        receipt = _inbox_receipt("evt-002", status="out_of_order")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 0
        assert result["duplicates"] == 0
        assert "out_of_order" in result["errors"][0]

    def test_multiple_events_consumed_independently(self, worker):
        records = [_outbox_record("evt-001"), _outbox_record("evt-002"), _outbox_record("evt-003")]
        receipts = {
            "evt-001": _inbox_receipt("evt-001", status="applied"),
            "evt-002": _inbox_receipt("evt-002", status="duplicate"),
            "evt-003": _inbox_receipt("evt-003", status="applied"),
        }

        def _consume(*, api_url, event_id, consumer_name, timeout_seconds=10.0):
            return receipts[event_id]

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 3
        assert result["consumed"] == 2
        assert result["duplicates"] == 1
        assert result["errors"] == []

    def test_http_error_is_recorded_not_raised(self, worker):
        records = [_outbox_record("evt-err")]

        def _consume(**_kwargs):
            raise urllib.error.HTTPError(
                url="http://x", code=500, msg="Internal Server Error", hdrs=None, fp=None
            )

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["errors"] != []
        assert "evt-err" in result["errors"][0]

    def test_failed_delivery_can_be_persisted_for_retry(self, worker):
        records = [_outbox_record("evt-retry")]

        def _consume(**_kwargs):
            raise RuntimeError("network timeout")

        failure_record = _outbox_record("evt-retry")
        failure_record["delivery_attempts"] = 1
        failure_record["last_error"] = "event_id=evt-retry error=network timeout"
        failure_record["status"] = "pending"

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
            patch.object(worker, "record_delivery_failure", return_value=failure_record) as mock_failure,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
                max_attempts=3,
                retry_delay_seconds=5,
            )

        assert result["retry_scheduled"] == 1
        assert result["dead_lettered"] == 0
        mock_failure.assert_called_once()
        assert mock_failure.call_args.kwargs["max_attempts"] == 3
        assert mock_failure.call_args.kwargs["retry_delay_seconds"] == 5

    def test_failed_delivery_counts_dead_lettered_record(self, worker):
        records = [_outbox_record("evt-dlq")]

        def _consume(**_kwargs):
            raise RuntimeError("terminal payload error")

        failure_record = _outbox_record("evt-dlq")
        failure_record["delivery_attempts"] = 3
        failure_record["last_error"] = "event_id=evt-dlq error=terminal payload error"
        failure_record["status"] = "dead_lettered"

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
            patch.object(worker, "record_delivery_failure", return_value=failure_record),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
                max_attempts=3,
                retry_delay_seconds=5,
            )

        assert result["retry_scheduled"] == 0
        assert result["dead_lettered"] == 1

    def test_pending_event_with_future_retry_is_skipped(self, worker):
        record = _outbox_record("evt-wait")
        record["next_retry_at"] = "2999-01-01T00:00:00Z"
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "consume_event") as mock_consume,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["skipped_not_due"] == 1
        mock_consume.assert_not_called()

    def test_missing_event_id_recorded_as_error(self, worker):
        bad_record = {"event": {}, "status": "pending"}
        with patch.object(worker, "fetch_pending_outbox", return_value=[bad_record]):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["errors"] != []
        assert result["consumed"] == 0

    def test_binding_requested_success(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = {
            "plan_id": "plan-001",
            "saga_id": "saga-001",
            "status": "awaiting_binding",
        }
        mock_plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "status": "approved",
            "metadata": {"loader_checks_passed": True},
        }
        mock_compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        mock_result = MagicMock()
        mock_result.succeeded.return_value = True
        mock_result.binding_id = "rb-001"
        mock_result.binding = {"runtime_id": "rt-001"}

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga) as mock_fetch_saga,
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan) as mock_fetch_plan,
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat) as mock_compat_check,
            patch("services.deployment.outbox_consumer_worker.dispatch_to_runtime_manager", return_value=mock_result) as mock_dispatch,
            patch("services.deployment.outbox_consumer_worker.record_binding_created") as mock_record_binding,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-001", status="applied")),
            patch("services.deployment.outbox_consumer_worker.RuntimeManagerClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.list_by_plan.return_value = []

            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["consumed"] == 1
            assert result["errors"] == []
            mock_fetch_saga.assert_called_once_with(api_url="http://localhost:8095", saga_id="saga-001", timeout_seconds=10.0)
            mock_fetch_plan.assert_called_once_with(api_url="http://localhost:8095", plan_id="plan-001", timeout_seconds=10.0)
            mock_compat_check.assert_called_once_with(
                api_url="http://localhost:8095",
                capital_pool_id="pool-001",
                sponsor_persona_id="persona-001",
                target_stage="paper",
                timeout_seconds=10.0,
            )
            mock_record_binding.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                binding_id="rb-001",
                runtime_id="rt-001",
                note="binding created/verified via deployment outbox consumer dispatch",
                timeout_seconds=10.0,
            )

    def test_binding_requested_compatibility_failed(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = {"plan_id": "plan-001"}
        mock_plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "metadata": {"loader_checks_passed": True},
        }
        mock_compat = {
            "ok": False,
            "errors": ["missing capital binding"],
        }

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga),
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan),
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat),
            patch("services.deployment.outbox_consumer_worker.record_saga_failure") as mock_saga_fail,
            patch("services.deployment.outbox_consumer_worker._record_failure_best_effort", return_value=({"status": "dead_lettered"}, None)) as mock_outbox_fail,
        ):
            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["dead_lettered"] == 1
            assert "Compatibility check failed" in result["errors"][0]
            mock_saga_fail.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                reason="Compatibility check failed: missing capital binding",
                failed_step="binding_requested",
                timeout_seconds=10.0,
            )
            mock_outbox_fail.assert_called_once_with(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="test-consumer",
                reason="Compatibility check failed: missing capital binding",
                retryable=False,
                max_attempts=3,
                retry_delay_seconds=30,
                timeout_seconds=10.0,
            )

    def test_binding_requested_transient_error(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = {"plan_id": "plan-001"}
        mock_plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "metadata": {"loader_checks_passed": True},
        }
        mock_compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        mock_result = MagicMock()
        mock_result.succeeded.return_value = False
        mock_result.is_retryable.return_value = True
        mock_result.error_message = "temporary 503"

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga),
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan),
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat),
            patch("services.deployment.outbox_consumer_worker.dispatch_to_runtime_manager", return_value=mock_result),
            patch("services.deployment.outbox_consumer_worker._record_failure_best_effort", return_value=({"status": "pending"}, None)) as mock_outbox_fail,
            patch("services.deployment.outbox_consumer_worker.RuntimeManagerClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.list_by_plan.return_value = []

            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["retry_scheduled"] == 1
            mock_outbox_fail.assert_called_once_with(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="test-consumer",
                reason="transient dispatch failure: temporary 503",
                retryable=True,
                max_attempts=3,
                retry_delay_seconds=30,
                timeout_seconds=10.0,
            )

    def test_binding_requested_terminal_error(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = {"plan_id": "plan-001"}
        mock_plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "metadata": {"loader_checks_passed": True},
        }
        mock_compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        mock_result = MagicMock()
        mock_result.succeeded.return_value = False
        mock_result.is_retryable.return_value = False
        mock_result.error_message = "invalid signature"

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga),
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan),
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat),
            patch("services.deployment.outbox_consumer_worker.dispatch_to_runtime_manager", return_value=mock_result),
            patch("services.deployment.outbox_consumer_worker.record_saga_failure") as mock_saga_fail,
            patch("services.deployment.outbox_consumer_worker._record_failure_best_effort", return_value=({"status": "dead_lettered"}, None)) as mock_outbox_fail,
            patch("services.deployment.outbox_consumer_worker.RuntimeManagerClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.list_by_plan.return_value = []

            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["dead_lettered"] == 1
            mock_saga_fail.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                reason="terminal dispatch failure: invalid signature",
                failed_step="binding_requested",
                timeout_seconds=10.0,
            )
            mock_outbox_fail.assert_called_once_with(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="test-consumer",
                reason="terminal dispatch failure: invalid signature",
                retryable=False,
                max_attempts=3,
                retry_delay_seconds=30,
                timeout_seconds=10.0,
            )

    def test_binding_requested_downstream_success_before_receipt(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = {
            "plan_id": "plan-001",
            "saga_id": "saga-001",
            "status": "awaiting_binding",
        }
        mock_plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "metadata": {"loader_checks_passed": True},
        }
        mock_compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        mock_result = MagicMock()
        mock_result.succeeded.return_value = True
        mock_result.binding_id = "rb-existing"
        mock_result.binding = {"runtime_id": "rt-001"}

        mock_existing_binding = {
            "binding_id": "rb-existing",
            "plan_id": "plan-001",
        }

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga),
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan),
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat),
            patch("services.deployment.outbox_consumer_worker.dispatch_to_runtime_manager", return_value=mock_result) as mock_dispatch,
            patch("services.deployment.outbox_consumer_worker.record_binding_created") as mock_record_binding,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-001", status="applied")),
            patch("services.deployment.outbox_consumer_worker.RuntimeManagerClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.list_by_plan.return_value = [mock_existing_binding]

            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["consumed"] == 1
            mock_dispatch.assert_called_once()
            dispatched_saga = mock_dispatch.call_args.kwargs["saga"]
            assert dispatched_saga["binding_id"] == "rb-existing"
            mock_record_binding.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                binding_id="rb-existing",
                runtime_id="rt-001",
                note="binding created/verified via deployment outbox consumer dispatch",
                timeout_seconds=10.0,
            )

    def test_binding_requested_replay_after_saga_advance_does_not_repeat_state_write(self, worker):
        record = _outbox_record("evt-replay")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "binding_id": "rb-existing",
            "status": "awaiting_runtime_load",
        }
        plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "status": "executing",
            "metadata": {"loader_checks_passed": True},
        }
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        dispatch_result = MagicMock()
        dispatch_result.succeeded.return_value = True
        dispatch_result.binding_id = "rb-existing"
        dispatch_result.binding = {"runtime_id": "rt-001"}

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(worker, "dispatch_to_runtime_manager", return_value=dispatch_result),
            patch.object(worker, "record_binding_created") as mock_record_binding,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-replay")),
            patch.object(worker, "RuntimeManagerClient"),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        mock_record_binding.assert_not_called()

    def test_binding_recovery_read_failure_never_blindly_redeploys(self, worker):
        record = _outbox_record("evt-recovery-fail")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "status": "awaiting_binding",
        }
        plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "status": "approved",
            "metadata": {"loader_checks_passed": True},
        }
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(worker, "dispatch_to_runtime_manager") as mock_dispatch,
            patch.object(
                worker,
                "_record_failure_best_effort",
                return_value=({"status": "pending"}, None),
            ),
            patch.object(worker, "RuntimeManagerClient") as client_cls,
        ):
            client_cls.return_value.list_by_plan.side_effect = RuntimeError("read timeout")
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["retry_scheduled"] == 1
        assert "BINDING_RECOVERY_READ_FAILED" not in " ".join(result["errors"])
        assert "authoritative pre-dispatch" in " ".join(result["errors"])
        mock_dispatch.assert_not_called()

    def test_runtime_load_requires_active_authoritative_readback(self, worker):
        record = _outbox_record("evt-load", sequence_no=2)
        record["event"].update(
            {
                "event_type": "runtime.load.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-001", "runtime_id": "rt-001"},
            }
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "binding_id": "rb-001",
            "status": "awaiting_runtime_load",
        }
        binding = {
            "binding_id": "rb-001",
            "plan_id": "plan-001",
            "runtime_id": "rt-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "deployment_mode": "paper",
            "execution_mode": "paper",
            "status": "active",
        }
        fleet = {
            "cycle_count": 2,
            "last_reconcile_at": "2026-07-14T08:00:00Z",
            "last_error": None,
            "workers": [
                {
                    "binding_id": "rb-001",
                    "runtime_id": "rt-001",
                    "capital_pool_id": "pool-001",
                    "status": "running",
                    "pid": 4242,
                }
            ],
        }

        with (
            patch.dict(
                worker.os.environ,
                {"PANTHEON_PAPER_FLEET_RECONCILER_URL": "http://fleet:8011"},
            ),
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(
                worker, "fetch_saga", side_effect=[saga, _completed_saga(saga)]
            ),
            patch.object(worker, "fetch_paper_fleet_state", return_value=fleet),
            patch.object(
                worker,
                "fetch_projection",
                return_value=_success_projection(saga, binding),
            ),
            patch.object(worker, "record_runtime_active") as mock_active,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-load")),
            patch.object(worker, "RuntimeManagerClient") as client_cls,
        ):
            client_cls.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        mock_active.assert_called_once_with(
            api_url="http://localhost:8095",
            saga_id="saga-001",
            binding_id="rb-001",
            runtime_id="rt-001",
            note="runtime active confirmed by authoritative RuntimeBinding readback",
            timeout_seconds=10.0,
        )

    def test_runtime_load_waits_for_matching_running_paper_worker(self, worker):
        record = _outbox_record("evt-load-wait", sequence_no=2)
        record["event"].update(
            {
                "event_type": "runtime.load.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-001"},
            }
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "binding_id": "rb-001",
            "status": "awaiting_runtime_load",
        }
        binding = {
            "binding_id": "rb-001",
            "plan_id": "plan-001",
            "runtime_id": "rt-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "deployment_mode": "paper",
            "execution_mode": "paper",
            "status": "active",
        }
        fleet = {
            "cycle_count": 3,
            "last_reconcile_at": "2026-07-14T08:00:00Z",
            "last_error": None,
            "workers": [],
        }

        with (
            patch.dict(
                worker.os.environ,
                {"PANTHEON_PAPER_FLEET_RECONCILER_URL": "http://fleet:8011"},
            ),
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_paper_fleet_state", return_value=fleet),
            patch.object(
                worker,
                "_record_failure_best_effort",
                return_value=({"status": "pending"}, None),
            ),
            patch.object(worker, "record_runtime_active") as mock_active,
            patch.object(worker, "RuntimeManagerClient") as client_cls,
        ):
            client_cls.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
            )

        assert result["retry_scheduled"] == 1
        assert "paper fleet post-state is not running yet" in " ".join(result["errors"])
        mock_active.assert_not_called()

    def test_runtime_load_paused_by_kill_switch_fails_closed(self, worker):
        record = _outbox_record("evt-load-killed", sequence_no=2)
        record["event"].update(
            {
                "event_type": "runtime.load.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-001"},
            }
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "binding_id": "rb-001",
            "status": "awaiting_runtime_load",
        }
        binding = {
            "binding_id": "rb-001",
            "plan_id": "plan-001",
            "runtime_id": "rt-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "deployment_mode": "paper",
            "execution_mode": "paper",
            "status": "paused",
        }

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "record_saga_failure") as mock_saga_failure,
            patch.object(
                worker,
                "_record_failure_best_effort",
                return_value=({"status": "dead_lettered"}, None),
            ),
            patch.object(worker, "record_runtime_active") as mock_active,
            patch.object(worker, "consume_event") as mock_consume,
            patch.object(worker, "RuntimeManagerClient") as client_cls,
        ):
            client_cls.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["dead_lettered"] == 1
        assert "status expected 'active'" in " ".join(result["errors"])
        mock_saga_failure.assert_called_once()
        mock_active.assert_not_called()
        mock_consume.assert_not_called()

    @pytest.mark.parametrize(
        ("plan", "expected"),
        [
            ({"metadata": {"loader_checks_passed": True}}, True),
            ({"loader_checks_passed": True}, True),
            ({"metadata": {"loader_checks_passed": False}}, False),
            ({"metadata": {"loader_checks_passed": "true"}}, False),
            ({"metadata": {}}, False),
            ({"metadata": None}, False),
            (
                {
                    "loader_checks_passed": False,
                    "metadata": {"loader_checks_passed": True},
                },
                False,
            ),
        ],
    )
    def test_loader_attestation_accepts_only_literal_uncontradicted_true(
        self, worker, plan, expected
    ):
        assert worker.loader_checks_attested(plan) is expected

    def test_missing_loader_attestation_fails_before_runtime_client(self, worker):
        record = _outbox_record("evt-loader-missing")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        saga = {
            "saga_id": "saga-001",
            "plan_id": "plan-001",
            "status": "awaiting_binding",
        }
        plan = {
            "plan_id": "plan-001",
            "sponsor_persona_id": "persona-001",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
            "status": "approved",
            "metadata": {},
        }
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(worker, "record_saga_failure") as saga_failure,
            patch.object(
                worker,
                "_record_failure_best_effort",
                return_value=({"status": "dead_lettered"}, None),
            ),
            patch.object(worker, "dispatch_to_runtime_manager") as dispatch,
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["dead_lettered"] == 1
        assert "not authoritatively attested" in " ".join(result["errors"])
        saga_failure.assert_called_once()
        client_class.assert_not_called()
        dispatch.assert_not_called()

    def test_sequence_block_never_mutates_or_burns_retry_budget(self, worker):
        record = _outbox_record("evt-comp-blocked", sequence_no=3)
        record["event"].update(
            {
                "event_type": "deployment.compensation.requested",
                "aggregate_id": "saga-001",
            }
        )
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(worker, "execute_compensation") as execute,
            patch.object(worker, "record_delivery_failure") as record_failure,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
            )

        assert result["skipped_not_due"] == 1
        assert "sequence_blocked" in " ".join(result["errors"])
        execute.assert_not_called()
        record_failure.assert_not_called()

    def test_compensation_branch_finalizes_before_consume(self, worker):
        record = _outbox_record("evt-comp", sequence_no=3)
        record["event"].update(
            {
                "event_type": "deployment.compensation.requested",
                "aggregate_id": "saga-001",
            }
        )
        saga = _compensating_saga("mark_binding_failed_inactive")
        terminal = {**saga, "status": "failed", "current_step": "compensated"}
        plan = {"plan_id": "plan-001", "status": "executing"}
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1), _applied_receipt(2)],
            ),
            patch.object(worker, "fetch_saga", side_effect=[saga, terminal]),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(
                worker,
                "execute_compensation",
                return_value=("failed", "executing"),
            ) as execute,
            patch.object(
                worker,
                "fetch_projection",
                return_value=_compensation_projection(
                    terminal, plan_status="executing"
                ),
            ),
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-comp"),
            ) as consume,
            patch.object(worker, "RuntimeManagerClient"),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        assert execute.call_count == 1
        assert consume.call_count == 1

    def test_terminal_compensation_replay_skips_runtime_mutation(self, worker):
        record = _outbox_record("evt-comp-replay", sequence_no=3)
        record["event"].update(
            {
                "event_type": "deployment.compensation.requested",
                "aggregate_id": "saga-001",
            }
        )
        saga = {
            **_compensating_saga("mark_binding_failed_inactive"),
            "status": "failed",
            "current_step": "compensated",
        }
        plan = {"plan_id": "plan-001", "status": "executing"}
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1), _applied_receipt(2)],
            ),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(
                worker,
                "fetch_projection",
                return_value=_compensation_projection(saga, plan_status="executing"),
            ),
            patch.object(worker, "execute_compensation") as execute,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-comp-replay"),
            ),
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        execute.assert_not_called()
        client_class.assert_not_called()


class TestExecuteCompensation:
    def _event(self, command: str) -> dict[str, Any]:
        return {
            "event_id": f"evt-{command}",
            "event_type": "deployment.compensation.requested",
            "aggregate_id": "saga-001",
            "sequence_no": 3,
            "idempotency_key": f"idem-{command}",
            "payload": {"compensation": {"command_type": command}},
        }

    def test_abort_plan_proves_no_binding_and_updates_only_plan(self, worker):
        saga = _compensating_saga("abort_plan")
        client = MagicMock()
        client.list_by_plan.side_effect = [[], []]
        with (
            patch.object(worker, "update_plan_status") as update,
            patch.object(
                worker,
                "fetch_plan",
                return_value={"plan_id": "plan-001", "status": "aborted"},
            ),
            patch.object(worker, "finalize_compensation") as finalize,
        ):
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan={"plan_id": "plan-001", "status": "approved"},
                event=self._event("abort_plan"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("aborted", "aborted")
        update.assert_called_once_with(
            api_url="http://deployment:8095",
            plan_id="plan-001",
            status="aborted",
            timeout_seconds=10.0,
        )
        finalize.assert_called_once()
        client.transition.assert_not_called()

    def test_mark_binding_failed_is_idempotent_and_plan_immutable(self, worker):
        saga = _compensating_saga("mark_binding_failed_inactive")
        active = _runtime_binding()
        failed = _runtime_binding(status="failed")
        client = MagicMock()
        client.get.side_effect = [active, failed]
        with patch.object(worker, "finalize_compensation") as finalize:
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan={"plan_id": "plan-001", "status": "executing"},
                event=self._event("mark_binding_failed_inactive"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("failed", "executing")
        client.transition.assert_called_once_with("rb-001", "failed")
        finalize.assert_called_once()

    def test_rollback_recovers_existing_child_without_second_post(self, worker):
        saga = _compensating_saga("request_rollback")
        old = _runtime_binding()
        retired = _runtime_binding(status="retired")
        child = _runtime_binding(
            binding_id="rb-fallback",
            plan_id="plan-fallback",
            runtime_id="rt-fallback",
            artifact_id="artifact-fallback",
            artifact_version="v0.9.0",
            rollback_parent="rb-001",
            rollback_action_type="replace",
        )
        client = MagicMock()
        client.get.side_effect = [old, retired, child]
        client.list_by_pool.return_value = [old, child]
        client.get_active_for_pool.return_value = child
        plan = {
            "plan_id": "plan-001",
            "status": "executed",
            "rollback": {
                "target_artifact_id": "artifact-fallback",
                "target_version": "v0.9.0",
                "action_type": "replace",
            },
        }
        with patch.object(worker, "finalize_compensation") as finalize:
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan=plan,
                event=self._event("request_rollback"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("failed", "executed")
        client.rollback.assert_not_called()
        finalize.assert_called_once()

    def test_rollback_requires_attestation_then_reads_exact_post_state(self, worker):
        saga = _compensating_saga("request_rollback")
        old = _runtime_binding()
        retired = _runtime_binding(status="retired")
        prior = _runtime_binding(
            binding_id="rb-prior",
            plan_id="plan-fallback",
            runtime_id="rt-prior",
            artifact_id="artifact-fallback",
            artifact_version="v0.9.0",
            status="retired",
            effective_at="2026-07-13T08:00:00Z",
        )
        child = _runtime_binding(
            binding_id="rb-fallback",
            plan_id="plan-fallback",
            runtime_id="rt-fallback",
            artifact_id="artifact-fallback",
            artifact_version="v0.9.0",
            rollback_parent="rb-001",
            rollback_action_type="replace",
        )
        client = MagicMock()
        client.get.side_effect = [old, retired, child]
        client.list_by_pool.return_value = [old, prior]
        client.rollback.return_value = {"new_binding": child}
        client.get_active_for_pool.return_value = child
        plan = {
            "plan_id": "plan-001",
            "status": "executed",
            "rollback": {
                "target_artifact_id": "artifact-fallback",
                "target_version": "v0.9.0",
                "action_type": "replace",
            },
            "metadata": {
                "rollback_loader_attestation": {
                    "artifact_id": "artifact-fallback",
                    "artifact_version": "v0.9.0",
                    "passed": True,
                    "proof_ref": "object-store://loader/proof-fallback",
                }
            },
        }
        with (
            patch.object(
                worker,
                "fetch_plan",
                return_value={
                    "plan_id": "plan-fallback",
                    "artifact_id": "artifact-fallback",
                    "artifact_version": "v0.9.0",
                },
            ),
            patch.object(worker, "finalize_compensation") as finalize,
        ):
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan=plan,
                event=self._event("request_rollback"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("failed", "executed")
        request = client.rollback.call_args.args[0]
        assert request["loader_checks_passed"] is True
        assert request["replacement_plan_id"] == "plan-fallback"
        assert request["replacement_metadata"]["compensation_event_id"] == "evt-request_rollback"
        finalize.assert_called_once()

    def test_safe_mode_requires_ack_readback_and_exact_incident(self, worker):
        saga = _compensating_saga("enter_safe_mode_and_raise_incident")
        active = _runtime_binding()
        paused = _runtime_binding(status="paused")
        client = MagicMock()
        client.get.side_effect = [active, paused]
        client.execute_kill_switch.return_value = {
            "telemetry_ack": {"ack_status": "acknowledged"}
        }
        client.get_safe_mode.return_value = {"safe_mode_state": "paused"}

        def _return_payload(**kwargs):
            return dict(kwargs["payload"])

        with (
            patch.object(worker, "create_incident", side_effect=_return_payload),
            patch.object(worker, "finalize_compensation") as finalize,
        ):
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan={"plan_id": "plan-001", "status": "executed"},
                event=self._event("enter_safe_mode_and_raise_incident"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("failed", "executed")
        kill = client.execute_kill_switch.call_args.args[0]
        assert kill["action_override"] == "pause"
        assert kill["idempotency_key"] == "idem-enter_safe_mode_and_raise_incident"
        finalize.assert_called_once()

    def test_kill_safe_mode_wins_over_rollback_compensation(self, worker):
        saga = _compensating_saga("request_rollback")
        active = _runtime_binding()
        paused = _runtime_binding(status="paused")
        client = MagicMock()
        client.get.side_effect = [active, paused]
        client.get_safe_mode.side_effect = [
            {"safe_mode_state": "paused"},
            {"safe_mode_state": "paused"},
        ]
        client.execute_kill_switch.return_value = {
            "telemetry_ack": {"ack_status": "acknowledged"}
        }
        plan = {
            "plan_id": "plan-001",
            "status": "executed",
            "rollback": {
                "target_artifact_id": "artifact-fallback",
                "target_version": "v0.9.0",
                "action_type": "replace",
            },
        }

        def _return_payload(**kwargs):
            return dict(kwargs["payload"])

        with (
            patch.object(worker, "create_incident", side_effect=_return_payload),
            patch.object(worker, "finalize_compensation"),
        ):
            result = worker.execute_compensation(
                api_url="http://deployment:8095",
                saga=saga,
                plan=plan,
                event=self._event("request_rollback"),
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert result == ("failed", "executed")
        client.rollback.assert_not_called()
        client.list_by_pool.assert_not_called()


# ---------------------------------------------------------------------------
# health file
# ---------------------------------------------------------------------------


class TestWriteHealth:
    def test_writes_json_to_path(self, worker, tmp_path):
        health_path = str(tmp_path / "consumer_health.json")
        state = {"status": "ok", "last_success": "2026-06-27T10:00:00Z", "ticks": 1}
        worker._write_health(health_path, state)
        content = json.loads(Path(health_path).read_text("utf-8"))
        assert content["status"] == "ok"
        assert content["ticks"] == 1

    def test_silently_ignores_write_failure(self, worker):
        worker._write_health("/nonexistent/path/health.json", {"status": "ok"})


# ---------------------------------------------------------------------------
# main loop — max_ticks integration
# ---------------------------------------------------------------------------


class TestMain:
    def test_max_ticks_terminates_cleanly(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[]),
            patch("time.sleep"),
        ):
            exit_code = worker.main()

        assert exit_code == 0
        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().splitlines() if line]
        assert len(lines) == 2
        last = json.loads(lines[-1])
        assert last["tick"] == 2

    def test_health_reflects_last_success(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        records = [_outbox_record("evt-main-001")]
        receipt = _inbox_receipt("evt-main-001", status="applied")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
            patch("time.sleep"),
        ):
            worker.main()

        captured = capsys.readouterr().out
        line = json.loads(captured.strip())
        assert line["health"]["total_consumed"] == 1
        assert line["health"]["last_success"] is not None
        assert line["health"]["last_failure"] is None

    def test_health_reflects_last_failure_on_error(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        records = [_outbox_record("evt-fail-001")]

        def _failing_consume(**_kwargs):
            raise RuntimeError("network timeout")

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_failing_consume),
            patch.object(worker, "record_delivery_failure", return_value={"status": "pending"}),
            patch("time.sleep"),
        ):
            worker.main()

        captured = capsys.readouterr().out
        line = json.loads(captured.strip())
        assert line["health"]["status"] == "degraded"
        assert line["health"]["last_failure"] is not None
        assert line["health"]["last_failure_reason"] is not None

    def test_health_file_written_each_tick(self, worker, monkeypatch, tmp_path, capsys):
        health_file = str(tmp_path / "health.json")
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", health_file)

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[]),
            patch("time.sleep"),
        ):
            worker.main()

        content = json.loads(Path(health_file).read_text("utf-8"))
        assert content["ticks"] == 2
        assert content["status"] == "ok"
