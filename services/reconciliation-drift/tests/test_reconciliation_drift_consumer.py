from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


def test_consumer_survives_read_timeout_then_delivers(monkeypatch) -> None:
    import http.server
    import threading
    import time

    consumer = _load_consumer_module()
    calls: list[int] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            calls.append(1)
            if len(calls) == 1:
                time.sleep(1.5)  # longer than the client timeout
            body = b'{"status": "ok", "drift_report_count": 1}'
            try:
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("RECONCILIATION_DRIFT_CONSUMER_POST_TIMEOUT_SECONDS", "0.3")
    try:
        with tempfile.TemporaryDirectory() as data_dir:
            state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
            with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]):
                result = consumer.run_runtime_summary_consumer_once(
                    service_url=f"http://127.0.0.1:{server.server_port}",
                    telemetry_url="http://telemetry:8083",
                    state=state,
                    max_attempts=3,
                    now_fn=lambda: datetime(2026, 7, 15, 1, 1, tzinfo=timezone.utc),
                )
    finally:
        server.shutdown()
    assert len(calls) == 2
    assert result["delivered_event_count"] == 1
    assert not state.pending


def test_post_events_non_transient_error_still_raises() -> None:
    consumer = _load_consumer_module()
    with mock.patch.object(consumer.urllib.request, "urlopen", side_effect=ValueError("boom")):
        with pytest.raises(ValueError):
            consumer.post_events("http://x", [{"event_id": "e"}])


def test_tick_budget_bounds_slow_downstream_and_drains_across_ticks(monkeypatch) -> None:
    import http.server
    import threading
    import time

    consumer = _load_consumer_module()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(0.4)
            body = b'{"status": "ok", "drift_report_count": 1}'
            self.send_response(201)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("RECONCILIATION_DRIFT_CONSUMER_POST_TIMEOUT_SECONDS", "2")
    summaries = []
    for index in range(4):
        summary = _runtime_summary()
        summary["runtime_id"] = f"runtime-{index}"
        summary["last_event_id"] = f"evt-{index}"
        summaries.append(summary)
    try:
        with tempfile.TemporaryDirectory() as data_dir:
            state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
            ticks = []
            with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=summaries):
                for _ in range(6):
                    started = time.monotonic()
                    result = consumer.run_runtime_summary_consumer_once(
                        service_url=f"http://127.0.0.1:{server.server_port}",
                        telemetry_url="http://telemetry:8083",
                        state=state,
                        tick_budget_seconds=0.5,
                        now_fn=lambda: datetime(2026, 7, 15, 1, 1, tzinfo=timezone.utc),
                    )
                    ticks.append((time.monotonic() - started, result))
                    if not state.pending:
                        break
    finally:
        server.shutdown()
    assert not state.pending
    assert len(ticks) >= 2  # backlog drained across ticks, not in one
    assert all(elapsed < 0.5 + 2 for elapsed, _ in ticks)  # budget plus one post
    assert not state.dead_letters


def _run_once(consumer, state, at, **kwargs):
    return consumer.run_runtime_summary_consumer_once(
        service_url="http://reconciliation-drift-svc:8102",
        telemetry_url="http://telemetry:8083",
        state=state,
        now_fn=lambda: at,
        **kwargs,
    )


def test_traceless_paused_summary_is_ineligible_not_invalid() -> None:
    consumer = _load_consumer_module()
    paused = _runtime_summary()
    paused.pop("trace_id")
    paused["state"] = "paused"
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[paused]), mock.patch.object(
            consumer, "post_events"
        ) as post:
            result = _run_once(consumer, state, datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc))
    assert result["invalid_summary_count"] == 0
    assert result["ineligible_summary_count"] == 1
    assert result["status"] == "ok"
    assert result["last_success_at"] == "2026-10-17T01:00:00Z"
    post.assert_not_called()


def test_retired_binding_summary_is_ineligible(monkeypatch) -> None:
    consumer = _load_consumer_module()
    retired = _runtime_summary()
    retired["state"] = "retired"
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager")
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[retired]), mock.patch.object(
            consumer, "_lookup_runtime_binding", return_value={"status": "retired"}
        ), mock.patch.object(consumer, "post_events") as post:
            result = _run_once(consumer, state, datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc))
    assert result["invalid_summary_count"] == 0
    assert result["ineligible_summaries"][0]["reason"] == "binding_retired"
    assert result["status"] == "ok"
    post.assert_not_called()


def _dead_letter_record(binding_id: str, event_id: str) -> dict[str, object]:
    return {
        "event": {"event_id": event_id, "binding_id": binding_id, "created_at": "2026-10-10T00:00:00Z"},
        "attempt_count": 3,
        "first_seen_at": "2026-10-10T00:00:00Z",
        "last_error": "boom",
    }


def test_retired_binding_dead_letters_and_pending_are_purged_once_per_binding() -> None:
    consumer = _load_consumer_module()
    bindings = {
        "b-retired": {"status": "retired"},
        "b-paused": {"status": "paused"},
        "b-fail": RuntimeError("down"),
    }
    calls: list[str] = []

    def lookup(binding_id, **_kw):
        calls.append(binding_id)
        result = bindings[binding_id]
        if isinstance(result, Exception):
            raise result
        return result

    with tempfile.TemporaryDirectory() as data_dir:
        path = Path(data_dir) / "state.json"
        state = consumer.ConsumerWorkerState(path)
        state.dead_letters["k1"] = _dead_letter_record("b-retired", "e1")
        state.dead_letters["k2"] = _dead_letter_record("b-retired", "e2")
        state.dead_letters["k3"] = _dead_letter_record("b-paused", "e3")
        state.pending["k4"] = _dead_letter_record("b-retired", "e4")
        path.write_text(json.dumps({"version": consumer.CONSUMER_STATE_VERSION, "pending": state.pending, "dead_letters": state.dead_letters, "completed": {}}), encoding="utf-8")
        state.reload()
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[]), mock.patch.object(
            consumer, "_lookup_runtime_binding", side_effect=lookup
        ), mock.patch.object(consumer, "post_events", return_value={}):
            result = _run_once(consumer, state, datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc))
        saved = json.loads(path.read_text(encoding="utf-8"))
    assert result["retired_purged_count"] == 3
    assert result["retired_purged_binding_ids"] == ["b-retired"]
    assert sorted(item["from"] for item in result["retired_purged"]) == ["dead_letter", "dead_letter", "pending"]
    assert list(state.dead_letters) == ["k3"]
    assert saved["last_retired_purge"]["binding_ids"] == ["b-retired"]
    assert saved["last_retired_purge"]["count"] == 3
    assert calls.count("b-retired") == 1
    assert calls.count("b-paused") == 1


def test_lookup_failure_keeps_dead_letters_and_stops_lookups() -> None:
    consumer = _load_consumer_module()
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        state.dead_letters["k1"] = _dead_letter_record("b1", "e1")
        state.dead_letters["k2"] = _dead_letter_record("b2", "e2")
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[]), mock.patch.object(
            consumer, "_lookup_runtime_binding", side_effect=RuntimeError("down")
        ) as lookup, mock.patch.object(consumer, "post_events", return_value={}):
            result = _run_once(consumer, state, datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc))
    assert result["retired_purged_count"] == 0
    assert set(state.dead_letters) == {"k1", "k2"}
    assert lookup.call_count == 1


def test_dns_failure_stays_pending_then_recovers_with_backoff() -> None:
    consumer = _load_consumer_module()
    err = consumer.TransientDeliveryError(
        "reconciliation-drift service unavailable: [Errno -3] Temporary failure in name resolution"
    )
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        t0 = datetime(2026, 10, 17, 2, 13, tzinfo=timezone.utc)
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer, "post_events", side_effect=err
        ) as post:
            first = _run_once(consumer, state, t0, max_attempts=3)
            assert post.call_count == 3
            assert first["dead_letter_count"] == 0
            assert first["pending_count"] == 1
            assert first["transient_failure_count"] == 1
            assert first["last_success_at"] is None
            # still inside the backoff window: no new attempt
            _run_once(consumer, state, t0 + timedelta(seconds=5), max_attempts=3)
            assert post.call_count == 3
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer, "post_events", return_value={"drift_report_count": 0}
        ):
            healed = _run_once(consumer, state, t0 + timedelta(minutes=2), max_attempts=3)
    assert healed["delivered_event_count"] == 1
    assert healed["pending_count"] == 0
    assert healed["dead_letter_count"] == 0
    assert healed["last_success_at"] is not None


def test_transient_failure_dead_letters_after_max_age() -> None:
    consumer = _load_consumer_module()
    err = consumer.TransientDeliveryError("timeout")
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        t0 = datetime(2026, 10, 17, 2, 13, tzinfo=timezone.utc)
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer, "post_events", side_effect=err
        ):
            _run_once(consumer, state, t0, max_attempts=1)
            late = _run_once(consumer, state, t0 + timedelta(days=2), max_attempts=1)
    assert late["dead_letter_count"] == 1


def test_completed_trim_is_by_time_not_key_order_and_keeps_current_event() -> None:
    consumer = _load_consumer_module()
    with tempfile.TemporaryDirectory() as data_dir:
        path = Path(data_dir) / "state.json"
        state = consumer.ConsumerWorkerState(path)
        assert state.acquire_lease(
            worker_id="w", lease_seconds=60, now=datetime(2026, 10, 17, tzinfo=timezone.utc)
        )
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i in range(consumer.COMPLETED_RETENTION + 50):
            stamp = (base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            state.completed[f"k{i:05d}"] = {"completed_at": stamp}
        current = consumer.ConsumerWorkerState.event_key({"event_id": "evt-real-001"})
        state.completed[current] = {"completed_at": "2026-09-01T00:00:00Z"}  # oldest
        state.save()
        reloaded = consumer.ConsumerWorkerState(path)  # sorted keys on disk
        reloaded.trim_completed(protected=[current])
    assert len(reloaded.completed) == consumer.COMPLETED_RETENTION
    assert current in reloaded.completed
    assert "k00000" not in reloaded.completed  # oldest unprotected evicted
    newest = f"k{consumer.COMPLETED_RETENTION + 49:05d}"
    assert newest in reloaded.completed


def test_last_success_recorded_with_old_dead_letters_present() -> None:
    consumer = _load_consumer_module()
    with tempfile.TemporaryDirectory() as data_dir:
        state = consumer.ConsumerWorkerState(Path(data_dir) / "state.json")
        state.dead_letters["old"] = {"event": {"event_id": "old"}, "first_seen_at": "2026-10-06T20:54:00Z"}
        with mock.patch.object(consumer, "fetch_runtime_summaries", return_value=[_runtime_summary()]), mock.patch.object(
            consumer, "post_events", return_value={"drift_report_count": 0}
        ):
            result = _run_once(consumer, state, datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc))
    assert result["delivered_event_count"] == 1
    assert result["last_success_at"] == "2026-10-17T03:00:00Z"
    assert result["dead_letter_count"] == 1
