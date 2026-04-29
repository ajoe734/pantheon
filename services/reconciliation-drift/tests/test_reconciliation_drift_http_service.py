from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module():
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_DATA_DIR": tempfile.mkdtemp(),
            "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
            "PANTHEON_LINEAGE_READ_URL": "http://lineage-read:8094",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("reconciliation_drift_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["reconciliation_drift_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_reconciliation_drift_generates_summary_status_and_alert_handoff() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "reconciliation-drift"
    assert health.json()["emergency_control_chain_member"] is False

    created = client.post(
        "/api/reconciliation-drift/evaluations",
        json={
            "evaluation_id": "rdeval-test-001",
            "binding_id": "binding-1",
            "runtime_id": "runtime-1",
            "baseline_metrics": {"slippage_bps": 10.0, "fill_ratio": 0.95},
            "telemetry_events": [
                {
                    "event_id": "evt-1",
                    "binding_id": "binding-1",
                    "runtime_id": "runtime-1",
                    "metrics": {"slippage_bps": 18.0, "fill_ratio": 0.90},
                },
                {
                    "event_id": "evt-2",
                    "binding_id": "binding-1",
                    "runtime_id": "runtime-1",
                    "metrics": {"slippage_bps": 20.0, "fill_ratio": 0.88},
                },
            ],
            "lineage_projection": {"target_id": "binding-1", "derived_only": True},
            "runtime_evidence": {"binding_id": "binding-1", "runtime_id": "runtime-1", "status": "active"},
            "thresholds": {
                "slippage_bps": {
                    "warning_relative_delta": 0.20,
                    "critical_relative_delta": 0.50,
                }
            },
            "evaluated_at": "2026-04-28T22:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["status"] == "critical"
    assert payload["observed_metrics"]["slippage_bps"] == 19.0
    assert payload["source_contract"]["derived_only"] is True
    assert payload["source_contract"]["emergency_control_chain_affected"] is False

    summary = client.get("/api/reconciliation-drift/summary", params={"binding_id": "binding-1"})
    assert summary.status_code == 200
    assert summary.json()["latest_evaluation_id"] == "rdeval-test-001"
    assert summary.json()["alert_handoff_count"] == 1

    status = client.get("/api/reconciliation-drift/reconciliation-status/binding-1")
    assert status.status_code == 200
    assert [check["status"] for check in status.json()["checks"]] == ["ok", "ok", "ok", "ok", "ok"]

    alerts = client.get("/api/reconciliation-drift/alerts", params={"binding_id": "binding-1"})
    assert alerts.status_code == 200
    alert = alerts.json()[0]
    assert alert["alert_type"] == "metric_drift"
    assert alert["target_service"] == "evolution"
    assert alert["emergency_control_chain_affected"] is False

    handoff = client.post(
        f"/api/reconciliation-drift/alerts/{alert['alert_id']}/handoff",
        json={"actor_id": "operator", "handoff_state": "sent", "note": "queued for threshold evaluation"},
    )
    assert handoff.status_code == 200
    assert handoff.json()["handoff_state"] == "sent"
    assert handoff.json()["handoff_actor"] == "operator"


def test_reconciliation_drift_marks_degraded_inputs_and_mismatch_alerts() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    created = client.post(
        "/api/reconciliation-drift/evaluations",
        json={
            "evaluation_id": "rdeval-test-002",
            "binding_id": "binding-expected",
            "baseline_metrics": {"pnl": 10.0},
            "telemetry_events": [
                {
                    "event_id": "evt-mismatch",
                    "binding_id": "binding-other",
                    "metrics": {"pnl": 10.5},
                }
            ],
            "lineage_projection": None,
            "runtime_evidence": None,
            "evaluated_at": "2026-04-28T22:10:00Z",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["status"] == "critical"
    assert any(check["status"] == "degraded" for check in payload["reconciliation_checks"])
    assert any(check["check"] == "telemetry_binding_alignment" and check["status"] == "critical" for check in payload["reconciliation_checks"])

    alerts = client.get("/api/reconciliation-drift/alerts", params={"binding_id": "binding-expected"})
    assert alerts.status_code == 200
    assert alerts.json()[0]["alert_type"] == "reconciliation_mismatch"
    assert alerts.json()[0]["target_service"] == "incidents"
