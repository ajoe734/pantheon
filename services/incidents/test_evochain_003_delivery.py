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
from services.incidents.main import app, store

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAllValidator())
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

def test_incident_resolution_delivery_success():
    _seed_incident()
    
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.text = "Created"
    
    with patch("httpx.post", return_value=mock_response) as mock_post:
        r = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
        assert r.status_code == 200
        
        # Verify postmortems API was called
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8091/api/postmortems/consume-resolved-incident"
        assert kwargs["json"] == {"incident_id": "inc-123"}

def test_incident_close_delivery_success():
    _seed_incident()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    with patch("httpx.post", return_value=mock_response) as mock_post:
        r = client.post("/api/incidents/inc-123/status", json={"status": "closed"})
        assert r.status_code == 200
        mock_post.assert_called_once()

def test_incident_delivery_failure_retry_and_error():
    _seed_incident()
    
    # Simulate connection failure
    with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")) as mock_post:
        r = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
        assert r.status_code == 502
        assert "Failed to send resolved incident" in r.json()["detail"]
        
        # Verify it retried 3 times
        assert mock_post.call_count == 3
