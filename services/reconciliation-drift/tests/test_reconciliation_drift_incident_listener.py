from __future__ import annotations

import importlib.util
import sys
import tempfile
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
