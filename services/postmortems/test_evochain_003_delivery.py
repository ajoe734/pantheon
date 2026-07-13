import sys
import os
import httpx
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from services.incident.incident import IncidentCase, IncidentStatus
from services.postmortems.main import app, store

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.postmortems.main.reference_validator", _AcceptAllValidator())
    monkeypatch.setattr("time.sleep", lambda s: None)
    
    store._incidents.clear()
    store._postmortems.clear()
    yield
    store._incidents.clear()
    store._postmortems.clear()

def _seed_incident(incident_id="inc-123"):
    from datetime import datetime, timezone
    inc = IncidentCase(
        incident_id=incident_id,
        title="Test Incident",
        status=IncidentStatus.OPEN.value,
        severity="high",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        binding_id="binding-123",
        deployment_stage="live",
        deployment_plan_id="plan-123",
        capital_pool_id="pool-123",
        persona_capital_binding_id="pcb-123",
        artifact_id="artifact-123",
        artifact_version="1.0.0",
        runtime_id="runtime-123",
        trace_id="trace-123",
    )
    store._incidents[incident_id] = inc
    return inc

def _seed_postmortem(postmortem_id="pm-123", incident_id="inc-123"):
    from datetime import datetime, timezone
    from services.incident.incident import Postmortem
    pm = Postmortem(
        postmortem_id=postmortem_id,
        title="Test Postmortem",
        status="draft",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        incident_id=incident_id,
        binding_id="binding-123",
        deployment_stage="live",
        deployment_plan_id="plan-123",
        capital_pool_id="pool-123",
        persona_capital_binding_id="pcb-123",
        artifact_id="artifact-123",
        artifact_version="1.0.0",
        runtime_id="runtime-123",
        trace_id="trace-123",
        root_cause="some root cause",
    )
    store._postmortems[postmortem_id] = pm
    return pm

def test_publish_delivery_success():
    _seed_incident()
    _seed_postmortem()
    
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.text = "Created"
    
    with patch("httpx.post", return_value=mock_response) as mock_post:
        r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
        assert r.status_code == 200
        
        # Verify evolution proposals API was called
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8093/api/evolution/proposals"
        
        # Verify payload mapping content
        payload = kwargs["json"]
        assert payload["linked_postmortem_id"] == "pm-123"
        assert payload["linked_incident_id"] == "inc-123"
        assert payload["action_type"] == "flag_for_review"

def test_publish_delivery_duplicate_success():
    _seed_incident()
    _seed_postmortem()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    with patch("httpx.post", return_value=mock_response) as mock_post:
        r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
        assert r.status_code == 200
        mock_post.assert_called_once()

def test_publish_delivery_failure_retry_and_error():
    _seed_incident()
    _seed_postmortem()
    
    # Simulate connection failure
    with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")) as mock_post:
        r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
        assert r.status_code == 502
        assert "Failed to publish postmortem" in r.json()["detail"]
        
        # Verify it retried 3 times
        assert mock_post.call_count == 3
