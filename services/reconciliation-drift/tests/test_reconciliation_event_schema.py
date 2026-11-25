"""Reconciliation events must satisfy the real telemetry schema (RC2).

Hosted shape before the fix: reconciliation_failed/completed carried top-level
``reason_code`` / ``reason_detail``; the telemetry schema has
``additionalProperties: false`` so every event was refused (about 530/day).
"""

from __future__ import annotations

import json
import tempfile
from unittest import mock

import pytest

from services.control_plane.bff import trade_journeys as tj
from services.telemetry.ingest_svc import TelemetryIngestService
from services.trade_journey.lifecycle_projector import LifecycleProjector
from pathlib import Path

from test_reconciliation_drift_scheduler import (
    _hosted_eval,
    _load_service_module,
    _paper_lifecycle_summary,
)

_DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "telemetry" / "telemetry_event.schema.json"
)
TENANT_ENV = {"PANTHEON_PAPER_BROKER_URL": ""}


def _validator() -> TelemetryIngestService:
    return TelemetryIngestService(schema_path=str(_DEFAULT_SCHEMA_PATH))


def _emit(evaluated_at: str, timestamp: str, mutate=None):
    summary = _paper_lifecycle_summary()
    evaluation = _hosted_eval(evaluated_at, summary)
    if mutate:
        mutate(evaluation)
    svc = _load_service_module(tempfile.mkdtemp())
    with mock.patch.dict("os.environ", TENANT_ENV):
        event, reason = svc._scheduled_lifecycle_event(
            summary=summary, evaluation=evaluation, timestamp=timestamp
        )
    assert reason is None
    return svc, summary, evaluation, event


def _failed_event():
    return _emit("2026-11-16T06:58:29Z", "2026-07-15T11:00:00Z")


def _completed_event():
    return _emit("2026-11-14T07:48:57Z", "2026-07-15T10:05:00Z")


def test_previous_hosted_shape_is_refused_by_real_schema() -> None:
    _, _, _, event = _failed_event()
    legacy = json.loads(json.dumps(event))
    legacy["reason_code"] = legacy["metadata"]["reason_code"]
    legacy["reason_detail"] = legacy["metadata"]["reason_detail"]
    ok, err = _validator()._validate_event(legacy)
    assert ok is False and "reason" in str(err)
    completed_legacy = json.loads(json.dumps(_completed_event()[3]))
    completed_legacy["reason_detail"] = [{"check": "x", "status": "ok"}]
    ok, err = _validator()._validate_event(completed_legacy)
    assert ok is False


@pytest.mark.parametrize("builder", [_failed_event, _completed_event])
def test_emitted_events_validate_against_real_schema(builder) -> None:
    event = builder()[3]
    assert "reason_code" not in event and "reason_detail" not in event
    ok, err = _validator()._validate_event(event)
    assert ok is True, err


def test_booking_checked_event_validates() -> None:
    svc = _load_service_module(tempfile.mkdtemp())
    summary = _paper_lifecycle_summary()
    evaluation = _hosted_eval("2026-11-16T07:03:47Z", summary)
    booking = (
        "failed",
        "cash_variance",
        [{"check": "cash_change", "status": "failed", "expected": 1.0, "actual": 2.0}],
    )
    with mock.patch.dict("os.environ", {"PANTHEON_PAPER_BROKER_URL": "http://b"}), mock.patch.object(
        svc, "_journey_booking_decision", return_value=booking
    ):
        event, reason = svc._scheduled_lifecycle_event(
            summary=summary, evaluation=evaluation, timestamp="2026-07-15T10:10:00Z"
        )
    assert reason is None and event["event_type"] == "reconciliation_failed"
    ok, err = _validator()._validate_event(event)
    assert ok is True, err
    assert event["metadata"]["reason_code"] == "cash_variance"


def test_reason_detail_is_bounded() -> None:
    svc = _load_service_module(tempfile.mkdtemp())
    detail = [{"check": f"c{i}", "detail": "x" * 5000} for i in range(100)]
    bounded = svc._bounded_reason_detail(detail)
    assert len(bounded) == svc._REASON_DETAIL_MAX_ENTRIES
    assert all(len(item["detail"]) == svc._REASON_DETAIL_MAX_TEXT for item in bounded)


def test_projector_and_bff_read_reason_from_stored_event() -> None:
    _, _, _, event = _failed_event()
    identity = {
        "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "persona_id": "persona-1",
        "persona_capital_binding_id": "pcb-1",
        "artifact_id": "a",
        "artifact_version": "1",
        "plan_id": "pl",
        "trace_id": "tr",
    }
    (projected,) = LifecycleProjector._journey_events(
        {"event": event, "identity": identity, "source_mode": "live", "sequence_no": 6}
    )
    assert projected["stage"] == "reconciliation"
    assert projected["stage_status"] == "failed"
    assert projected["reason_code"] == "runtime_unhealthy_at_reconciliation"
    assert {d["check"] for d in projected["reason_detail"]} == {
        "runtime_state",
        "runtime_health_summary",
    }
    analysis = tj._reconciliation_analysis(projected)
    assert analysis["reason_code"] == "runtime_unhealthy_at_reconciliation"
    assert analysis["reason_detail"]


def _run_pass(svc, evaluation_id: str, summary, timestamp: str, delivery):
    evaluation = _hosted_eval("2026-11-16T06:58:29Z", summary)
    evaluation["evaluation_id"] = evaluation_id
    with mock.patch.dict("os.environ", TENANT_ENV), mock.patch.object(
        svc, "_append_telemetry_lifecycle_event", delivery
    ), mock.patch.object(
        svc, "_accepted_append_visibility_reason", return_value=(None, {})
    ):
        svc.store.put_evaluation(evaluation)
        token = svc._TENANT_CONTEXT.set(str(evaluation.get("tenant_id") or "default"))
        try:
            return svc._ensure_scheduled_lifecycle_append(
                summary=summary,
                evaluation=evaluation,
                telemetry_url="http://telemetry",
                timestamp=timestamp,
            )
        finally:
            svc._TENANT_CONTEXT.reset(token)


def test_refused_event_stops_repeating_and_resumes() -> None:
    svc = _load_service_module(tempfile.mkdtemp())
    summary = _paper_lifecycle_summary()
    refusal = {
        "status": "terminal_rejected",
        "terminal": True,
        "retryable": False,
        "outcome": "failed",
        "http_status": 422,
        "response": {"error": "schema"},
        "error": "Additional properties are not allowed ('reason_detail')",
    }
    sends = mock.Mock(return_value=dict(refusal))
    states = [
        _run_pass(svc, f"ev-{i}", summary, f"2026-07-15T11:0{i}:00Z", sends) for i in range(3)
    ]
    assert sends.call_count == 3
    assert all(s["status"] == "terminal_rejected" and not s.get("repeat_suppressed") for s in states)
    # Fourth and fifth passes: no send, refusal reason kept.
    for i in (3, 4):
        state = _run_pass(svc, f"ev-{i}", summary, f"2026-07-15T11:0{i}:00Z", sends)
        assert state["repeat_suppressed"] is True
        assert state["reason"] == "telemetry_refused_identical_event"
        assert "reason_detail" in state["error"]
        assert state["http_status"] == 422
        receipt = svc._lifecycle_append_receipt("b", state)
        assert receipt["refusal_count"] == 3
    assert sends.call_count == 3
    # After the retry window the identical event is probed once; acceptance resumes.
    accepted = {
        "status": "accepted", "terminal": True, "retryable": False, "outcome": "accepted",
        "http_status": 202, "response": {"status": "accepted"}, "error": None,
    }
    resumed = _run_pass(
        svc, "ev-9", summary, "2026-07-15T13:00:00Z", mock.Mock(return_value=accepted)
    )
    assert resumed["status"] == "accepted"
