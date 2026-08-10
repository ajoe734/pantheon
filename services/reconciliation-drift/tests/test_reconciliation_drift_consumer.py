from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]
FIXTURE_PATH = SERVICE_DIR / "fixtures" / "devloop-drift-telemetry-event.json"


def _capital_runtime_event() -> dict[str, object]:
    """The paper-only fill event emitted by the L12 Capital runtime boundary."""
    return {
        "event_id": "evt-l12-min-cap-runtime-001",
        "tenant_id": "tenant-l12",
        "event_type": "paper_fill_simulated",
        "created_at": "2026-08-10T03:15:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-l12-min-cap",
        "runtime_id": "runtime-l12-min-cap",
        "capital_pool_id": "pool-l12-paper",
        "artifact_id": "artifact-l12-paper",
        "artifact_version": "1.0.0",
        "plan_id": "plan-l12-min-dep",
        "persona_capital_binding_id": "pcb-l12-paper",
        "trace_id": "trace-l12-min-cap",
        "target": {"strategy_id": "strategy-l12-paper"},
        "metrics": {"fill_quantity": 2.0, "fill_price": 100.0},
        "metadata": {
            "runtime_package": "paper_execution_runtime",
            "ledger_committed": True,
            "is_real_order": False,
            "is_real_capital": False,
        },
    }


def _runtime_summary() -> dict[str, object]:
    return {
        "runtime_id": "runtime-real-001",
        "runtime_binding_id": "binding-real-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-real-001",
        "capital_pool_id": "pool-real-001",
        "persona_capital_binding_id": "pcb-real-001",
        "artifact_id": "artifact-real-001",
        "artifact_version": "1.0.0",
        "trace_id": "trace-real-001",
        "last_event_id": "evt-real-001",
        "last_heartbeat_event_id": "evt-heartbeat-real-001",
        "last_event_type": "pnl_snapshot",
        "last_event_at": "2026-07-15T01:00:00Z",
        "pnl": 12.5,
        "drawdown": 0.03,
        "queue_lag_ms": 5,
        "event_delivery_lag_ms": 8,
        "state": "active",
        "health_summary": {"telemetry": "ok", "paper_runtime": "ok"},
        "projection_source": "telemetry_ingest",
    }


def _load_consumer_module():
    sys.modules.pop("reconciliation_drift_consumer_test", None)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_consumer_test",
        SERVICE_DIR / "consumer.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_consumer_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_scheduler_module():
    sys.modules.pop("reconciliation_drift_scheduler_terminal_test", None)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_scheduler_terminal_test",
        SERVICE_DIR / "scheduler_worker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_scheduler_terminal_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_consume_test_main", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "reconciliation_drift_consume_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["reconciliation_drift_consume_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
                "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)


def test_telemetry_fixture_builds_canonical_drift_report() -> None:
    consumer = _load_consumer_module()

    events = consumer.load_telemetry_events_from_path(FIXTURE_PATH)
    assert len(events) == 1

    report = consumer.build_drift_report_from_event(events[0], existing_report_ids=set())

    assert report is not None
    assert report["drift_report_id"] == "drift-evt-devloop-reconcile-live-001"
    assert report["recon_run_id"] == "recon-evt-devloop-reconcile-live-001"
    assert report["drift_type"] == "slippage"
    assert report["scope_ref"] == "rtb-devloop-reconcile-001"
    assert report["baseline_ref"] == "paper-baseline-devloop-reconcile-001"
    assert report["current_ref"] == "live-window-devloop-reconcile-001"
    assert report["severity"] == "critical"
    assert report["status"] == "open"
    assert report["recommended_action"] == "open_incident"
    assert report["source_contract"]["telemetry_truth_owner"] == "telemetry-ingest"
    assert report["source_contract"]["emergency_control_chain_affected"] is False
    assert "telemetry_event:evt-devloop-reconcile-live-001" in report["evidence_refs"]
    assert set(report["metrics"]["breached_metric_ids"]) == {"avg_slippage_bps", "drawdown", "pnl"}


def test_drift_report_propagates_telemetry_trade_envelope() -> None:
    from services.trade_journey.correlation_envelope import mint_trade_envelope
    consumer = _load_consumer_module()
    event = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    event["correlation_envelope"] = mint_trade_envelope({"tenant_id": "tenant-1", "environment": "paper"}, producer="execution.paper_runtime")
    report = consumer.build_drift_report_from_event(event)
    assert report["correlation_envelope"]["journey_id"] == event["correlation_envelope"]["journey_id"]
    assert report["correlation_envelope"]["causation_event_id"] == event["correlation_envelope"]["event_id"]


def test_consume_endpoint_persists_drift_report_from_telemetry_fixture() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        module = _load_service_module(data_dir)
        client = TestClient(module.app)
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

        consumed = client.post(
            "/api/reconciliation-drift/telemetry-events/consume",
            json={"events": [fixture]},
        )
        assert consumed.status_code == 201, consumed.text
        payload = consumed.json()
        assert payload["consumed_event_count"] == 1
        assert payload["drift_report_count"] == 1
        assert payload["ignored_event_ids"] == []

        report = payload["drift_reports"][0]
        assert report["drift_report_id"] == "drift-evt-devloop-reconcile-live-001"
        assert report["deployment_stage"] == "live"
        assert report["binding_id"] == "rtb-devloop-reconcile-001"
        assert report["runtime_id"] == "runtime-devloop-reconcile-001"
        assert report["metrics"]["current_metrics"]["avg_slippage_bps"] == 4.8
        assert report["metrics"]["baseline_metrics"]["avg_slippage_bps"] == 2.0

        listed = client.get(
            "/api/reconciliation-drift/drift-reports",
            params={"scope_ref": "rtb-devloop-reconcile-001"},
        )
        assert listed.status_code == 200
        assert [item["drift_report_id"] for item in listed.json()] == [
            "drift-evt-devloop-reconcile-live-001"
        ]

        fetched = client.get("/api/reconciliation-drift/drift-reports/drift-evt-devloop-reconcile-live-001")
        assert fetched.status_code == 200
        assert fetched.json()["recommended_action"] == "open_incident"

        summary = client.get("/api/reconciliation-drift/summary", params={"binding_id": "rtb-devloop-reconcile-001"})
        assert summary.status_code == 200
        assert summary.json()["drift_report_count"] == 1

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["drift_report_count"] == 1


def test_capital_runtime_event_persists_terminal_incident_for_evolution() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        module = _load_service_module(data_dir)
        client = TestClient(module.app)
        event = _capital_runtime_event()

        with mock.patch.object(
            module,
            "_classify_drift_report_incident",
            return_value={"incident_id": "inc-l12-min-telrec-001"},
        ) as classify:
            consumed = client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                json={
                    "tenant_id": "tenant-l12",
                    "events": [event],
                    "baseline_metrics": {"fill_quantity": 1.0},
                    "thresholds": {
                        "fill_quantity": {
                            "warning_relative_delta": 0.2,
                            "critical_relative_delta": 0.5,
                        }
                    },
                },
                headers={"X-Tenant-Id": "tenant-l12"},
            )

        assert consumed.status_code == 201, consumed.text
        payload = consumed.json()
        assert payload["drift_report_count"] == 1
        assert payload["incident_case_count"] == 1
        assert payload["incident_cases"] == [{"incident_id": "inc-l12-min-telrec-001"}]
        report = payload["drift_reports"][0]
        assert report["drift_report_id"] == "drift-evt-l12-min-cap-runtime-001"
        assert report["telemetry_event_ids"] == [event["event_id"]]
        assert report["metrics"]["breached_metric_ids"] == ["fill_quantity"]
        assert event["metadata"]["is_real_capital"] is False
        classify.assert_called_once_with(report)
        persisted = module.store.get_drift_report(report["drift_report_id"], tenant_id="tenant-l12")
        assert persisted == report


def test_scheduler_exposes_terminal_incident_id_for_evolution() -> None:
    scheduler = _load_scheduler_module()

    result = scheduler._decorate_result(
        {
            "status": "critical",
            "incident_ids": ["inc-l12-min-telrec-001", "inc-l12-min-telrec-001"],
        },
        tick_id="tick-l12-min-telrec-001",
        controller_status="degraded",
        attempts=[{"status": "ok", "at": "2026-08-10T03:15:01Z"}],
        errors=[],
        max_attempts=1,
        retry_backoff_seconds=0.0,
    )

    assert result["terminal_incident_ids"] == ["inc-l12-min-telrec-001"]
    assert result["terminal_incident_id"] == "inc-l12-min-telrec-001"


def test_runtime_summary_conversion_requires_real_identity_and_metrics() -> None:
    consumer = _load_consumer_module()

    event = consumer.runtime_summary_to_event(_runtime_summary())

    assert event["event_id"] == "evt-real-001"
    assert event["binding_id"] == "binding-real-001"
    assert event["trace_id"] == "trace-real-001"
    assert event["metrics"] == {
        "drawdown": 0.03,
        "event_delivery_lag_ms": 8.0,
        "pnl": 12.5,
        "queue_lag_ms": 5.0,
    }
    assert event["source_contract"]["synthetic"] is False

    incomplete = _runtime_summary()
    incomplete.pop("trace_id")
    with pytest.raises(ValueError, match="trace_id"):
        consumer.runtime_summary_to_event(incomplete)

    without_actuals = _runtime_summary()
    for key in ("pnl", "drawdown", "queue_lag_ms", "event_delivery_lag_ms"):
        without_actuals.pop(key)
    with pytest.raises(ValueError, match="no authoritative numeric"):
        consumer.runtime_summary_to_event(without_actuals)


def test_real_consumer_empty_source_is_degraded_not_green() -> None:
    consumer = _load_consumer_module()
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[]), mock.patch.object(
            consumer, "post_events"
        ) as post:
            result = consumer.run_runtime_summary_consumer_once(
                service_url="http://reconciliation-drift-svc:8102",
                telemetry_url="http://telemetry:8083",
                state=state,
                now_fn=lambda: datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
            )

    assert result["status"] == "degraded"
    assert result["summary_count"] == 0
    assert result["controller_status"] == "degraded"
    post.assert_not_called()


def test_real_consumer_dlq_replay_and_restart_state_are_durable() -> None:
    consumer = _load_consumer_module()
    with tempfile.TemporaryDirectory() as data_dir:
        state_path = Path(data_dir) / "consumer-state.json"
        state = consumer.ConsumerWorkerState(state_path)
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer,
            "post_events",
            side_effect=RuntimeError("incident chain unavailable"),
        ) as post:
            failed = consumer.run_runtime_summary_consumer_once(
                service_url="http://reconciliation-drift-svc:8102",
                telemetry_url="http://telemetry:8083",
                state=state,
                max_attempts=2,
                now_fn=lambda: datetime(2026, 7, 15, 1, 1, tzinfo=timezone.utc),
            )

        assert post.call_count == 2
        assert failed["status"] == "degraded"
        assert failed["dead_letter_count"] == 1
        assert failed["backlog_count"] == 1

        reloaded = consumer.ConsumerWorkerState(state_path)
        assert set(reloaded.dead_letters) == {"evt-real-001"}
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer,
            "post_events",
            return_value={
                "drift_report_count": 1,
                "incident_case_count": 1,
                "incident_cases": [{"incident_id": "inc-l12-consumer-001"}],
            },
        ):
            replayed = consumer.run_runtime_summary_consumer_once(
                service_url="http://reconciliation-drift-svc:8102",
                telemetry_url="http://telemetry:8083",
                state=reloaded,
                max_attempts=2,
                replay_dead_letters=True,
                now_fn=lambda: datetime(2026, 7, 15, 1, 2, tzinfo=timezone.utc),
            )

        assert replayed["status"] == "ok"
        assert replayed["replayed_dead_letter_count"] == 1
        assert replayed["delivered_event_count"] == 1
        assert replayed["incident_case_count"] == 1
        assert replayed["terminal_incident_ids"] == ["inc-l12-consumer-001"]
        assert replayed["terminal_incident_id"] == "inc-l12-consumer-001"
        assert replayed["backlog_count"] == 0
        completed = consumer.ConsumerWorkerState(state_path).completed["evt-real-001"]
        assert completed["attempt_count"] == 1
        assert completed["terminal_incident_ids"] == ["inc-l12-consumer-001"]
