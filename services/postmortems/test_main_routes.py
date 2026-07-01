"""
Route tests for services/postmortems — Postmortem Evidence Service (BP5-SVC-011).

Covers:
- POST /api/postmortems — create, duplicate rejection, orphan incident rejection,
      evidence-mismatch rejection
- GET  /api/postmortems — list, filter by incident_id, status
- GET  /api/postmortems/{id} — get, 404 on missing
- POST /api/postmortems/{id}/status — lifecycle, auto published_at
- POST /api/postmortems/{id}/link-evolution-decision — EVO-003 edge
- GET  /api/incidents/{incident_id}/postmortem — convenience route
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from services.incident.reference_validation import CanonicalReferenceError
from services.incident.incident import IncidentCase, IncidentStatus
from services.postmortems.main import app, store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.postmortems.main.reference_validator", _AcceptAllValidator())
    store._incidents.clear()
    store._postmortems.clear()
    yield
    store._incidents.clear()
    store._postmortems.clear()


client = TestClient(app)

_INC_ID = f"inc-{uuid.uuid4().hex[:8]}"
_PM_ID = f"pm-{uuid.uuid4().hex[:8]}"
_BINDING_ID = "binding-abc"

_INC_EVIDENCE = dict(
    binding_id=_BINDING_ID,
    deployment_stage="live",
    deployment_plan_id="plan-001",
    capital_pool_id="pool-001",
    persona_capital_binding_id="pcb-001",
    artifact_id="artifact-001",
    artifact_version="1.0.0",
    runtime_id="runtime-001",
    trace_id="trace-001",
)


def _seed_incident(incident_id: str = _INC_ID, **overrides) -> None:
    """Seed a minimal IncidentCase directly into the store."""
    from datetime import datetime, timezone
    kwargs = {
        "incident_id": incident_id,
        "title": "Test incident",
        "status": IncidentStatus.OPEN.value,
        "severity": "high",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        **_INC_EVIDENCE,
    }
    kwargs.update(overrides)
    inc = IncidentCase(**kwargs)
    store._incidents[incident_id] = inc


_PM_PAYLOAD = {
    "postmortem_id": _PM_ID,
    "title": "Postmortem for drawdown breach",
    "status": "draft",
    "incident_id": _INC_ID,
    "root_cause": "Misconfigured drawdown threshold parameter",
    **_INC_EVIDENCE,
}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/__health__")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/postmortems — create
# ---------------------------------------------------------------------------

def test_create_postmortem():
    _seed_incident()
    r = client.post("/api/postmortems", json=_PM_PAYLOAD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["postmortem_id"] == _PM_ID
    assert body["incident_id"] == _INC_ID
    assert body["root_cause"] == "Misconfigured drawdown threshold parameter"
    assert body["binding_id"] == _BINDING_ID
    assert body["telemetry_event_ids"] == []
    assert body["reconciliation_ids"] == []


def test_create_postmortem_auto_id():
    _seed_incident()
    payload = {k: v for k, v in _PM_PAYLOAD.items() if k != "postmortem_id"}
    r = client.post("/api/postmortems", json=payload)
    assert r.status_code == 201
    assert r.json()["postmortem_id"].startswith("pm-")


def test_create_postmortem_duplicate_rejected():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.post("/api/postmortems", json=_PM_PAYLOAD)
    assert r.status_code == 409


def test_create_postmortem_orphan_incident_rejected():
    """Postmortem cannot be created without a matching IncidentCase."""
    r = client.post("/api/postmortems", json=_PM_PAYLOAD)
    assert r.status_code == 422
    assert "unknown IncidentCase" in r.text or "IncidentCase" in r.text


def test_create_postmortem_evidence_mismatch_rejected():
    """Propagated evidence fields must match the referenced IncidentCase."""
    _seed_incident()
    wrong_payload = {
        **_PM_PAYLOAD,
        "postmortem_id": f"pm-bad-{uuid.uuid4().hex[:6]}",
        "binding_id": "binding-WRONG",  # mismatch
    }
    r = client.post("/api/postmortems", json=wrong_payload)
    assert r.status_code == 422
    assert "mismatch" in r.text.lower() or "binding_id" in r.text.lower()


def test_create_postmortem_missing_root_cause():
    _seed_incident()
    payload = {k: v for k, v in _PM_PAYLOAD.items() if k != "root_cause"}
    r = client.post("/api/postmortems", json=payload)
    assert r.status_code == 422


def test_create_postmortem_parent_incident_reference_error_rejected(monkeypatch):
    class _RejectingValidator:
        def validate_incident(self, incident):
            raise CanonicalReferenceError(["binding_id 'binding-abc' does not resolve"])

    monkeypatch.setattr("services.postmortems.main.reference_validator", _RejectingValidator())
    _seed_incident()
    r = client.post("/api/postmortems", json=_PM_PAYLOAD)
    assert r.status_code == 422
    assert "reference_errors" in r.text


# ---------------------------------------------------------------------------
# POST /api/postmortems/consume-resolved-incident — draft worker
# ---------------------------------------------------------------------------

def test_consume_resolved_incident_creates_postmortem_draft():
    _seed_incident(
        status=IncidentStatus.RESOLVED.value,
        resolved_at="2026-06-27T15:30:00Z",
        telemetry_event_ids=["evt-drift-001"],
        reconciliation_ids=["drift-report-001", "recon-run-001"],
        incident_cluster_id="drift:rolling_drawdown_multiple",
        evidence_summary="rolling_drawdown_multiple breached paper threshold",
        lineage_ref="artifact-001@1.0.0",
    )

    r = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["postmortem_id"] == f"pm-{_INC_ID}"
    assert body["status"] == "draft"
    assert body["incident_id"] == _INC_ID
    assert body["telemetry_event_ids"] == ["evt-drift-001"]
    assert body["reconciliation_ids"] == ["drift-report-001", "recon-run-001"]
    assert body["incident_cluster_id"] == "drift:rolling_drawdown_multiple"
    assert body["incident_evidence_summary"] == "rolling_drawdown_multiple breached paper threshold"
    assert body["lineage_ref"] == "artifact-001@1.0.0"
    assert "root cause pending analysis" in body["root_cause"]
    assert len(store.list_postmortems()) == 1


def test_consume_resolved_incident_duplicate_event_is_idempotent():
    _seed_incident(
        status=IncidentStatus.RESOLVED.value,
        resolved_at="2026-06-27T15:30:00Z",
        telemetry_event_ids=["evt-drift-001"],
        reconciliation_ids=["drift-report-001"],
    )

    first = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})
    second = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})

    assert first.status_code == 201
    assert second.status_code == 200, second.text
    assert second.json()["postmortem_id"] == first.json()["postmortem_id"]
    assert len(store.list_postmortems()) == 1


def test_consume_resolved_incident_refreshes_existing_draft_evidence():
    _seed_incident(
        status=IncidentStatus.RESOLVED.value,
        resolved_at="2026-06-27T15:30:00Z",
        telemetry_event_ids=["evt-drift-001"],
        reconciliation_ids=["drift-report-001"],
        evidence_summary="initial drift evidence",
    )
    first = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})
    assert first.status_code == 201

    current = store.get_incident(_INC_ID)
    assert current is not None
    store._incidents[_INC_ID] = IncidentCase(
        **{
            **current.to_dict(),
            "telemetry_event_ids": ["evt-drift-001", "evt-drift-002"],
            "reconciliation_ids": ["drift-report-001", "recon-run-002"],
            "evidence_summary": "initial drift evidence; second reconciliation evidence",
        }
    )

    second = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["postmortem_id"] == first.json()["postmortem_id"]
    assert body["telemetry_event_ids"] == ["evt-drift-001", "evt-drift-002"]
    assert body["reconciliation_ids"] == ["drift-report-001", "recon-run-002"]
    assert body["incident_evidence_summary"] == "initial drift evidence; second reconciliation evidence"
    assert len(store.list_postmortems()) == 1


def test_consume_resolved_incident_rejects_open_incident():
    _seed_incident(status=IncidentStatus.OPEN.value)

    r = client.post("/api/postmortems/consume-resolved-incident", json={"incident_id": _INC_ID})

    assert r.status_code == 422
    assert "not resolved or closed" in r.text
    assert store.list_postmortems() == []


# ---------------------------------------------------------------------------
# GET /api/postmortems
# ---------------------------------------------------------------------------

def test_list_postmortems_empty():
    r = client.get("/api/postmortems")
    assert r.status_code == 200
    assert r.json() == []


def test_list_postmortems_returns_created():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.get("/api/postmortems")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["postmortem_id"] == _PM_ID


def test_list_filter_by_incident_id():
    _seed_incident(_INC_ID)
    inc2_id = f"inc-{uuid.uuid4().hex[:8]}"
    _seed_incident(inc2_id)
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    pm2_payload = {
        **_PM_PAYLOAD,
        "postmortem_id": f"pm-{uuid.uuid4().hex[:8]}",
        "incident_id": inc2_id,
    }
    client.post("/api/postmortems", json=pm2_payload)

    r = client.get(f"/api/postmortems?incident_id={_INC_ID}")
    assert r.status_code == 200
    items = r.json()
    assert all(i["incident_id"] == _INC_ID for i in items)
    assert len(items) == 1


def test_list_filter_by_status():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.get("/api/postmortems?status=draft")
    assert r.status_code == 200
    assert all(i["status"] == "draft" for i in r.json())


# ---------------------------------------------------------------------------
# GET /api/postmortems/{id}
# ---------------------------------------------------------------------------

def test_get_postmortem():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.get(f"/api/postmortems/{_PM_ID}")
    assert r.status_code == 200
    assert r.json()["postmortem_id"] == _PM_ID


def test_get_postmortem_not_found():
    r = client.get("/api/postmortems/pm-missing")
    assert r.status_code == 404


def test_get_postmortem_operator_payload():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.get(f"/api/postmortems/{_PM_ID}/operator-payload")
    assert r.status_code == 200
    body = r.json()
    assert body["postmortem_id"] == _PM_ID
    assert body["incident_id"] == _INC_ID
    assert body["incident_status"] == "open"
    assert body["incident_severity"] == "high"
    assert body["linked_evolution_decision_id"] is None


# ---------------------------------------------------------------------------
# POST /api/postmortems/{id}/status
# ---------------------------------------------------------------------------

def test_status_draft_to_review():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.post(f"/api/postmortems/{_PM_ID}/status", json={"status": "review"})
    assert r.status_code == 200
    assert r.json()["status"] == "review"


def test_status_to_published_sets_published_at():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.post(f"/api/postmortems/{_PM_ID}/status", json={"status": "published"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None


def test_status_invalid():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.post(f"/api/postmortems/{_PM_ID}/status", json={"status": "invalid"})
    assert r.status_code == 400


def test_status_404():
    r = client.post("/api/postmortems/pm-missing/status", json={"status": "review"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/postmortems/{id}/link-evolution-decision
# ---------------------------------------------------------------------------

def test_link_evolution_decision():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.post(
        f"/api/postmortems/{_PM_ID}/link-evolution-decision",
        json={"evolution_decision_id": "evo-001"},
    )
    assert r.status_code == 200
    assert r.json()["linked_evolution_decision_id"] == "evo-001"


def test_link_evolution_decision_404():
    r = client.post(
        "/api/postmortems/pm-missing/link-evolution-decision",
        json={"evolution_decision_id": "evo-001"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/incidents/{incident_id}/postmortem — convenience
# ---------------------------------------------------------------------------

def test_get_postmortem_for_incident_exists():
    _seed_incident()
    client.post("/api/postmortems", json=_PM_PAYLOAD)
    r = client.get(f"/api/incidents/{_INC_ID}/postmortem")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert body["incident_id"] == _INC_ID


def test_get_postmortem_for_incident_none():
    r = client.get("/api/incidents/inc-no-postmortem/postmortem")
    assert r.status_code == 200
    assert r.json() is None
