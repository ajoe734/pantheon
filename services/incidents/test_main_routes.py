"""
Route tests for services/incidents — Incident Evidence Service (BP5-SVC-011).

Covers:
- POST /api/incidents — create, duplicate rejection, missing-field rejection
- GET  /api/incidents — list, filter by binding_id, status, open_only
- GET  /api/incidents/{id} — get, 404 on missing
- POST /api/incidents/{id}/status — lifecycle transitions, auto resolved_at
- GET  /api/incidents/{id}/operator-payload — enriched view, is_open flag
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from services.incident.reference_validation import CanonicalReferenceError
from services.incidents.consumer import ThresholdTelemetryIncidentConsumer
from services.incidents.main import app, store


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    """Reset the in-memory store before each test."""
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAllValidator())
    _reset_store()
    yield
    _reset_store()


def _reset_store():
    store._incidents.clear()
    store._postmortems.clear()
    path = getattr(store, "_path", None)
    if path is not None and path.exists():
        path.unlink()
    if hasattr(store, "_loaded_mtime_ns"):
        store._loaded_mtime_ns = None


client = TestClient(app)

_BINDING_ID = "binding-abc"
_INCIDENT_ID = f"inc-test-{uuid.uuid4().hex[:8]}"
_THRESHOLD_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "threshold_breach_telemetry.json"

_BASE_PAYLOAD = {
    "incident_id": _INCIDENT_ID,
    "title": "Drawdown threshold breached",
    "status": "open",
    "severity": "high",
    "binding_id": _BINDING_ID,
    "deployment_stage": "live",
    "deployment_plan_id": "plan-001",
    "capital_pool_id": "pool-001",
    "persona_capital_binding_id": "pcb-001",
    "artifact_id": "artifact-001",
    "artifact_version": "1.0.0",
    "runtime_id": "runtime-001",
    "trace_id": "trace-001",
}


def _threshold_fixture():
    return json.loads(_THRESHOLD_FIXTURE_PATH.read_text(encoding="utf-8"))


def _drift_report_payload(**overrides):
    payload = {
        "drift_report_id": "drift-report-001",
        "recon_run_id": "recon-run-001",
        "incident_cluster_id": "drift:rolling_drawdown_multiple",
        "drift_type": "pnl",
        "status": "open",
        "severity": "medium",
        "scope_ref": "binding-drift-001",
        "binding_id": "binding-drift-001",
        "runtime_id": "runtime-drift-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-drift-001",
        "capital_pool_id": "pool-drift-001",
        "persona_capital_binding_id": "pcb-drift-001",
        "artifact_id": "artifact-drift-001",
        "artifact_version": "1.0.0",
        "trace_id": "trace-drift-001",
        "telemetry_event_ids": ["evt-drift-001"],
        "metrics": {
            "worst_metric": "rolling_drawdown_multiple",
            "breached_metric_ids": ["rolling_drawdown_multiple"],
        },
        "evidence_refs": [
            "telemetry_event:evt-drift-001",
            "drift_report:drift-report-001",
            "reconciliation_record:recon-run-001",
        ],
        "generated_at": "2026-06-27T15:00:00Z",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/__health__")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/incidents — create
# ---------------------------------------------------------------------------

def test_create_incident():
    r = client.post("/api/incidents", json=_BASE_PAYLOAD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_id"] == _INCIDENT_ID
    assert body["severity"] == "high"
    assert body["status"] == "open"
    assert body["binding_id"] == _BINDING_ID


def test_create_incident_auto_id():
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "incident_id"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["incident_id"].startswith("inc-")


def test_create_incident_duplicate_rejected():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post("/api/incidents", json=_BASE_PAYLOAD)
    assert r.status_code == 409


def test_create_incident_invalid_severity():
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}", "severity": "extreme"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


def test_create_incident_invalid_stage():
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}", "deployment_stage": "staging"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


def test_create_incident_reference_error_rejected(monkeypatch):
    class _RejectingValidator:
        def validate_incident(self, incident):
            raise CanonicalReferenceError(["binding_id 'binding-abc' does not resolve"])

    monkeypatch.setattr("services.incidents.main.reference_validator", _RejectingValidator())
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422
    assert "reference_errors" in r.text


def test_create_incident_missing_binding_id():
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "binding_id"}
    payload["incident_id"] = f"inc-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/incidents/consume-threshold — telemetry threshold consumer
# ---------------------------------------------------------------------------

def test_threshold_consumer_fixture_creates_incident_case():
    payload = _threshold_fixture()
    result = ThresholdTelemetryIncidentConsumer(incident_store=store).consume(payload)

    assert result.created is True
    incident = result.incident
    telemetry = payload["telemetry_event"]
    assert incident.incident_id == payload["incident_id"]
    assert incident.binding_id == telemetry["runtime_binding_id"]
    assert incident.deployment_stage == "paper"
    assert incident.deployment_plan_id == telemetry["deployment_plan_id"]
    assert incident.capital_pool_id == telemetry["capital_pool_id"]
    assert incident.persona_capital_binding_id == telemetry["persona_capital_binding_id"]
    assert incident.artifact_id == telemetry["artifact_id"]
    assert incident.artifact_version == telemetry["artifact_version"]
    assert incident.runtime_id == telemetry["runtime_id"]
    assert incident.trace_id == telemetry["trace_id"]
    assert incident.severity == "high"
    assert incident.telemetry_event_ids == [telemetry["event_id"]]
    assert "threshold_metric=rolling_drawdown_multiple" in (incident.evidence_summary or "")
    assert store.get_incident(payload["incident_id"]) is not None


def test_consume_threshold_route_creates_incident_case():
    payload = _threshold_fixture()
    r = client.post("/api/incidents/consume-threshold", json=payload)
    assert r.status_code == 201, r.text

    body = r.json()
    telemetry = payload["telemetry_event"]
    assert body["incident_id"] == payload["incident_id"]
    assert body["binding_id"] == telemetry["runtime_binding_id"]
    assert body["deployment_stage"] == "paper"
    assert body["telemetry_event_ids"] == [telemetry["event_id"]]
    assert "threshold_metric=rolling_drawdown_multiple" in body["evidence_summary"]


def test_consume_threshold_route_idempotent_replay_returns_existing():
    payload = _threshold_fixture()
    first = client.post("/api/incidents/consume-threshold", json=payload)
    second = client.post("/api/incidents/consume-threshold", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["incident_id"] == payload["incident_id"]
    assert len(store.list_incidents()) == 1


def test_consume_threshold_route_rejects_unbreached_threshold():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-unbreached-threshold"
    payload["threshold_snapshot"]["breached"] = False

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert store.get_incident("inc-unbreached-threshold") is None


def test_consume_drift_report_route_creates_incident_case():
    payload = _drift_report_payload()
    r = client.post("/api/incidents/consume-drift-report", json={"drift_report": payload})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_id"].startswith("inc-drift-")
    assert body["binding_id"] == "binding-drift-001"
    assert body["runtime_id"] == "runtime-drift-001"
    assert body["telemetry_event_ids"] == ["evt-drift-001"]
    assert body["reconciliation_ids"] == ["drift-report-001", "recon-run-001"]
    assert body["incident_cluster_id"] == "drift:rolling_drawdown_multiple"
    assert len(store.list_incidents()) == 1


def test_consume_drift_report_route_dedupes_by_binding_runtime_cluster():
    first_payload = _drift_report_payload()
    second_payload = _drift_report_payload(
        drift_report_id="drift-report-002",
        recon_run_id="recon-run-002",
        severity="critical",
        telemetry_event_ids=["evt-drift-002"],
        evidence_refs=[
            "telemetry_event:evt-drift-002",
            "drift_report:drift-report-002",
            "reconciliation_record:recon-run-002",
        ],
        generated_at="2026-06-27T15:05:00Z",
    )

    first = client.post("/api/incidents/consume-drift-report", json={"drift_report": first_payload})
    second = client.post("/api/incidents/consume-drift-report", json={"drift_report": second_payload})

    assert first.status_code == 201
    assert second.status_code == 200, second.text
    assert second.json()["incident_id"] == first.json()["incident_id"]
    assert second.json()["severity"] == "critical"
    assert second.json()["telemetry_event_ids"] == ["evt-drift-001", "evt-drift-002"]
    assert second.json()["reconciliation_ids"] == [
        "drift-report-001",
        "recon-run-001",
        "drift-report-002",
        "recon-run-002",
    ]
    assert len(store.list_incidents()) == 1


# ---------------------------------------------------------------------------
# GET /api/incidents — list
# ---------------------------------------------------------------------------

def test_list_incidents_empty():
    r = client.get("/api/incidents")
    assert r.status_code == 200
    assert r.json() == []


def test_list_incidents_returns_created():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get("/api/incidents")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["incident_id"] == _INCIDENT_ID


def test_list_filter_by_binding_id():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    other = {
        **_BASE_PAYLOAD,
        "incident_id": f"inc-other-{uuid.uuid4().hex[:6]}",
        "binding_id": "binding-other",
    }
    client.post("/api/incidents", json=other)
    r = client.get(f"/api/incidents?binding_id={_BINDING_ID}")
    assert r.status_code == 200
    items = r.json()
    assert all(i["binding_id"] == _BINDING_ID for i in items)
    assert len(items) == 1


def test_list_filter_by_status():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get("/api/incidents?status=open")
    assert r.status_code == 200
    assert all(i["status"] == "open" for i in r.json())


def test_list_filter_open_only():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    resolved_payload = {
        **_BASE_PAYLOAD,
        "incident_id": f"inc-resolved-{uuid.uuid4().hex[:6]}",
        "status": "resolved",
    }
    # Can't create resolved without resolved_at via store directly — create open then transition
    r2 = client.post("/api/incidents", json={**_BASE_PAYLOAD, "incident_id": f"inc-r-{uuid.uuid4().hex[:6]}"})
    inc2_id = r2.json()["incident_id"]
    client.post(f"/api/incidents/{inc2_id}/status", json={"status": "resolved"})

    r = client.get("/api/incidents?open_only=true")
    assert r.status_code == 200
    items = r.json()
    assert all(i["status"] in ("open", "investigating") for i in items)


# ---------------------------------------------------------------------------
# GET /api/incidents/{id} — get single
# ---------------------------------------------------------------------------

def test_get_incident():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}")
    assert r.status_code == 200
    assert r.json()["incident_id"] == _INCIDENT_ID


def test_get_incident_not_found():
    r = client.get("/api/incidents/inc-missing")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/incidents/{id}/status — lifecycle transitions
# ---------------------------------------------------------------------------

def test_status_transition_open_to_investigating():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "investigating"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "investigating"


def test_status_transition_to_resolved_auto_sets_resolved_at():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "resolved"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None


def test_status_transition_invalid_status():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "invalid_status"},
    )
    assert r.status_code == 400


def test_status_transition_404():
    r = client.post(
        "/api/incidents/inc-missing/status",
        json={"status": "resolved"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/incidents/{id}/operator-payload
# ---------------------------------------------------------------------------

def test_operator_payload_basic():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"] == _INCIDENT_ID
    assert body["is_open"] is True
    assert body["postmortem_id"] is None
    assert body["linked_evolution_decision_id"] is None


def test_operator_payload_is_open_false_when_resolved():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    client.post(f"/api/incidents/{_INCIDENT_ID}/status", json={"status": "resolved"})
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    assert r.status_code == 200
    assert r.json()["is_open"] is False


def test_operator_payload_not_found():
    r = client.get("/api/incidents/inc-missing/operator-payload")
    assert r.status_code == 404


def test_operator_payload_evidence_fields_present():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    body = r.json()
    for field in (
        "binding_id", "deployment_stage", "deployment_plan_id",
        "capital_pool_id", "persona_capital_binding_id",
        "artifact_id", "artifact_version", "runtime_id", "trace_id",
    ):
        assert field in body, f"Missing evidence field: {field}"
        assert body[field], f"Evidence field should not be empty: {field}"
