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
