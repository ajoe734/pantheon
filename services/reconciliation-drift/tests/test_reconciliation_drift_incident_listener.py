from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]


def _load_service_module(data_dir: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_incident_test_main", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "reconciliation_drift_incident_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["reconciliation_drift_incident_test_main"] = module
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


def _load_listener_module():
    sys.modules.pop("reconciliation_drift_incident_listener_test", None)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_incident_listener_test",
        SERVICE_DIR / "incident_listener.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_incident_listener_test"] = module
    spec.loader.exec_module(module)
    return module


def _incident_payload() -> dict[str, object]:
    return {
        "incident_id": "inc-heartbeat-loss-001",
        "title": "Runtime heartbeat lost",
        "status": "open",
        "severity": "high",
        "binding_id": "rtb-heartbeat-loss-001",
        "runtime_id": "runtime-heartbeat-loss-001",
        "deployment_stage": "paper",
        "telemetry_event_ids": ["evt-heartbeat-loss-001"],
        "evidence_summary": "heartbeat_loss detected from telemetry runtime summary",
    }


def test_incident_trigger_creates_reconciliation_evaluation() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        resp = client.post(
            "/api/reconciliation-drift/incident-triggers/consume",
            json={"incident": _incident_payload()},
        )

        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["created"] is True
        assert payload["trigger"] == "incident"
        assert payload["incident_id"] == "inc-heartbeat-loss-001"
        assert payload["source_event_id"] == "evt-heartbeat-loss-001"
        assert payload["reason"] == "Runtime heartbeat lost"

        listed = client.get(
            "/api/reconciliation-drift/evaluations",
            params={"binding_id": "rtb-heartbeat-loss-001"},
        )
        assert listed.status_code == 200
        evaluations = listed.json()
        assert len(evaluations) == 1
        evaluation = evaluations[0]
        assert evaluation["trigger"] == "incident"
        assert evaluation["status"] == "critical"
        assert evaluation["trigger_reason"] == "Runtime heartbeat lost"
        assert evaluation["incident_id"] == "inc-heartbeat-loss-001"
        assert evaluation["source_event_id"] == "evt-heartbeat-loss-001"
        assert evaluation["telemetry_event_ids"] == ["evt-heartbeat-loss-001"]
        check = evaluation["reconciliation_checks"][0]
        assert check["check"] == "incident_trigger_received"
        assert check["reason"] == "Runtime heartbeat lost"
        assert check["source_event_id"] == "evt-heartbeat-loss-001"


def test_incident_trigger_is_idempotent_for_duplicate_anomaly_event() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        body = {"incident": _incident_payload()}

        first = client.post("/api/reconciliation-drift/incident-triggers/consume", json=body)
        second = client.post("/api/reconciliation-drift/incident-triggers/consume", json=body)

        assert first.status_code == 201
        assert first.json()["created"] is True
        assert second.status_code == 201
        assert second.json()["created"] is False
        assert second.json()["skipped"] is True
        assert second.json()["evaluation_id"] == first.json()["evaluation_id"]

        listed = client.get(
            "/api/reconciliation-drift/evaluations",
            params={"binding_id": "rtb-heartbeat-loss-001"},
        )
        assert len(listed.json()) == 1


def test_incident_listener_tick_triggers_reconciliation_without_manual_scheduled_post() -> None:
    listener = _load_listener_module()
    incident = _incident_payload()

    with mock.patch.object(listener, "fetch_open_incidents", return_value=[incident]) as fetch, mock.patch.object(
        listener,
        "post_incident_trigger",
        return_value={"created": True, "evaluation_id": "rdeval-incident-inc-heartbeat-loss-001"},
    ) as post:
        result = listener.run_tick(
            incidents_url="http://incidents:8090",
            reconciliation_url="http://reconciliation-drift-svc:8102",
        )

    assert result["status"] == "ok"
    assert result["fetched_incident_count"] == 1
    assert result["triggered_incident_count"] == 1
    assert result["terminal_incident_ids"] == ["inc-heartbeat-loss-001"]
    assert result["terminal_incident_id"] == "inc-heartbeat-loss-001"
    fetch.assert_called_once_with(incidents_url="http://incidents:8090", timeout_seconds=30.0)
    post.assert_called_once_with(
        reconciliation_url="http://reconciliation-drift-svc:8102",
        incident=incident,
        timeout_seconds=30.0,
    )


def test_infrastructure_incident_returns_fast_not_applicable() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        incident = {
            "incident_id": "infra-bff-down-001",
            "title": "BFF down",
            "status": "open",
            "severity": "high",
            "binding_id": "infra-subject-default-control-plane-bff-incidents",
            "evidence_summary": "non_trading_infrastructure_incident=true; tenant_id=default",
        }

        resp = client.post(
            "/api/reconciliation-drift/incident-triggers/consume",
            json={"incident": incident},
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["created"] is False
        assert payload["skipped"] is True
        assert payload["not_applicable"] is True
        assert payload["reason"] == "non_trading_infrastructure_incident"

        listed = client.get("/api/reconciliation-drift/evaluations")
        assert listed.status_code == 200
        assert len(listed.json()) == 0


def test_incident_listener_acknowledges_infrastructure_incident_without_downstream_delivery() -> None:
    listener = _load_listener_module()
    incident = {
        "incident_id": "infra-bff-001",
        "title": "BFF down",
        "status": "open",
        "severity": "high",
        "binding_id": "infra-subject-default-control-plane-bff-incidents",
        "evidence_summary": "non_trading_infrastructure_incident=true",
    }

    with mock.patch.object(listener, "fetch_open_incidents", return_value=[incident]) as fetch, mock.patch.object(
        listener,
        "post_incident_trigger",
    ) as post:
        result = listener.run_tick(
            incidents_url="http://incidents:8090",
            reconciliation_url="http://reconciliation-drift-svc:8102",
        )

    assert result["status"] == "ok"
    assert result["fetched_incident_count"] == 1
    assert result["triggered_incident_count"] == 1
    assert result["results"][0]["acknowledged"] is True
    assert result["results"][0]["result"]["not_applicable"] is True
    post.assert_not_called()


def test_backlog_of_18_infrastructure_incidents_finishes_within_budget_and_health_becomes_ready(tmp_path: Path) -> None:
    listener = _load_listener_module()
    state_file = tmp_path / "listener-state.json"
    health_file = tmp_path / "listener-health.json"

    backlog = {}
    for i in range(18):
        ident = f"infra-incident-{i:03d}"
        backlog[ident] = {
            "identity": ident,
            "incident": {
                "incident_id": ident,
                "title": f"BFF degradation {i}",
                "status": "open",
                "severity": "high",
                "binding_id": "infra-subject-default-control-plane-bff-incidents",
                "evidence_summary": "non_trading_infrastructure_incident=true; tenant_id=default",
            },
            "first_failed_at": "2026-10-07T22:50:02Z",
            "last_failed_at": "2026-10-08T03:12:58Z",
            "last_error": "[Errno -3] Temporary failure in name resolution",
            "attempt_count": 21,
        }
    state_file.write_text(json.dumps({"version": 1, "backlog": backlog}), encoding="utf-8")

    state = listener.IncidentListenerState(state_file)
    assert len(state.backlog) == 18

    def _slow_reconcile_stub(**kwargs):
        time.sleep(0.5)
        raise RuntimeError("Slow stub should not be invoked for infrastructure incidents")

    env = {
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE": str(health_file),
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH": str(state_file),
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_TICK_BUDGET_SECONDS": "2.0",
    }

    started = time.monotonic()
    with mock.patch.dict("os.environ", env), mock.patch.object(
        listener,
        "fetch_open_incidents",
        return_value=[],
    ), mock.patch.object(
        listener,
        "post_incident_trigger",
        side_effect=_slow_reconcile_stub,
    ) as post_mock:
        exit_code = listener.main()

    elapsed = time.monotonic() - started
    assert exit_code == 0
    assert elapsed < 2.0
    post_mock.assert_not_called()

    state.reload()
    assert len(state.backlog) == 0

    health_payload = json.loads(health_file.read_text(encoding="utf-8"))
    assert health_payload["status"] == "ok"
    assert health_payload["ticks"] == 1
    assert health_payload["backlog_count"] == 0
    assert health_payload["last_progress_at"] is not None
    with mock.patch.dict("os.environ", env):
        assert listener.healthcheck() == 0


def test_tick_budget_bounds_slow_downstream_and_progress_is_reported(tmp_path: Path) -> None:
    listener = _load_listener_module()
    state_file = tmp_path / "listener-state.json"
    health_file = tmp_path / "listener-health.json"

    backlog = {}
    for i in range(10):
        ident = f"trading-incident-{i:03d}"
        backlog[ident] = {
            "identity": ident,
            "incident": {
                "incident_id": ident,
                "title": f"Order rejection {i}",
                "status": "open",
                "severity": "high",
                "binding_id": f"rtb-trading-{i:03d}",
                "evidence_summary": "trading incident",
            },
            "first_failed_at": "2026-10-07T22:50:02Z",
            "attempt_count": 1,
        }
    state_file.write_text(json.dumps({"version": 1, "backlog": backlog}), encoding="utf-8")

    def _slow_post(**kwargs):
        time.sleep(0.15)
        return {"status": "ok", "created": True, "evaluation_id": "eval-1"}

    env = {
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE": str(health_file),
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH": str(state_file),
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_TICK_BUDGET_SECONDS": "0.3",
    }

    started = time.monotonic()
    with mock.patch.dict("os.environ", env), mock.patch.object(
        listener, "fetch_open_incidents", return_value=[]
    ), mock.patch.object(
        listener, "post_incident_trigger", side_effect=_slow_post
    ):
        exit_code = listener.main()

    elapsed = time.monotonic() - started
    assert exit_code == 0
    assert elapsed < 0.3 + 0.25

    state = listener.IncidentListenerState(state_file)
    assert 0 < len(state.backlog) < 10

    health_payload = json.loads(health_file.read_text(encoding="utf-8"))
    assert health_payload["status"] == "ok"
    assert health_payload["ticks"] == 1
    assert health_payload["backlog_count"] == len(state.backlog)
    assert health_payload["last_progress_at"] is not None
    with mock.patch.dict("os.environ", env):
        assert listener.healthcheck() == 0


def test_unchanged_incident_is_not_reposted_across_ticks_and_restart(tmp_path: Path) -> None:
    listener = _load_listener_module()
    state_path = tmp_path / "state.json"
    incident = {**_incident_payload(), "updated_at": "2026-10-17T00:00:00Z"}
    infra = {
        "incident_id": "inc-infra-1",
        "binding_id": "infra-subject-1",
        "updated_at": "2026-10-17T00:00:00Z",
    }
    kwargs = {
        "incidents_url": "http://incidents:8090",
        "reconciliation_url": "http://reconciliation-drift-svc:8102",
        "state_path": state_path,
    }
    ack = {"created": True, "evaluation_id": "rdeval-1"}
    with mock.patch.object(
        listener, "fetch_open_incidents", return_value=[incident, infra]
    ), mock.patch.object(listener, "post_incident_trigger", return_value=ack) as post:
        first = listener.run_tick(**kwargs)
        assert post.call_count == 1
        second = listener.run_tick(**kwargs)
        assert post.call_count == 1  # second tick posts nothing
        # restart: fresh state object reloads the persisted record
        third = listener.run_tick(**kwargs)
        assert post.call_count == 1
        changed = {**incident, "updated_at": "2026-10-17T00:05:00Z"}
        with mock.patch.object(
            listener, "fetch_open_incidents", return_value=[changed, infra]
        ):
            fourth = listener.run_tick(**kwargs)
            assert post.call_count == 2  # evidence change posts exactly once
            listener.run_tick(**kwargs)
            assert post.call_count == 2
    assert second["unchanged_incident_count"] == 1
    assert third["unchanged_incident_count"] == 1
    # infrastructure acknowledgement still reported every tick, never posted
    assert any(r.get("acknowledged") for r in second["results"])
    assert first["triggered_incident_count"] == 2
    assert fourth["triggered_incident_count"] == 2


def test_delivered_record_drops_incidents_no_longer_open(tmp_path: Path) -> None:
    listener = _load_listener_module()
    state_path = tmp_path / "state.json"
    incident = {**_incident_payload(), "updated_at": "t1"}
    kwargs = {
        "incidents_url": "http://i",
        "reconciliation_url": "http://r",
        "state_path": state_path,
    }
    with mock.patch.object(
        listener, "post_incident_trigger", return_value={"created": True}
    ):
        with mock.patch.object(listener, "fetch_open_incidents", return_value=[incident]):
            listener.run_tick(**kwargs)
        assert json.loads(state_path.read_text())["delivered"]
        with mock.patch.object(listener, "fetch_open_incidents", return_value=[]):
            listener.run_tick(**kwargs)
    assert json.loads(state_path.read_text())["delivered"] == {}


def _fill_event(event_id: str, order_id: str, created_at: str, *, qty: float = 1.0, status: str = "filled", **overrides):
    event = {
        "event_id": event_id,
        "created_at": created_at,
        "tenant_id": "tenant-a",
        "binding_id": "rb-dup",
        "runtime_id": "rt-dup",
        "artifact_id": "art-1",
        "artifact_version": "v1",
        "plan_id": "plan-1",
        "capital_pool_id": "pool-1",
        "signal_id": "sig-1",
        "run_id": "run-1",
        "deployment_stage": "paper",
        "persona_capital_binding_id": "pcb-1",
        "trace_id": f"trace-{event_id}",
        "order_id": order_id,
        "symbol": "AAPL",
        "metrics": {"action": "SELL", "fill_quantity": -qty, "submitted_to_broker": True},
        "metadata": {
            "requested_quantity": 1.0,
            "broker_submission_status": status,
            "ledger_committed": True,
            "broker_order_id": order_id,
            "market_price_as_of": "2026-10-23T20:00:00Z",
        },
    }
    event.update(overrides)
    return event


def _post_duplicate_trigger(client, svc, events, incident_id="inc-dup-001"):
    by_id = {event["event_id"]: event for event in events}
    with mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry", "PANTHEON_TENANT_ID": "tenant-a"}), mock.patch(
        "telemetry_client.fetch_accepted_event",
        side_effect=lambda url, event_id, **kwargs: by_id.get(event_id),
    ):
        return client.post(
            "/api/reconciliation-drift/incident-triggers/consume",
            json={
                "incident": {
                    "incident_id": incident_id,
                    "binding_id": "rb-dup",
                    "runtime_id": "rt-dup",
                    "severity": "medium",
                    "telemetry_event_ids": list(by_id),
                },
            },
        )


def _reports(svc):
    return [r for r in svc.store.list_drift_reports() if r.get("tenant_id") == "tenant-a"]


def _duplicate_check(client):
    with mock.patch.dict("os.environ", {"PANTHEON_TENANT_ID": "tenant-a"}):
        evaluation = client.get(
            "/api/reconciliation-drift/evaluations", params={"binding_id": "rb-dup"}
        ).json()[0]
    check = next(
        c for c in evaluation["reconciliation_checks"] if c["check"] == "duplicate_execution_invariant"
    )
    return evaluation, check


def test_two_distinct_full_orders_for_one_decision_store_duplicate_report_and_replay_is_idempotent() -> None:
    events = [
        _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
        _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z"),
    ]
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        first = _post_duplicate_trigger(client, svc, events)
        second = _post_duplicate_trigger(client, svc, events)
        assert first.status_code == 201 and first.json()["created"] is True
        assert second.json()["skipped"] is True

        evaluation, check = _duplicate_check(client)
        assert evaluation["status"] == "critical"
        assert check["duplicate_detected"] is True and check["measurement"] == "measured"
        assert check["requested_quantity"] == 1.0 and check["committed_quantity"] == 2.0
        assert check["occurrence_window"] == {
            "first_created_at": "2026-10-24T09:50:13Z",
            "last_created_at": "2026-10-25T09:51:15Z",
        }
        reports = _reports(svc)
        assert len(reports) == 1
        report = reports[0]
        assert report["telemetry_event_ids"] == ["evt-b"]
        assert report["duplicate_event_ids"] == ["evt-a", "evt-b"]
        assert report["predecessor_incident_id"] == "inc-dup-001"
        assert "incident:inc-dup-001" in report["evidence_refs"]
        assert evaluation["drift_report_ids"] == [report["drift_report_id"]]
        assert "threshold_metric" not in json.dumps(report)


def test_partial_fills_same_order_and_replayed_order_are_not_duplicates() -> None:
    cases = {
        "partial": [
            _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z", qty=0.4, status="partially_filled"),
            _fill_event("evt-b", "ord-1", "2026-10-24T09:50:14Z", qty=0.6),
        ],
        "replay": [
            _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
            _fill_event("evt-b", "ord-1", "2026-10-24T09:50:13Z", **{"metadata": {**_fill_event("x", "ord-1", "t")["metadata"], "fill_id": "f-1"}}),
        ],
        "different_bar": [
            _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
            _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", **{"metadata": {**_fill_event("x", "ord-2", "t")["metadata"], "market_price_as_of": "2026-10-24T20:00:00Z"}}),
        ],
        "different_signal": [
            _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
            _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", signal_id="sig-2"),
        ],
        "reversed": [
            _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
            _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", **{"metadata": {**_fill_event("x", "ord-2", "t")["metadata"], "reverses_event_id": "evt-a"}}),
        ],
    }
    for name, events in cases.items():
        with tempfile.TemporaryDirectory() as data_dir:
            svc = _load_service_module(data_dir)
            client = TestClient(svc.app)
            assert _post_duplicate_trigger(client, svc, events).status_code == 201, name
            _, check = _duplicate_check(client)
            assert check["status"] != "critical" and not check.get("duplicate_detected"), name
            assert _reports(svc) == [], name


def test_unresolved_or_cross_tenant_evidence_is_unmeasured_not_healthy() -> None:
    events = [
        _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
        _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", tenant_id="tenant-b"),
    ]
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        _post_duplicate_trigger(client, svc, events)
        evaluation, check = _duplicate_check(client)
        assert check["measurement"] == "unmeasured"
        assert check["status"] == "degraded"
        assert "duplicate_detected" not in check
        assert _reports(svc) == []


def _two_orders():
    return [
        _fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
        _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z"),
    ]


def test_duplicate_report_is_consumable_by_incident_owner_and_delivery_retries() -> None:
    from services.incidents.consumer import build_incident_from_drift_report

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app, raise_server_exceptions=False)
        env = {"PANTHEON_INCIDENTS_API_URL": "http://incidents"}
        with mock.patch.dict("os.environ", env), mock.patch.object(
            svc, "_post_json", side_effect=svc.HTTPException(status_code=502, detail="down")
        ):
            failed = _post_duplicate_trigger(client, svc, _two_orders())
        assert failed.status_code == 502
        assert len(_reports(svc)) == 1  # durable report survives, evaluation not complete
        delivered = []
        with mock.patch.dict("os.environ", env), mock.patch.object(
            svc, "_post_json", side_effect=lambda url, payload, **kw: delivered.append(payload) or {"incident_id": "inc-x"}
        ):
            ok = _post_duplicate_trigger(client, svc, _two_orders())
            again = _post_duplicate_trigger(client, svc, _two_orders())
        assert ok.status_code == 201 and again.json()["skipped"] is True
        assert len(delivered) == 1 and len(_reports(svc)) == 1
        report = delivered[0]["drift_report"]
        case = build_incident_from_drift_report(report)
        assert case.telemetry_event_ids == ["evt-b"]
        assert report["duplicate_event_ids"] == ["evt-a", "evt-b"]
        assert report["predecessor_incident_id"] == "inc-dup-001"
        assert (case.deployment_stage, case.persona_capital_binding_id) == ("paper", "pcb-1")
        assert case.trace_id == "trace-evt-b"
        assert _reports(svc)[0]["handoff_state"] == "delivered"


def test_telemetry_timeout_is_retryable_and_converges_after_recovery() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app, raise_server_exceptions=False)
        with mock.patch.dict(
            "os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry", "PANTHEON_TENANT_ID": "tenant-a"}
        ), mock.patch("telemetry_client.fetch_accepted_event", side_effect=TimeoutError("t")):
            resp = client.post(
                "/api/reconciliation-drift/incident-triggers/consume",
                json={"incident": {"incident_id": "inc-dup-001", "binding_id": "rb-dup",
                                   "runtime_id": "rt-dup", "telemetry_event_ids": ["evt-a", "evt-b"]}},
            )
        assert resp.status_code == 503
        assert _reports(svc) == []
        recovered = _post_duplicate_trigger(client, svc, _two_orders())
        assert recovered.status_code == 201 and recovered.json()["created"] is True
        assert len(_reports(svc)) == 1


def test_missing_ledger_commit_and_plan_scope_are_not_silently_measured() -> None:
    def meta_without_ledger():
        meta = dict(_fill_event("x", "ord-2", "t")["metadata"])
        del meta["ledger_committed"]
        return {"metadata": meta}

    cases = {
        "no_ledger": [_fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
                      _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", **meta_without_ledger())],
        "no_plan": [_fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
                    _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", plan_id="")],
    }
    for name, events in cases.items():
        with tempfile.TemporaryDirectory() as data_dir:
            svc = _load_service_module(data_dir)
            client = TestClient(svc.app)
            assert _post_duplicate_trigger(client, svc, events).status_code == 201, name
            _, check = _duplicate_check(client)
            assert check["measurement"] == "unmeasured" and not check.get("duplicate_detected"), name
            assert _reports(svc) == [], name
    events = [_fill_event("evt-a", "ord-1", "2026-10-24T09:50:13Z"),
              _fill_event("evt-b", "ord-2", "2026-10-25T09:51:15Z", plan_id="plan-B")]
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        _post_duplicate_trigger(client, svc, events)
        _, check = _duplicate_check(client)
        assert not check.get("duplicate_detected") and _reports(svc) == []
