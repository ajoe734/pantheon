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
import os
import sys
import time
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
def worker(monkeypatch):
    module = _load_worker()
    monkeypatch.setenv(
        "PANTHEON_DEPLOYMENT_SERVICE_TOKEN",
        "deployment-consumer-test:service,deployment_consumer",
    )
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-deployment-test")
    module._CLAIM_TOKENS.clear()
    with patch.object(
        module,
        "verify_deploy_authorities",
        side_effect=lambda request, **_kwargs: _authority_report(request),
    ):
        yield module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _authority_report(
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": "plan-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "strategy_id": "strategy-001",
        "approval_decision_id": "approval-001",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }
    if request:
        for field in (
            "plan_id",
            "plan_status",
            "target_stage",
            "artifact_id",
            "artifact_version",
            "strategy_id",
            "approval_decision_id",
            "sponsor_persona_id",
            "capital_pool_id",
            "persona_capital_binding_id",
            "persona_capital_binding_status",
            "allowed_deployment_scope",
        ):
            if request.get(field) is not None:
                report[field] = request[field]
    return report


def _binding_saga(**overrides: Any) -> dict[str, Any]:
    saga = {
        "saga_id": "saga-001",
        "plan_id": "plan-001",
        "approval_decision_id": "approval-001",
        "strategy_id": "strategy-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-001",
        "target_stage": "paper",
        "status": "awaiting_binding",
        "metadata": {
            "tenant_id": "tenant-deployment-test",
            "foundation": {
                "trace_context": {
                    "trace_id": "trace-deployment-test",
                    "correlation_id": "correlation-deployment-test",
                }
            },
        },
    }
    saga.update(overrides)
    return saga


def _binding_plan(**overrides: Any) -> dict[str, Any]:
    plan = {
        "plan_id": "plan-001",
        "approval_decision_id": "approval-001",
        "strategy_id": "strategy-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "sponsor_persona_id": "persona-001",
        "capital_pool_id": "pool-001",
        "target_stage": "paper",
        "status": "approved",
        # A forged legacy assertion is intentionally ignored by production
        # code; keeping it here guards against accidental trust regression.
        "metadata": {
            "loader_checks_passed": True,
            "tenant_id": "tenant-deployment-test",
        },
    }
    plan.update(overrides)
    return plan


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
        "metadata": {
            "allowed_deployment_scope": "paper",
            "strategy_id": "strategy-001",
            "authoritative_loader_attestation": _authority_report(),
        },
    }
    binding.update(overrides)
    return binding


def _kill_ack(binding_id: str = "rb-001") -> dict[str, Any]:
    return {
        "ack_status": "acknowledged",
        "capital_pool_id": "pool-001",
        "safe_mode_after": "paused",
        "runtime_status_after": "paused",
        "runtime_binding_id": binding_id,
    }


def _fallback_plan() -> dict[str, Any]:
    return {
        "plan_id": "plan-fallback",
        "status": "executed",
        "target_stage": "paper",
        "artifact_id": "artifact-fallback",
        "artifact_version": "v0.9.0",
        "strategy_id": "strategy-001",
        "approval_decision_id": "approval-001",
        "sponsor_persona_id": "persona-001",
        "capital_pool_id": "pool-001",
    }


def _fallback_authority_report(
    *, allowed_deployment_scope: str = "paper"
) -> dict[str, Any]:
    return _authority_report(
        {
            **_fallback_plan(),
            "plan_status": "executed",
            "persona_capital_binding_id": "pcb-001",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": allowed_deployment_scope,
        }
    )


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

    def test_posts_transactional_claim(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            worker.fetch_pending_outbox(api_url="http://localhost:8095")

        request = mock_urlopen.call_args[0][0]
        assert request.full_url.endswith("/api/deployment/outbox/claim")
        assert request.method == "POST"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert request.headers["X-tenant-id"] == "tenant-deployment-test"
        assert json.loads(request.data)["consumer_name"] == "deployment-outbox-consumer"

    def test_url_can_isolate_one_aggregate(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            worker.fetch_pending_outbox(
                api_url="http://localhost:8095",
                aggregate_id="deployment-saga-task-001",
            )

        request = mock_urlopen.call_args[0][0]
        assert json.loads(request.data)["aggregate_id"] == "deployment-saga-task-001"


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
    def test_aggregate_filter_is_forwarded_to_pending_query(self, worker):
        with patch.object(worker, "fetch_pending_outbox", return_value=[]) as fetch:
            worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                aggregate_id="deployment-saga-task-001",
            )

        fetch.assert_called_once_with(
            api_url="http://localhost:8095",
            consumer_name="test-consumer",
            timeout_seconds=10.0,
            aggregate_id="deployment-saga-task-001",
        )

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

        mock_saga = _binding_saga()
        mock_plan = _binding_plan()
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
            authority_request = worker.verify_deploy_authorities.call_args.args[0]
            assert authority_request == {
                "plan_id": "plan-001",
                "approval_decision_id": "approval-001",
                "strategy_id": "strategy-001",
                "artifact_id": "artifact-001",
                "artifact_version": "v1.0.0",
                "capital_pool_id": "pool-001",
                "target_stage": "paper",
                "plan_status": "approved",
               "sponsor_persona_id": "persona-001",
                "persona_capital_binding_id": "pcb-001",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "paper",
            }
            deploy_context = mock_dispatch.call_args.kwargs["deploy_context"]
            assert deploy_context["sponsor_persona_id"] == "persona-001"
            assert deploy_context["metadata"]["authoritative_loader_attestation"] == _authority_report()
            assert deploy_context["metadata"]["tenant_id"] == "tenant-deployment-test"
            assert (
                deploy_context["metadata"]["deployment_correlation_id"]
                == "correlation-deployment-test"
            )
            assert "loader_checks_passed" not in deploy_context["metadata"]
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

        mock_saga = _binding_saga()
        mock_plan = _binding_plan()
        mock_compat = {
            "ok": False,
            "errors": ["missing capital binding"],
        }

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch("services.deployment.outbox_consumer_worker.fetch_saga", return_value=mock_saga),
            patch("services.deployment.outbox_consumer_worker.fetch_plan", return_value=mock_plan),
            patch("services.deployment.outbox_consumer_worker.run_compatibility_check", return_value=mock_compat),
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-001"),
            ),
        ):
            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["dead_lettered"] == 0
            assert result["consumed"] == 1
            assert "Compatibility check failed" in result["errors"][0]
            handoff.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                reason="Compatibility check failed: missing capital binding",
                event_type="runtime.binding.requested",
                timeout_seconds=10.0,
            )

    def test_binding_requested_transient_error(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = _binding_saga()
        mock_plan = _binding_plan()
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

        mock_saga = _binding_saga()
        mock_plan = _binding_plan()
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
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-001"),
            ),
            patch("services.deployment.outbox_consumer_worker.RuntimeManagerClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.list_by_plan.return_value = []

            result = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")

            assert result["events_found"] == 1
            assert result["dead_lettered"] == 0
            assert result["consumed"] == 1
            handoff.assert_called_once_with(
                api_url="http://localhost:8095",
                saga_id="saga-001",
                reason="terminal dispatch failure: invalid signature",
                event_type="runtime.binding.requested",
                timeout_seconds=10.0,
            )

    def test_binding_requested_downstream_success_before_receipt(self, worker):
        record = _outbox_record("evt-001")
        record["event"]["event_type"] = "runtime.binding.requested"
        record["event"]["aggregate_id"] = "saga-001"

        mock_saga = _binding_saga()
        mock_plan = _binding_plan()
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
        saga = _binding_saga(
            binding_id="rb-existing",
            status="awaiting_runtime_load",
        )
        plan = _binding_plan(status="executing")
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

    def test_completed_binding_history_is_receipt_only(self, worker):
        record = _outbox_record("evt-terminal-history", sequence_no=1)
        record["event"].update(
            {
                "event_type": "runtime.binding.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-existing"},
            }
        )
        saga = _binding_saga(
            binding_id="rb-existing",
            status="completed",
            current_step="runtime_active",
        )
        worker.verify_deploy_authorities.reset_mock()
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-terminal-history"),
            ),
            patch.object(worker, "fetch_plan") as fetch_plan,
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["consumed"] == 1
        fetch_plan.assert_not_called()
        client_class.assert_not_called()
        worker.verify_deploy_authorities.assert_not_called()

    def test_completed_runtime_load_revalidates_terminal_projection_before_receipt(
        self, worker
    ):
        record = _outbox_record("evt-completed-load", sequence_no=2)
        record["event"].update(
            {
                "event_type": "runtime.load.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-existing"},
            }
        )
        saga = _binding_saga(
            binding_id="rb-existing",
            status="completed",
            current_step="runtime_active",
            target_stage="canary",
        )
        binding = _runtime_binding(
            binding_id="rb-existing",
            deployment_mode="canary",
            execution_mode="canary",
            metadata={
                "strategy_id": "strategy-001",
                "authoritative_loader_attestation": {
                    **_authority_report(),
                    "target_stage": "canary",
                },
            },
        )

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(worker, "fetch_saga", side_effect=[saga, saga]),
            patch.object(worker, "fetch_projection", return_value={}),
            patch.object(worker, "consume_event") as consume,
            patch.object(worker, "record_runtime_active") as record_active,
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            client_class.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["consumed"] == 0
        assert "terminal deployment projection did not converge" in " ".join(
            result["errors"]
        )
        consume.assert_not_called()
        record_active.assert_not_called()

    def test_binding_recovery_read_failure_never_blindly_redeploys(self, worker):
        record = _outbox_record("evt-recovery-fail")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        saga = _binding_saga()
        plan = _binding_plan()
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

    def test_missing_remote_client_config_exhaustion_hands_off_saga(self, worker):
        record = _outbox_record("evt-client-config")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=_binding_saga()),
            patch.object(worker, "fetch_plan", return_value=_binding_plan()),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(
                worker,
                "RuntimeManagerClient",
                side_effect=RuntimeError("PANTHEON_RUNTIME_MANAGER_URL is required"),
            ),
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-client-config"),
            ),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
                max_attempts=1,
            )

        assert result["consumed"] == 1
        assert result["dead_lettered"] == 0
        handoff.assert_called_once_with(
            api_url="http://localhost:8095",
            saga_id="saga-001",
            event_type="runtime.binding.requested",
            reason=(
                "event_id=evt-client-config error="
                "PANTHEON_RUNTIME_MANAGER_URL is required"
            ),
            timeout_seconds=10.0,
        )

    def test_runtime_load_completes_from_authoritative_binding_without_fleet(
        self, worker, monkeypatch
    ):
        monkeypatch.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
        record = _outbox_record("evt-load", sequence_no=2)
        record["event"].update(
            {
                "event_type": "runtime.load.requested",
                "aggregate_id": "saga-001",
                "payload": {"binding_id": "rb-001", "runtime_id": "rt-001"},
            }
        )
        saga = _binding_saga(
            binding_id="rb-001",
            status="awaiting_runtime_load",
        )
        binding = _runtime_binding()
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(
                worker, "fetch_saga", side_effect=[saga, _completed_saga(saga)]
            ),
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

    def test_runtime_load_projection_exhaustion_hands_off_then_acks_predecessor(
        self, worker
    ):
        record = _outbox_record("evt-load-projection-exhausted", sequence_no=2)
        record["delivery_attempts"] = 0
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
            "approval_decision_id": "approval-001",
            "strategy_id": "strategy-001",
            "artifact_id": "artifact-001",
            "artifact_version": "v1.0.0",
            "capital_pool_id": "pool-001",
            "target_stage": "canary",
            "binding_id": "rb-001",
            "status": "awaiting_runtime_load",
            "current_step": "runtime_load_requested",
        }
        completed = {
            **saga,
            "status": "completed",
            "current_step": "runtime_active",
        }
        compensating = {
            **completed,
            "status": "compensating",
            "current_step": "compensation_requested",
            "compensation": {"command_type": "request_rollback"},
        }
        binding = _runtime_binding(
            deployment_mode="canary",
            execution_mode="canary",
            metadata={
                "allowed_deployment_scope": "canary",
                "strategy_id": "strategy-001",
                "authoritative_loader_attestation": {
                    **_authority_report(),
                    "target_stage": "canary",
                },
            },
        )
        operations: list[str] = []

        def _record_saga_failure(**_kwargs):
            operations.append("saga_failure")
            return {"command_type": "request_rollback"}

        def _ack(**_kwargs):
            operations.append("ack")
            return _inbox_receipt("evt-load-projection-exhausted")

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(
                worker,
                "fetch_saga",
                side_effect=[saga, completed, completed, compensating],
            ),
            patch.object(worker, "fetch_projection", return_value={}),
            patch.object(worker, "record_runtime_active") as runtime_active,
            patch.object(
                worker, "record_saga_failure", side_effect=_record_saga_failure
            ) as saga_failure,
            patch.object(worker, "consume_event", side_effect=_ack),
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            client_class.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
                record_failures=True,
                max_attempts=1,
            )

        assert result["dead_lettered"] == 0
        assert result["consumed"] == 1
        assert operations == ["saga_failure", "ack"]
        assert saga_failure.call_args.kwargs["failed_step"] == "runtime_active"
        runtime_active.assert_called_once()

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
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-load-killed"),
            ),
            patch.object(worker, "record_runtime_active") as mock_active,
            patch.object(worker, "RuntimeManagerClient") as client_cls,
        ):
            client_cls.return_value.get.return_value = binding
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["dead_lettered"] == 0
        assert result["consumed"] == 1
        assert "status expected 'active'" in " ".join(result["errors"])
        handoff.assert_called_once()
        mock_active.assert_not_called()

    def test_forged_loader_boolean_cannot_bypass_authority_rejection(self, worker):
        record = _outbox_record("evt-loader-forged")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        saga = _binding_saga()
        plan = _binding_plan(
            metadata={
                "loader_checks_passed": True,
                "tenant_id": "tenant-deployment-test",
            }
        )
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
            patch.object(
                worker,
                "verify_deploy_authorities",
                side_effect=worker.DeployAuthorityError("checksum mismatch"),
            ),
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-loader-forged"),
            ),
            patch.object(worker, "dispatch_to_runtime_manager") as dispatch,
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["dead_lettered"] == 0
        assert result["consumed"] == 1
        assert "deploy authority rejected: checksum mismatch" in " ".join(result["errors"])
        handoff.assert_called_once()
        client_class.assert_not_called()
        dispatch.assert_not_called()

    def test_authority_unavailable_retries_before_runtime_client(self, worker):
        record = _outbox_record("evt-authority-unavailable")
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=_binding_saga()),
            patch.object(worker, "fetch_plan", return_value=_binding_plan()),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(
                worker,
                "verify_deploy_authorities",
                side_effect=worker.DeployAuthorityUnavailableError("registry timeout"),
            ),
            patch.object(worker, "record_saga_failure") as saga_failure,
            patch.object(
                worker,
                "_record_failure_best_effort",
                return_value=({"status": "pending"}, None),
            ),
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["retry_scheduled"] == 1
        assert "deploy authority unavailable: registry timeout" in " ".join(result["errors"])
        saga_failure.assert_not_called()
        client_class.assert_not_called()

    def test_authority_unavailable_exhaustion_hands_off_then_acks(self, worker):
        record = _outbox_record("evt-authority-exhausted")
        record["delivery_attempts"] = 2
        record["event"].update(
            {"event_type": "runtime.binding.requested", "aggregate_id": "saga-001"}
        )
        compat = {
            "ok": True,
            "persona_binding_id": "pcb-001",
            "persona_scope_ok": True,
            "allowed_deployment_scope": "paper",
        }
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(worker, "fetch_saga", return_value=_binding_saga()),
            patch.object(worker, "fetch_plan", return_value=_binding_plan()),
            patch.object(worker, "run_compatibility_check", return_value=compat),
            patch.object(
                worker,
                "verify_deploy_authorities",
                side_effect=worker.DeployAuthorityUnavailableError("governance 503"),
            ),
            patch.object(worker, "_trigger_delivery_compensation") as handoff,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-authority-exhausted"),
            ),
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["dead_lettered"] == 0
        assert result["consumed"] == 1
        handoff.assert_called_once_with(
            api_url="http://localhost:8095",
            saga_id="saga-001",
            reason="deploy authority unavailable: governance 503",
            event_type="runtime.binding.requested",
            timeout_seconds=10.0,
        )
        client_class.assert_not_called()

    def test_plan_saga_identity_mismatch_fails_before_authority_read(self, worker):
        worker.verify_deploy_authorities.reset_mock()

        with pytest.raises(worker.DeployAuthorityError, match="artifact_id mismatch"):
            worker.verify_binding_deploy_authorities(
                saga=_binding_saga(),
                plan=_binding_plan(artifact_id="forged-artifact"),
                persona_capital_binding_id="pcb-001",
                persona_capital_binding_status="active",
                allowed_deployment_scope="paper",
                deployment_base_url="http://deployment:8095",
                timeout_seconds=10.0,
            )

        worker.verify_deploy_authorities.assert_not_called()

    def test_stage_promotion_authority_is_scoped_to_source_registry_stage(self, worker):
        worker.verify_deploy_authorities.reset_mock()
        saga = _binding_saga(
            current_stage="paper",
            target_stage="canary",
            runtime_action="replace_binding",
        )
        plan = _binding_plan(
            current_stage="paper",
            target_stage="canary",
            runtime_action="replace_binding",
            binding_id="rb-paper-001",
        )

        worker.verify_binding_deploy_authorities(
            saga=saga,
            plan=plan,
            persona_capital_binding_id="pcb-001",
            persona_capital_binding_status="active",
            allowed_deployment_scope="canary",
            deployment_base_url="http://deployment:8095",
            timeout_seconds=10.0,
        )

        call_kwargs = worker.verify_deploy_authorities.call_args.kwargs
        assert call_kwargs["allowed_target_stages"] == ("canary",)
        assert call_kwargs["allowed_registry_deployment_stages"] == ("paper",)

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

    def test_terminal_predecessor_ack_unblocks_compensation_sequence(self, worker):
        record = _outbox_record("evt-comp-after-terminal", sequence_no=2)
        record["event"].update(
            {
                "event_type": "deployment.compensation.requested",
                "aggregate_id": "saga-001",
            }
        )
        saga = _compensating_saga("abort_plan")
        terminal = {**saga, "status": "aborted", "current_step": "compensated"}
        plan = {"plan_id": "plan-001", "status": "approved"}
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[record]),
            patch.object(
                worker,
                "fetch_applied_inbox",
                return_value=[_applied_receipt(1)],
            ),
            patch.object(worker, "fetch_saga", side_effect=[saga, terminal]),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(
                worker,
                "execute_compensation",
                return_value=("aborted", "aborted"),
            ) as execute,
            patch.object(
                worker,
                "fetch_projection",
                return_value=_compensation_projection(
                    terminal, plan_status="aborted"
                ),
            ),
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-comp-after-terminal"),
            ),
            patch.object(worker, "RuntimeManagerClient"),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["consumed"] == 1
        assert result["skipped_not_due"] == 0
        execute.assert_called_once()

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
            client_class.return_value.get.return_value = _runtime_binding(
                status="failed"
            )
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        execute.assert_not_called()
        client_class.assert_called_once()
        client_class.return_value.get.assert_called_once_with("rb-001")
        client_class.return_value.transition.assert_not_called()
        client_class.return_value.execute_kill_switch.assert_not_called()

    def test_terminal_safe_mode_replay_reproves_incident_before_consume(self, worker):
        record = _outbox_record("evt-comp-safe-replay", sequence_no=3)
        record["event"].update(
            {
                "event_type": "deployment.compensation.requested",
                "aggregate_id": "saga-001",
            }
        )
        saga = {
            **_compensating_saga("enter_safe_mode_and_raise_incident"),
            "status": "failed",
            "current_step": "compensated",
        }
        plan = {"plan_id": "plan-001", "status": "executed"}
        paused = _runtime_binding(status="paused")
        incident = worker._incident_payload(
            saga=saga,
            binding=paused,
            event_id="evt-comp-safe-replay",
            reason="original containment reason",
        )
        with (
            patch.dict(
                worker.os.environ,
                {"PANTHEON_INCIDENTS_API_URL": "http://incidents:8090"},
            ),
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
                return_value=_compensation_projection(saga, plan_status="executed"),
            ),
            patch.object(worker, "fetch_incident", return_value=incident) as fetch_incident,
            patch.object(worker, "execute_compensation") as execute,
            patch.object(
                worker,
                "consume_event",
                return_value=_inbox_receipt("evt-comp-safe-replay"),
            ) as consume,
            patch.object(worker, "RuntimeManagerClient") as client_class,
        ):
            client_class.return_value.get.side_effect = [paused, paused]
            client_class.return_value.get_safe_mode.return_value = {
                "safe_mode_state": "paused"
            }
            result = worker.run_poll(
                api_url="http://localhost:8095", consumer_name="test-consumer"
            )

        assert result["consumed"] == 1
        execute.assert_not_called()
        client_class.return_value.execute_kill_switch.assert_not_called()
        fetch_incident.assert_called_once_with(
            base_url="http://incidents:8090",
            incident_id="inc-deployment-comp-saga-001",
            timeout_seconds=10.0,
        )
        consume.assert_called_once()


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
            metadata={
                "allowed_deployment_scope": "canary",
                "strategy_id": "strategy-001",
                "authoritative_loader_attestation": _fallback_authority_report(
                    allowed_deployment_scope="canary"
                ),
            },
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
        }
        with (
            patch.object(
                worker,
                "fetch_plan",
                return_value=_fallback_plan(),
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
        assert request["replacement_plan_id"] == "plan-fallback"
        assert request["replacement_plan_status"] == "executed"
        assert (
            request["replacement_authority_attestation"]
            == _fallback_authority_report(allowed_deployment_scope="canary")
        )
        assert request["replacement_allowed_deployment_scope"] == "canary"
        assert request["replacement_metadata"]["compensation_event_id"] == "evt-request_rollback"
        finalize.assert_called_once()

    def test_kill_racing_rollback_contains_paused_child_and_finalizes(self, worker):
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
            metadata={
                "allowed_deployment_scope": "paper",
                "strategy_id": "strategy-001",
                "authoritative_loader_attestation": _fallback_authority_report(),
            },
        )
        child = _runtime_binding(
            binding_id="rb-fallback",
            plan_id="plan-fallback",
            runtime_id="rt-fallback",
            artifact_id="artifact-fallback",
            artifact_version="v0.9.0",
            rollback_parent="rb-001",
            rollback_action_type="replace",
            status="paused",
        )
        client = MagicMock()
        client.get.side_effect = [old, retired, child, child]
        client.get_safe_mode.side_effect = [
            {"safe_mode_state": "normal"},
            {"safe_mode_state": "paused"},
        ]
        client.list_by_pool.return_value = [old, prior]
        client.rollback.return_value = {"new_binding": child}
        client.execute_kill_switch.return_value = {
            "telemetry_ack": _kill_ack("rb-fallback")
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
            patch.object(
                worker,
                "fetch_plan",
                return_value=_fallback_plan(),
            ),
            patch.object(worker, "create_incident", side_effect=_return_payload),
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
        kill_request = client.execute_kill_switch.call_args.args[0]
        assert kill_request["binding_id"] == "rb-001"
        assert client.get.call_args_list[-1] == call("rb-fallback")
        client.get_active_for_pool.assert_not_called()
        finalize.assert_called_once()

    def test_terminal_rollback_replay_reproves_paused_child_incident(self, worker):
        saga = {
            **_compensating_saga("request_rollback"),
            "status": "failed",
            "current_step": "compensated",
        }
        source = _runtime_binding(status="retired")
        child = _runtime_binding(
            binding_id="rb-fallback",
            plan_id="plan-fallback",
            runtime_id="rt-fallback",
            artifact_id="artifact-fallback",
            artifact_version="v0.9.0",
            rollback_parent="rb-001",
            rollback_action_type="replace",
            status="paused",
        )
        plan = {
            "plan_id": "plan-001",
            "status": "executed",
            "rollback": {
                "target_artifact_id": "artifact-fallback",
                "target_version": "v0.9.0",
                "action_type": "replace",
            },
        }
        event = self._event("request_rollback")
        incident = worker._incident_payload(
            saga=saga,
            binding=child,
            event_id=event["event_id"],
            reason="original containment reason",
        )
        client = MagicMock()
        client.get.side_effect = [source, child, child]
        client.list_by_pool.return_value = [source, child]
        client.get_safe_mode.return_value = {"safe_mode_state": "paused"}

        with patch.object(worker, "fetch_incident", return_value=incident):
            status = worker.verify_terminal_compensation_side_effects(
                saga=saga,
                plan=plan,
                event=event,
                client=client,
                incident_url="http://incidents:8090",
                timeout_seconds=10.0,
            )

        assert status == "executed"
        client.get_active_for_pool.assert_not_called()
        client.execute_kill_switch.assert_not_called()

    def test_safe_mode_requires_ack_readback_and_exact_incident(self, worker):
        saga = _compensating_saga("enter_safe_mode_and_raise_incident")
        active = _runtime_binding()
        paused = _runtime_binding(status="paused")
        client = MagicMock()
        client.get.side_effect = [active, paused]
        client.execute_kill_switch.return_value = {"telemetry_ack": _kill_ack()}
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

    def test_incident_response_loss_replay_keeps_kill_payload_stable(self, worker):
        saga = _compensating_saga("request_rollback")
        paused = _runtime_binding(status="paused")
        event = self._event("request_rollback")
        client = MagicMock()
        client.execute_kill_switch.return_value = {"telemetry_ack": _kill_ack()}
        client.get_safe_mode.return_value = {"safe_mode_state": "paused"}
        client.get.return_value = paused
        committed_incident = worker._incident_payload(
            saga=saga,
            binding=paused,
            event_id=event["event_id"],
            reason="rollback loader proof failed closed",
        )

        with patch.object(
            worker,
            "create_incident",
            side_effect=[
                urllib.error.URLError("incident response lost after commit"),
                committed_incident,
            ],
        ):
            with pytest.raises(urllib.error.URLError):
                worker._contain_and_raise_incident(
                    client=client,
                    saga=saga,
                    binding=paused,
                    event_id=event["event_id"],
                    event_idempotency_key=event["idempotency_key"],
                    incident_url="http://incidents:8090",
                    reason="rollback loader proof failed closed",
                    timeout_seconds=10.0,
                )
            worker._contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=paused,
                event_id=event["event_id"],
                event_idempotency_key=event["idempotency_key"],
                incident_url="http://incidents:8090",
                reason="kill-switch safe mode won before rollback compensation: paused",
                timeout_seconds=10.0,
            )

        kill_calls = client.execute_kill_switch.call_args_list
        assert len(kill_calls) == 2
        assert kill_calls[0].args[0] == kill_calls[1].args[0]
        assert (
            kill_calls[0].args[0]["context"]["reason"]
            == "deployment_compensation_fail_closed"
        )

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
        client.execute_kill_switch.return_value = {"telemetry_ack": _kill_ack()}
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
        client.list_by_pool.assert_called_once_with("pool-001")


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

    def test_successful_idle_poll_recovers_degraded_health(
        self, worker, monkeypatch, capsys
    ):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        with (
            patch.object(
                worker,
                "run_poll",
                side_effect=[
                    {
                        "events_found": 1,
                        "consumed": 0,
                        "duplicates": 0,
                        "retry_scheduled": 1,
                        "dead_lettered": 0,
                        "skipped_not_due": 0,
                        "errors": ["temporary deployment API failure"],
                    },
                    {
                        "events_found": 0,
                        "consumed": 0,
                        "duplicates": 0,
                        "retry_scheduled": 0,
                        "dead_lettered": 0,
                        "skipped_not_due": 0,
                        "errors": [],
                    },
                ],
            ),
            patch("time.sleep"),
        ):
            worker.main()

        last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert last["health"]["status"] == "ok"
        assert last["health"]["last_idle_success"] is not None
        assert last["health"]["last_recovered_at"] is not None
        assert last["health"]["recovery_count"] == 1

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


# ---------------------------------------------------------------------------
# L12-CURRENT-DEPLOYMENT-AUTH-20260814 Authority Header Contract Tests
# ---------------------------------------------------------------------------


class TestAuthorityHeaderContract:
    def test_deployment_headers_require_token_and_tenant(self, worker, monkeypatch):
        monkeypatch.delenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", raising=False)
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-test")
        with pytest.raises(RuntimeError, match="PANTHEON_DEPLOYMENT_SERVICE_TOKEN is required"):
            worker._deployment_headers()

        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "token-test")
        monkeypatch.delenv("PANTHEON_DEPLOYMENT_TENANT_ID", raising=False)
        with pytest.raises(RuntimeError, match="PANTHEON_DEPLOYMENT_TENANT_ID is required"):
            worker._deployment_headers()

    def test_deployment_headers_bearer_format_and_content_type(self, worker, monkeypatch):
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "raw-token")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-alpha")
        headers = worker._deployment_headers(json_body=True)
        assert headers["Authorization"] == "Bearer raw-token"
        assert headers["X-Tenant-Id"] == "tenant-alpha"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "Bearer already-bearer")
        headers_get = worker._deployment_headers(json_body=False)
        assert headers_get["Authorization"] == "Bearer already-bearer"
        assert "Content-Type" not in headers_get

    def test_all_owner_mutation_and_get_calls_carry_credentials(self, worker, monkeypatch):
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "test-svc-token")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "test-tenant-id")

        captured_requests = []

        def fake_urlopen(req, *args, **kwargs):
            captured_requests.append(req)
            resp = MagicMock()
            if req.full_url.endswith("/claim") or req.full_url.endswith("/inbox"):
                resp.read.return_value = b"[]"
            else:
                resp.read.return_value = b'{"status": "ok"}'
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            worker.fetch_pending_outbox(
                api_url="http://deployment:8095",
                consumer_name="worker-1",
            )
            worker.fetch_applied_inbox(
                api_url="http://deployment:8095",
                consumer_name="worker-1",
                aggregate_id="saga-1",
            )
            worker.consume_event(
                api_url="http://deployment:8095",
                event_id="evt-1",
                consumer_name="worker-1",
            )
            worker.record_delivery_failure(
                api_url="http://deployment:8095",
                event_id="evt-1",
                consumer_name="worker-1",
                reason="timeout",
                retryable=True,
                max_attempts=3,
                retry_delay_seconds=30,
            )
            worker.fetch_saga(api_url="http://deployment:8095", saga_id="saga-1")
            worker.fetch_plan(api_url="http://deployment:8095", plan_id="plan-1")
            worker.fetch_projection(api_url="http://deployment:8095", plan_id="plan-1")
            worker.update_plan_status(
                api_url="http://deployment:8095",
                plan_id="plan-1",
                status="executing",
            )
            worker.finalize_compensation(
                api_url="http://deployment:8095",
                saga_id="saga-1",
                note="failure",
                terminal_status="rolled_back",
            )
            worker.run_compatibility_check(
                api_url="http://deployment:8095",
                capital_pool_id="pool-1",
                sponsor_persona_id="persona-1",
                target_stage="paper",
            )
            worker.record_binding_created(
                api_url="http://deployment:8095",
                saga_id="saga-1",
                binding_id="rb-1",
                runtime_id="rt-1",
                note="created",
            )
            worker.record_runtime_active(
                api_url="http://deployment:8095",
                saga_id="saga-1",
                binding_id="rb-1",
                runtime_id="rt-1",
                note="active",
            )
            worker.record_saga_failure(
                api_url="http://deployment:8095",
                saga_id="saga-1",
                reason="error",
                failed_step="runtime_active",
            )

        assert len(captured_requests) == 13
        for req in captured_requests:
            assert req.headers.get("Authorization") == "Bearer test-svc-token"
            assert req.headers.get("X-tenant-id") == "test-tenant-id"

    def test_fetch_authority_json_authenticates_deployment_urls(self, worker, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "sec-token")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-x")

        captured = []

        def fake_open(self, req, *args, **kwargs):
            captured.append(req)
            resp = MagicMock()
            resp.read.return_value = b'{"plan_id": "plan-1"}'
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.OpenerDirector.open", fake_open):
            res_dep = worker._fetch_authority_json("http://deployment:8095/api/deployment/plans/plan-1", 5.0)
            res_gov = worker._fetch_authority_json("http://governance:8091/api/approvals/app-1", 5.0)

        assert res_dep == {"plan_id": "plan-1"}
        assert captured[0].headers.get("Authorization") == "Bearer sec-token"
        assert captured[0].headers.get("X-tenant-id") == "tenant-x"
        assert "Authorization" not in captured[1].headers
        assert "X-tenant-id" not in captured[1].headers

    def test_fetch_authority_json_does_not_authenticate_foreign_origin_with_deployment_path(self, worker, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "sec-token")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-x")

        captured = []

        def fake_open(self, req, *args, **kwargs):
            captured.append(req)
            resp = MagicMock()
            resp.read.return_value = b'{"status": "foreign"}'
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.OpenerDirector.open", fake_open):
            # Foreign domain that includes /api/deployment/ in the path
            res_foreign = worker._fetch_authority_json("http://attacker.com/api/deployment/plans/plan-1", 5.0)
            # Foreign port on same hostname that includes /api/deployment/ in the path
            res_foreign_port = worker._fetch_authority_json("http://deployment:9999/api/deployment/plans/plan-1", 5.0)

        assert res_foreign == {"status": "foreign"}
        assert res_foreign_port == {"status": "foreign"}
        assert "Authorization" not in captured[0].headers
        assert "X-tenant-id" not in captured[0].headers
        assert "Authorization" not in captured[1].headers
        assert "X-tenant-id" not in captured[1].headers

    def test_cross_origin_redirect_strips_credentials(self, worker, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
        handler = worker._AuthorityRedirectHandler()
        request = urllib.request.Request(
            "http://deployment:8095/api/deployment/plans/1",
            headers={
                "Authorization": "Bearer sec-token",
                "X-Tenant-Id": "tenant-x",
                "Accept": "application/json",
            },
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://attacker.com/leak",
        )
        assert redirected is not None
        assert redirected.get_header("Authorization") is None
        assert redirected.get_header("X-tenant-id") is None
        assert redirected.get_header("Accept") == "application/json"

    def test_same_origin_redirect_preserves_credentials(self, worker, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
        handler = worker._AuthorityRedirectHandler()
        request = urllib.request.Request(
            "http://deployment:8095/api/deployment/plans/1",
            headers={
                "Authorization": "Bearer sec-token",
                "X-Tenant-Id": "tenant-x",
                "Accept": "application/json",
            },
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://deployment:8095/api/deployment/plans/1_redirected",
        )
        assert redirected is not None
        assert redirected.get_header("Authorization") == "Bearer sec-token"
        assert redirected.get_header("X-tenant-id") == "tenant-x"
        assert redirected.get_header("Accept") == "application/json"

    def test_fetch_authority_json_maps_http_errors(self, worker, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://deployment:8095")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "sec-token")
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_TENANT_ID", "tenant-x")

        def fake_403(self, req, *args, **kwargs):
            raise urllib.error.HTTPError("http://deployment:8095/api/deployment/plans/1", 403, "Forbidden", {}, None)

        def fake_503(self, req, *args, **kwargs):
            raise urllib.error.HTTPError("http://deployment:8095/api/deployment/plans/1", 503, "Service Unavailable", {}, None)

        with patch("urllib.request.OpenerDirector.open", fake_403):
            with pytest.raises(worker.DeployAuthorityError, match="HTTP 403"):
                worker._fetch_authority_json("http://deployment:8095/api/deployment/plans/1", 5.0)

        with patch("urllib.request.OpenerDirector.open", fake_503):
            with pytest.raises(worker.DeployAuthorityUnavailableError, match="HTTP 503"):
                worker._fetch_authority_json("http://deployment:8095/api/deployment/plans/1", 5.0)


# ---------------------------------------------------------------------------
# L12-CURRENT-DEPLOYMENT-AUTH-20260814 Health Tests (Idle Success & 403)
# ---------------------------------------------------------------------------


class TestHealthcheckAndTruthfulHealth:
    def test_healthcheck_returns_zero_on_healthy_consumer(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "deployment-outbox-consumer",
                "status": "ok",
                "ticks": 5,
                "last_success": "2026-08-14T10:00:00Z",
                "consecutive_errors": 0,
            },
        )
        assert worker.healthcheck(health_file=health_path, consumer_name="deployment-outbox-consumer") == 0

    def test_healthcheck_returns_zero_on_idle_success(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "deployment-outbox-consumer",
                "status": "ok",
                "ticks": 1,
                "last_success": "2026-08-14T10:00:00Z",
                "last_idle_success": "2026-08-14T10:00:00Z",
                "consecutive_errors": 0,
            },
        )
        assert worker.healthcheck(health_file=health_path, consumer_name="deployment-outbox-consumer") == 0

    def test_healthcheck_fails_when_unconfigured_or_missing(self, worker, tmp_path):
        assert worker.healthcheck(health_file="", consumer_name="test") == 1
        assert worker.healthcheck(health_file=str(tmp_path / "nonexistent.json"), consumer_name="test") == 1

    def test_healthcheck_fails_on_status_not_ok_or_zero_ticks(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "test",
                "status": "starting",
                "ticks": 0,
                "last_success": None,
            },
        )
        assert worker.healthcheck(health_file=health_path, consumer_name="test") == 1

    def test_healthcheck_fails_on_zero_success_streak(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "test",
                "status": "degraded",
                "ticks": 3,
                "last_success": None,
                "consecutive_errors": 3,
                "last_failure_reason": "HTTP 403 Forbidden",
            },
        )
        assert worker.healthcheck(health_file=health_path, consumer_name="test") == 1

    def test_healthcheck_fails_on_consecutive_errors_even_if_previously_succeeded(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "test",
                "status": "degraded",
                "ticks": 10,
                "last_success": "2026-08-14T09:00:00Z",
                "consecutive_errors": 2,
                "last_failure_reason": "connection timeout",
            },
        )
        assert worker.healthcheck(health_file=health_path, consumer_name="test") == 1

    def test_healthcheck_fails_on_stale_heartbeat(self, worker, tmp_path):
        health_path = str(tmp_path / "health.json")
        worker._write_health(
            health_path,
            {
                "consumer_name": "test",
                "status": "ok",
                "ticks": 5,
                "last_success": "2026-08-14T10:00:00Z",
                "consecutive_errors": 0,
            },
        )
        file_mtime = os.path.getmtime(health_path)
        assert worker.healthcheck(
            health_file=health_path,
            consumer_name="test",
            max_age_seconds=10.0,
            now=file_mtime + 50.0,
        ) == 1

    def test_main_resets_health_at_startup(self, worker, monkeypatch, tmp_path):
        health_file = str(tmp_path / "health.json")
        Path(health_file).write_text(json.dumps({"status": "ok", "ticks": 99}))

        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", health_file)

        records = [_outbox_record("evt-init-001")]
        receipt = _inbox_receipt("evt-init-001", status="applied")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
            patch("time.sleep"),
        ):
            worker.main()

        content = json.loads(Path(health_file).read_text("utf-8"))
        assert content["ticks"] == 1
        assert content["status"] == "ok"
        assert content["total_consumed"] == 1

    def test_main_transitions_to_degraded_on_403_and_fails_healthcheck(self, worker, monkeypatch, tmp_path):
        health_file = str(tmp_path / "health.json")
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", health_file)

        def _forbidden_claim(**_kwargs):
            raise urllib.error.HTTPError(
                "http://localhost:8095/api/deployment/outbox/claim",
                403,
                "Forbidden: invalid credentials",
                {},
                None,
            )

        with (
            patch.object(worker, "fetch_pending_outbox", side_effect=_forbidden_claim),
            patch("time.sleep"),
        ):
            worker.main()

        assert not Path(health_file).exists()
        assert worker.healthcheck(health_file=health_file) == 1

    def test_main_recovers_health_file_after_403(self, worker, monkeypatch, tmp_path):
        health_file = str(tmp_path / "health.json")
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", health_file)

        call_count = 0

        def _flaky_claim(**_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.HTTPError(
                    "http://localhost:8095/api/deployment/outbox/claim",
                    403,
                    "Forbidden: invalid credentials",
                    {},
                    None,
                )
            return []

        with (
            patch.object(worker, "fetch_pending_outbox", side_effect=_flaky_claim),
            patch("time.sleep"),
        ):
            worker.main()

        assert Path(health_file).exists()
        content = json.loads(Path(health_file).read_text("utf-8"))
        assert content["status"] == "ok"
        assert content["ticks"] == 2
        assert content["recovery_count"] == 1
        assert content["last_recovered_at"] is not None
        assert worker.healthcheck(health_file=health_file) == 0


# ---------------------------------------------------------------------------
# L12-CURRENT-DEPLOYMENT-AUTH-20260814 Saga Replay and Readback Tests
# ---------------------------------------------------------------------------


class TestSagaReplayAndReadback:
    def test_approved_plan_full_lifecycle_and_exact_readback(self, worker, monkeypatch):
        plan = _binding_plan()
        saga = _binding_saga()
        binding = _runtime_binding()
        saga_id = saga["saga_id"]
        plan_id = plan["plan_id"]
        binding_id = binding["binding_id"]

        # Step 1: runtime.binding.requested
        evt1 = _outbox_record("evt-bind-001", sequence_no=1)
        evt1["event"].update({"event_type": "runtime.binding.requested", "aggregate_id": saga_id})

        mock_client = MagicMock()
        mock_client.list_by_plan.return_value = []
        mock_result = worker.DispatchResult(
            outcome=worker.DispatchOutcome.SUCCESS,
            binding_id=binding_id,
            binding=binding,
        )

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[evt1]),
            patch.object(worker, "fetch_applied_inbox", return_value=[]),
            patch.object(worker, "fetch_saga", return_value=saga),
            patch.object(worker, "fetch_plan", return_value=plan),
            patch.object(
                worker,
                "run_compatibility_check",
                return_value={
                    "ok": True,
                    "persona_binding_id": "pcb-001",
                    "persona_scope_ok": True,
                    "allowed_deployment_scope": "paper",
                },
            ),
            patch.object(worker, "dispatch_to_runtime_manager", return_value=mock_result),
            patch.object(worker, "RuntimeManagerClient", return_value=mock_client),
            patch.object(worker, "record_binding_created", return_value={"status": "ok"}) as mock_rec_bind,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-bind-001", status="applied")),
        ):
            res1 = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")
            assert res1["consumed"] == 1
            assert len(res1["errors"]) == 0
            mock_rec_bind.assert_called_once()

        # Step 2: runtime.load.requested
        evt2 = _outbox_record("evt-load-001", sequence_no=2)
        evt2["event"].update({
            "event_type": "runtime.load.requested",
            "aggregate_id": saga_id,
            "payload": {"binding_id": binding_id},
        })
        saga["status"] = "awaiting_runtime_load"
        saga["binding_id"] = binding_id

        terminal_saga = _completed_saga(saga)
        projection = _success_projection(terminal_saga, binding)

        mock_client.get.return_value = binding

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[evt2]),
            patch.object(worker, "fetch_applied_inbox", return_value=[_applied_receipt(1)]),
            patch.object(worker, "fetch_saga", side_effect=[saga, terminal_saga]),
            patch.object(worker, "fetch_projection", return_value=projection),
            patch.object(worker, "RuntimeManagerClient", return_value=mock_client),
            patch.object(worker, "record_runtime_active", return_value={"status": "ok"}) as mock_rec_act,
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-load-001", status="applied")),
        ):
            res2 = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")
            assert res2["consumed"] == 1
            assert len(res2["errors"]) == 0
            mock_rec_act.assert_called_once()

    def test_replay_idempotency_on_completed_saga(self, worker):
        saga_id = "saga-completed-001"
        completed_saga = _binding_saga(
            saga_id=saga_id,
            plan_id="plan-001",
            status="completed",
            binding_id="rb-001",
        )

        evt = _outbox_record("evt-replay-001", sequence_no=1)
        evt["event"].update({"event_type": "runtime.binding.requested", "aggregate_id": saga_id})

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[evt]),
            patch.object(worker, "fetch_applied_inbox", return_value=[]),
            patch.object(worker, "fetch_saga", return_value=completed_saga),
            patch.object(worker, "consume_event", return_value=_inbox_receipt("evt-replay-001", status="applied")),
            patch.object(worker, "dispatch_to_runtime_manager") as mock_dispatch,
        ):
            res = worker.run_poll(api_url="http://localhost:8095", consumer_name="test-consumer")
            assert res["consumed"] == 1
            mock_dispatch.assert_not_called()
