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
from services.postmortems.main import (
    app,
    inbox_store,
    outbox_store,
    process_postmortems_outbox,
    reconcile_postmortems_outbox,
    store,
)

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.postmortems.main.reference_validator", _AcceptAllValidator())
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_MAX_ATTEMPTS", "3")

    store._incidents.clear()
    store._postmortems.clear()
    # Clean outbox store
    if hasattr(outbox_store.impl, "path") and outbox_store.impl.path.exists():
        try:
            outbox_store.impl.path.unlink()
        except Exception:
            pass
    if hasattr(inbox_store.impl, "path") and inbox_store.impl.path.exists():
        inbox_store.impl.path.unlink()
    yield
    store._incidents.clear()
    store._postmortems.clear()
    if hasattr(outbox_store.impl, "path") and outbox_store.impl.path.exists():
        try:
            outbox_store.impl.path.unlink()
        except Exception:
            pass
    if hasattr(inbox_store.impl, "path") and inbox_store.impl.path.exists():
        inbox_store.impl.path.unlink()

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

    # 1. Status transition publishes postmortem and writes to outbox
    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 200

    # Verify it is in outbox
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.payload["postmortem_id"] == "pm-123"
    assert "decision_id" in records[0].event.payload
    assert "inc-123" in records[0].event.payload["decision_id"]

    # 2. Process outbox and verify httpx POST is made
    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        asyncio.run(process_postmortems_outbox())
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8093/api/evolution/proposals"
        payload = kwargs["json"]
        assert payload["decision_id"] == records[0].event.payload["decision_id"]
        assert payload["delivery_event"]["event_id"] == records[0].event.event_id
        assert payload["delivery_event"]["idempotency_key"] == records[0].event.idempotency_key
        assert payload["delivery_event"]["sequence_no"] == 1
        assert payload["delivery_event"]["trace_id"] == records[0].event.trace_id
        assert payload["metadata"]["bridge_contract"] == "on_postmortem_published"
        assert payload["metadata"]["bridge_proposed_action"] == "rollback"
        assert payload["action_type"] == "flag_for_review"

        # Verify record is marked published
        assert len(outbox_store.list_pending_and_failed()) == 0

def test_publish_delivery_duplicate_success():
    _seed_incident()
    _seed_postmortem()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"

    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 200

    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1

    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        asyncio.run(process_postmortems_outbox())
        mock_post.assert_called_once()
        assert len(outbox_store.list_pending_and_failed()) == 0

def test_publish_delivery_failure_retry_and_error():
    _seed_incident()
    _seed_postmortem()

    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 200

    # Simulate connection failure
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")) as mock_post:
        # First attempt
        asyncio.run(process_postmortems_outbox())
        assert mock_post.call_count == 1
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 1
        assert records[0].delivery_attempts == 1
        assert records[0].status == "failed"

        # Second attempt
        asyncio.run(process_postmortems_outbox())
        assert mock_post.call_count == 2
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 1
        assert records[0].delivery_attempts == 2

        # Third attempt
        asyncio.run(process_postmortems_outbox())
        assert mock_post.call_count == 3
        # Should now be dead_lettered
        records = outbox_store.list_pending_and_failed()
        assert len(records) == 0

        # Verify in complete list
        all_payloads = outbox_store.impl.list_all()
        assert len(all_payloads) == 1
        assert all_payloads[0]["status"] == "dead_lettered"

def test_publish_delivery_precondition_validation_failed():
    _seed_incident()
    pm = _seed_postmortem()

    # Use dataclasses.replace to modify frozen Postmortem instance
    import dataclasses
    invalid_pm = dataclasses.replace(pm, artifact_id="")
    store._postmortems[pm.postmortem_id] = invalid_pm

    # Should fail synchronously with 422
    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 422
    assert "Bridge precondition validation failed" in r.json()["detail"]

    # Verify nothing was enqueued to outbox
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 0


def test_published_transition_invokes_pure_bridge(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    from services.evolution import postmortem_bridge

    bridge_spy = MagicMock(wraps=postmortem_bridge.on_postmortem_published)
    monkeypatch.setattr(postmortem_bridge, "on_postmortem_published", bridge_spy)

    response = client.post("/api/postmortems/pm-123/status", json={"status": "published"})

    assert response.status_code == 200
    bridge_spy.assert_called_once()
    bridge_input = bridge_spy.call_args.args[0]
    assert bridge_input["postmortem_id"] == "pm-123"
    assert bridge_input["severity"] == "high"


def test_low_severity_without_corrective_action_is_audited_noop():
    incident = _seed_incident()
    import dataclasses

    store._incidents[incident.incident_id] = dataclasses.replace(incident, severity="low")
    _seed_postmortem()

    response = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert response.status_code == 200
    record = outbox_store.list_pending_and_failed()[0]
    assert record.event.payload["proposal"] is None

    with patch("httpx.AsyncClient.post") as mock_post:
        asyncio.run(process_postmortems_outbox())
    mock_post.assert_not_called()
    assert outbox_store.list_pending_and_failed() == []


def test_duplicate_publish_transition_keeps_one_logical_event():
    _seed_incident()
    _seed_postmortem()

    first = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    replay = client.post("/api/postmortems/pm-123/status", json={"status": "published"})

    assert first.status_code == replay.status_code == 200
    assert replay.json()["published_at"] == first.json()["published_at"]
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.event_id.startswith("evt-postmortem-published-")


def test_prepare_failure_does_not_publish_postmortem(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    monkeypatch.setattr(outbox_store, "prepare", MagicMock(side_effect=OSError("disk full")))

    response = client.post("/api/postmortems/pm-123/status", json={"status": "published"})

    assert response.status_code == 503
    assert store.get_postmortem("pm-123").status == "draft"


def test_activation_failure_leaves_recoverable_postmortem_event(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    real_activate = outbox_store.activate
    monkeypatch.setattr(outbox_store, "activate", MagicMock(side_effect=OSError("activation interrupted")))

    response = client.post("/api/postmortems/pm-123/status", json={"status": "published"})

    assert response.status_code == 503
    assert store.get_postmortem("pm-123").status == "published"
    assert len(outbox_store.list_prepared()) == 1

    monkeypatch.setattr(outbox_store, "activate", real_activate)
    assert reconcile_postmortems_outbox() == 1
    assert len(outbox_store.list_pending_and_failed()) == 1


def test_postmortem_dlq_redrive_requires_token_and_approval(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    record = outbox_store.list_pending_and_failed()[0]
    outbox_store.put(
        record.mark_failed(
            "contract failure",
            max_attempts=1,
            base_delay_seconds=0,
            permanent=True,
        )
    )
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_REDRIVE_TOKEN", "redrive-secret")

    accepted = client.post(
        f"/api/postmortems/outbox/{record.outbox_id}/redrive",
        headers={"X-Pantheon-Outbox-Redrive-Token": "redrive-secret"},
        json={
            "actor_id": "risk-1",
            "actor_role": "risk_owner",
            "approval_ref": "APR-2",
            "reason": "evolution recovered",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "pending"
    assert accepted.json()["delivery"]["redrive_count"] == 1
