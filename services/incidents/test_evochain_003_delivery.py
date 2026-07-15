import sys
import os
import httpx
from pathlib import Path
import pytest
import asyncio
from unittest.mock import MagicMock, patch

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from services.incident.incident import IncidentCase, IncidentStatus
from services.incidents.main import (
    app,
    outbox_store,
    process_incidents_outbox,
    reconcile_incidents_outbox,
    store,
)

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAllValidator())
    monkeypatch.setenv("INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("INCIDENTS_OUTBOX_MAX_ATTEMPTS", "3")

    if store._path and store._path.exists():
        store._path.unlink()
    store._loaded_mtime_ns = None
    store._incidents.clear()
    store._postmortems.clear()
    # Clean outbox store
    if hasattr(outbox_store.impl, "path") and outbox_store.impl.path.exists():
        try:
            outbox_store.impl.path.unlink()
        except Exception:
            pass
    yield
    if store._path and store._path.exists():
        store._path.unlink()
    store._loaded_mtime_ns = None
    store._incidents.clear()
    store._postmortems.clear()
    if hasattr(outbox_store.impl, "path") and outbox_store.impl.path.exists():
        try:
            outbox_store.impl.path.unlink()
        except Exception:
            pass

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

    # 1. Status transition resolves the incident and writes to outbox
    r = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    assert r.status_code == 200

    # Verify it is in outbox
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.payload["incident_id"] == "inc-123"

    # 2. Process outbox and verify httpx POST is made
    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        asyncio.run(process_incidents_outbox())
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8091/api/postmortems/consume-resolved-incident"
        assert kwargs["json"]["incident_id"] == "inc-123"
        envelope = kwargs["json"]["event"]
        assert envelope["event_id"] == records[0].event.event_id
        assert envelope["idempotency_key"] == records[0].event.idempotency_key
        assert envelope["sequence_no"] == 1
        assert envelope["trace_id"] == records[0].event.trace_id
        assert envelope["trace"]["environment"]["name"] == "live"

        # Verify record is marked published
        assert len(outbox_store.list_pending_and_failed()) == 0

def test_incident_close_delivery_success():
    _seed_incident()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"

    r = client.post("/api/incidents/inc-123/status", json={"status": "closed"})
    assert r.status_code == 200

    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1

    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        asyncio.run(process_incidents_outbox())
        mock_post.assert_called_once()
        assert len(outbox_store.list_pending_and_failed()) == 0

def test_incident_delivery_failure_retry_and_error():
    _seed_incident()

    r = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    assert r.status_code == 200

    # Simulate connection failure
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")) as mock_post:
        # First attempt
        asyncio.run(process_incidents_outbox())
        assert mock_post.call_count == 1
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 1
        assert records[0].delivery_attempts == 1
        assert records[0].status == "failed"

        # Second attempt
        asyncio.run(process_incidents_outbox())
        assert mock_post.call_count == 2
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 1
        assert records[0].delivery_attempts == 2

        # Third attempt (hits limit of 3, gets dead_lettered)
        asyncio.run(process_incidents_outbox())
        assert mock_post.call_count == 3
        # Should now be dead_lettered, so list_pending_and_failed doesn't return it
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 0

        # Verify in complete list
        all_payloads = outbox_store.impl.list_all()
        assert len(all_payloads) == 1
        assert all_payloads[0]["status"] == "dead_lettered"


def test_duplicate_terminal_transitions_keep_one_event_and_original_resolution_time():
    _seed_incident()

    first = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    replay = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    closed = client.post("/api/incidents/inc-123/status", json={"status": "closed"})

    assert first.status_code == replay.status_code == closed.status_code == 200
    assert replay.json()["resolved_at"] == first.json()["resolved_at"]
    assert closed.json()["resolved_at"] == first.json()["resolved_at"]
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.event_id.startswith("evt-incident-terminal-")


def test_prepare_failure_does_not_commit_incident_status(monkeypatch):
    _seed_incident()
    monkeypatch.setattr(outbox_store, "prepare", MagicMock(side_effect=OSError("disk full")))

    response = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})

    assert response.status_code == 503
    assert store.get_incident("inc-123").status == "open"


def test_activation_failure_leaves_recoverable_prepared_event(monkeypatch):
    _seed_incident()
    real_activate = outbox_store.activate
    monkeypatch.setattr(outbox_store, "activate", MagicMock(side_effect=OSError("activation interrupted")))

    response = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})

    assert response.status_code == 503
    assert store.get_incident("inc-123").status == "resolved"
    assert len(outbox_store.list_prepared()) == 1

    monkeypatch.setattr(outbox_store, "activate", real_activate)
    assert reconcile_incidents_outbox() == 1
    assert len(outbox_store.list_pending_and_failed()) == 1


def test_incident_save_failure_rolls_back_status_and_keeps_intent_prepared(monkeypatch):
    _seed_incident()
    monkeypatch.setattr(store, "_save", MagicMock(side_effect=OSError("domain disk full")))

    with pytest.raises(OSError, match="domain disk full"):
        client.post("/api/incidents/inc-123/status", json={"status": "resolved"})

    assert store.get_incident("inc-123").status == "open"
    assert len(outbox_store.list_prepared()) == 1
    assert outbox_store.list_pending_and_failed() == []
    assert reconcile_incidents_outbox() == 0


def test_dead_letter_redrive_requires_token_and_approval(monkeypatch):
    _seed_incident()
    client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    record = outbox_store.list_pending_and_failed()[0]
    dead = record.mark_failed(
        "permanent contract failure",
        max_attempts=1,
        base_delay_seconds=0,
        permanent=True,
    )
    outbox_store.put(dead)
    monkeypatch.setenv("INCIDENTS_OUTBOX_REDRIVE_TOKEN", "redrive-secret")

    denied = client.post(
        f"/api/incidents/outbox/{record.outbox_id}/redrive",
        json={
            "actor_id": "risk-1",
            "actor_role": "risk_owner",
            "approval_ref": "APR-1",
            "reason": "dependency recovered",
        },
    )
    assert denied.status_code == 403

    accepted = client.post(
        f"/api/incidents/outbox/{record.outbox_id}/redrive",
        headers={"X-Pantheon-Outbox-Redrive-Token": "redrive-secret"},
        json={
            "actor_id": "risk-1",
            "actor_role": "risk_owner",
            "approval_ref": "APR-1",
            "reason": "dependency recovered",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "pending"
    assert accepted.json()["delivery"]["redrive_count"] == 1


def test_incident_status_regression_prevention():
    _seed_incident()
    # Transition to resolved (terminal status)
    r1 = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    assert r1.status_code == 200

    # Try regressing from resolved back to open
    r2 = client.post("/api/incidents/inc-123/status", json={"status": "open"})
    assert r2.status_code == 400
    assert "cannot regress" in r2.json()["detail"]

    # Transition to closed (terminal status)
    r3 = client.post("/api/incidents/inc-123/status", json={"status": "closed"})
    assert r3.status_code == 200

    # Try regressing from closed to resolved
    r4 = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    assert r4.status_code == 400
    assert "cannot transition" in r4.json()["detail"]


def test_incident_status_cas_validation(monkeypatch):
    _seed_incident()
    
    real_update = store.update_incident_status
    call_count = 0
    def race_update(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            real_update("inc-123", "investigating")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_incident_status", race_update)
    r = client.post("/api/incidents/inc-123/status", json={"status": "resolved"})
    assert r.status_code == 409
    assert "changed concurrently" in r.json()["detail"]
    assert outbox_store.list_prepared() == []

    # The losing resolved intent included terminal_status=resolved.  It must be
    # removed so a later valid close can reuse the deterministic outbox ID with
    # terminal_status=closed instead of failing on an identity collision.
    retried = client.post("/api/incidents/inc-123/status", json={"status": "closed"})
    assert retried.status_code == 200, retried.text
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.payload["terminal_status"] == "closed"
