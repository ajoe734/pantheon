import sys
import os
import httpx
from pathlib import Path
import pytest
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import MagicMock, patch

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from services.incident.incident import (
    IncidentCase,
    IncidentConcurrencyError,
    IncidentStatus,
    Postmortem,
)
from services.foundation import (
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    TraceContext,
)
from services.foundation.reliable_delivery import ReliableInboxConcurrencyError
from services.postmortems import main as postmortems_main
from services.postmortems.consumer import postmortem_id_for_incident
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
    if hasattr(inbox_store.impl, "path") and inbox_store.impl.path.exists():
        inbox_store.impl.path.unlink()
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


def _resolved_incident_event(incident_id="inc-123"):
    trace = TraceContext.new(
        environment=EnvironmentScope(name=EnvironmentName.LIVE),
        source_system="incident-svc",
        idempotency_key=f"idmp-{incident_id}",
    )
    return EventEnvelope(
        event_id=f"evt-{incident_id}",
        event_type="incident.resolved",
        aggregate_type="incident",
        aggregate_id=incident_id,
        sequence_no=1,
        trace=trace,
        payload={"incident_id": incident_id, "terminal_status": "resolved"},
        idempotency_key=f"idmp-{incident_id}",
        producer_service="incident-svc",
    )


def test_concurrent_exact_first_hop_is_claimed_instead_of_false_rejected(monkeypatch):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    event = _resolved_incident_event()
    request = {"incident_id": "inc-123", "event": event.to_dict()}
    entered = threading.Event()
    release = threading.Event()
    consumer_type = postmortems_main.ResolvedIncidentPostmortemDraftConsumer
    real_consume = consumer_type.consume

    def delayed_consume(self, payload):
        entered.set()
        assert release.wait(timeout=5)
        return real_consume(self, payload)

    monkeypatch.setattr(consumer_type, "consume", delayed_consume)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(
            lambda: TestClient(app).post(
                "/api/postmortems/consume-resolved-incident",
                json=request,
            )
        )
        assert entered.wait(timeout=5)
        concurrent = TestClient(app).post(
            "/api/postmortems/consume-resolved-incident",
            json=request,
        )
        release.set()
        first = first_future.result(timeout=5)

    assert first.status_code == 201, first.text
    assert concurrent.status_code == 503, concurrent.text
    assert concurrent.status_code != 422
    replay = TestClient(app).post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    assert replay.status_code == 200, replay.text
    assert len(store.list_postmortems()) == 1


def test_first_hop_divergent_exact_identity_replay_fails_closed():
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    event = _resolved_incident_event()
    accepted = client.post(
        "/api/postmortems/consume-resolved-incident",
        json={"incident_id": incident.incident_id, "event": event.to_dict()},
    )
    divergent = replace(
        event,
        payload={"incident_id": incident.incident_id, "terminal_status": "closed"},
    )
    rejected = client.post(
        "/api/postmortems/consume-resolved-incident",
        json={"incident_id": incident.incident_id, "event": divergent.to_dict()},
    )

    assert accepted.status_code == 201, accepted.text
    assert rejected.status_code == 409, rejected.text
    assert "divergent" in rejected.json()["detail"]
    assert len(store.list_postmortems()) == 1


def test_receipt_cas_contention_returns_retryable_first_hop(monkeypatch):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    event = _resolved_incident_event()
    monkeypatch.setattr(
        inbox_store,
        "record_applied",
        MagicMock(
            side_effect=ReliableInboxConcurrencyError(
                "inbox reservation changed concurrently"
            )
        ),
    )

    response = client.post(
        "/api/postmortems/consume-resolved-incident",
        json={"incident_id": "inc-123", "event": event.to_dict()},
    )

    assert response.status_code == 503, response.text
    assert response.status_code != 409
    assert store.find_postmortem_for_incident("inc-123") is not None


@pytest.mark.parametrize("legacy_postmortem_id", ["legacy-manual-pm", "pm-inc-123"])
def test_first_hop_reuses_preexisting_postmortem_and_exact_replay(
    legacy_postmortem_id,
):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    _seed_postmortem(postmortem_id=legacy_postmortem_id)
    event = _resolved_incident_event()
    request = {"incident_id": "inc-123", "event": event.to_dict()}

    first = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    replay = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["postmortem_id"] == legacy_postmortem_id
    assert replay.json()["postmortem_id"] == legacy_postmortem_id
    assert len(store.list_postmortems()) == 1
    state, receipt = inbox_store.classify(event)
    assert state == "duplicate"
    assert receipt is not None
    assert receipt["state"] == "applied"
    assert receipt["result_ref"] == legacy_postmortem_id


def test_postmortem_identity_does_not_collapse_sanitized_incident_ids():
    left = _seed_incident("a b")
    right = _seed_incident("a-b")
    store._incidents[left.incident_id] = replace(
        left,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    store._incidents[right.incident_id] = replace(
        right,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )

    left_event = _resolved_incident_event("a b")
    right_event = _resolved_incident_event("a-b")
    left_response = client.post(
        "/api/postmortems/consume-resolved-incident",
        json={"incident_id": "a b", "event": left_event.to_dict()},
    )
    right_response = client.post(
        "/api/postmortems/consume-resolved-incident",
        json={"incident_id": "a-b", "event": right_event.to_dict()},
    )

    assert left_response.status_code == 201, left_response.text
    assert right_response.status_code == 201, right_response.text
    assert left_response.json()["postmortem_id"] != right_response.json()["postmortem_id"]
    assert postmortem_id_for_incident("a b") != postmortem_id_for_incident("a-b")


def test_concurrent_draft_owner_write_returns_retryable_first_hop(monkeypatch):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    _seed_postmortem(postmortem_id="legacy-manual-pm")
    event = _resolved_incident_event()
    request = {"incident_id": "inc-123", "event": event.to_dict()}
    real_update = store.update_postmortem_draft
    monkeypatch.setattr(
        store,
        "update_postmortem_draft",
        MagicMock(
            side_effect=IncidentConcurrencyError(
                "Postmortem changed concurrently before durable write: legacy-manual-pm"
            )
        ),
    )

    raced = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    assert raced.status_code == 503, raced.text
    assert inbox_store.classify(event)[0] == "new"

    monkeypatch.setattr(store, "update_postmortem_draft", real_update)
    retried = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    assert retried.status_code == 200, retried.text
    assert inbox_store.classify(event)[1]["state"] == "applied"


def test_real_concurrent_draft_edit_is_preserved_and_retried(monkeypatch):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    original = _seed_postmortem(postmortem_id="legacy-manual-pm")
    event = _resolved_incident_event()
    request = {"incident_id": "inc-123", "event": event.to_dict()}
    real_update = store.update_postmortem_draft

    def edit_before_generated_cas(generated, *, expected_snapshot=None):
        current = store.require_postmortem(original.postmortem_id)
        edited = Postmortem(
            **{
                **current.to_dict(),
                "root_cause": "operator edit committed during draft generation",
            }
        )
        real_update(edited, expected_snapshot=current.to_dict())
        return real_update(generated, expected_snapshot=expected_snapshot)

    monkeypatch.setattr(
        store,
        "update_postmortem_draft",
        edit_before_generated_cas,
    )
    raced = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )

    assert raced.status_code == 503, raced.text
    assert (
        store.require_postmortem(original.postmortem_id).root_cause
        == "operator edit committed during draft generation"
    )
    assert inbox_store.classify(event)[0] == "new"

    monkeypatch.setattr(store, "update_postmortem_draft", real_update)
    retried = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    assert retried.status_code == 200, retried.text
    assert (
        store.require_postmortem(original.postmortem_id).root_cause
        == "operator edit committed during draft generation"
    )
    assert inbox_store.classify(event)[1]["state"] == "applied"


def test_concurrent_manual_postmortem_create_returns_retryable_first_hop(monkeypatch):
    incident = _seed_incident()
    store._incidents[incident.incident_id] = replace(
        incident,
        status="resolved",
        resolved_at="2026-07-15T00:00:00Z",
    )
    event = _resolved_incident_event()
    request = {"incident_id": "inc-123", "event": event.to_dict()}
    real_create = store.create_postmortem

    def create_after_manual_race(generated):
        _seed_postmortem(postmortem_id="pm-manual-race")
        return real_create(generated)

    monkeypatch.setattr(store, "create_postmortem", create_after_manual_race)
    raced = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )

    assert raced.status_code == 503, raced.text
    assert inbox_store.classify(event)[0] == "new"
    monkeypatch.setattr(store, "create_postmortem", real_create)

    retried = client.post(
        "/api/postmortems/consume-resolved-incident",
        json=request,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["postmortem_id"] == "pm-manual-race"
    assert inbox_store.classify(event)[1]["state"] == "applied"

def test_publish_delivery_success():
    _seed_incident()
    _seed_postmortem()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "Created"
    mock_response.json.return_value = {
        "decision_id": "evo-pm-candidate_artifact-artifact-123-incident_cluster-inc-123",
        "source_postmortem_id": "pm-123",
    }

    # 1. Status transition publishes postmortem and writes to outbox
    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 200

    # Verify it is in outbox
    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    assert records[0].event.payload["postmortem_id"] == "pm-123"
    assert "decision_id" in records[0].event.payload
    assert "inc-123" in records[0].event.payload["decision_id"]
    assert (
        records[0].event.payload["postmortem"]["published_at"]
        == store.get_postmortem("pm-123").published_at
    )
    assert (
        records[0].event.event_id
        == store.get_postmortem("pm-123").published_event_id
        == records[0].event.payload["postmortem"]["published_event_id"]
    )

    async def _mock_post_and_get(url, **kwargs):
        return mock_response

    with patch("httpx.AsyncClient.post", side_effect=_mock_post_and_get) as mock_post, \
         patch("httpx.AsyncClient.get", side_effect=_mock_post_and_get) as mock_get:
        asyncio.run(process_postmortems_outbox())
        assert mock_post.called
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:8093/api/evolution/proposals"
        payload = kwargs["json"]
        assert payload["decision_id"] == records[0].event.payload["decision_id"]
        assert payload["delivery_event"]["event_id"] == records[0].event.event_id
        assert payload["delivery_event"]["idempotency_key"] == records[0].event.idempotency_key
        assert payload["delivery_event"]["sequence_no"] == 1
        assert payload["delivery_event"]["trace_id"] == records[0].event.trace_id
        assert payload["delivery_event"]["trace"]["environment"]["name"] == "live"
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
    mock_response.json.return_value = {
        "decision_id": "evo-pm-candidate_artifact-artifact-123-incident_cluster-inc-123",
        "source_postmortem_id": "pm-123",
    }

    r = client.post("/api/postmortems/pm-123/status", json={"status": "published"})
    assert r.status_code == 200

    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1

    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post, \
         patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get:
        asyncio.run(process_postmortems_outbox())
        assert mock_post.called
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


def test_published_postmortem_cannot_regress_or_replace_publish_identity():
    _seed_incident()
    _seed_postmortem()

    published = client.post(
        "/api/postmortems/pm-123/status",
        json={"status": "published"},
    )
    regressed = client.post(
        "/api/postmortems/pm-123/status",
        json={"status": "draft"},
    )
    republished = client.post(
        "/api/postmortems/pm-123/status",
        json={"status": "published", "published_at": "2030-01-01T00:00:00Z"},
    )

    assert published.status_code == 200
    assert regressed.status_code == 400
    assert "cannot transition" in regressed.json()["detail"]
    assert republished.status_code == 400
    assert "cannot replace" in republished.json()["detail"]
    durable = store.get_postmortem("pm-123")
    assert durable.status == "published"
    assert durable.published_at == published.json()["published_at"]


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

    # A legitimate parent transition after the postmortem commit must not
    # invalidate the historical publication event selected by that commit.
    store.update_incident_status("inc-123", "closed")
    monkeypatch.setattr(outbox_store, "activate", real_activate)
    assert reconcile_postmortems_outbox() == 1
    assert len(outbox_store.list_pending_and_failed()) == 1


def test_postmortem_save_failure_rolls_back_status_and_keeps_intent_prepared(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    monkeypatch.setattr(store, "_save", MagicMock(side_effect=OSError("domain disk full")))

    with pytest.raises(OSError, match="domain disk full"):
        client.post("/api/postmortems/pm-123/status", json={"status": "published"})

    assert store.get_postmortem("pm-123").status == "draft"
    assert len(outbox_store.list_prepared()) == 1
    assert outbox_store.list_pending_and_failed() == []
    assert reconcile_postmortems_outbox() == 0


def test_concurrent_draft_change_cannot_publish_stale_snapshot(monkeypatch):
    _seed_incident()
    _seed_postmortem()
    original_prepare = postmortems_main._prepare_evolution_delivery

    def prepare_then_change_draft(postmortem, incident):
        prepared = original_prepare(postmortem, incident)
        current = store.get_postmortem("pm-123")
        changed = Postmortem(
            **{
                **current.to_dict(),
                "root_cause": "root cause changed while publish was preparing",
            }
        )
        store.update_postmortem_draft(changed)
        return prepared

    monkeypatch.setattr(
        postmortems_main,
        "_prepare_evolution_delivery",
        prepare_then_change_draft,
    )
    raced = client.post(
        "/api/postmortems/pm-123/status",
        json={"status": "published"},
    )

    assert raced.status_code == 409
    assert store.get_postmortem("pm-123").status == "draft"
    assert "root cause changed" in store.get_postmortem("pm-123").root_cause
    assert outbox_store.list_pending_and_failed() == []
    assert len(outbox_store.list_prepared()) == 1
    assert reconcile_postmortems_outbox() == 0

    # A new intent is versioned from the updated snapshot.  Only that intent
    # becomes deliverable; the stale prepared record remains inert.
    monkeypatch.setattr(
        postmortems_main,
        "_prepare_evolution_delivery",
        original_prepare,
    )
    retried = client.post(
        "/api/postmortems/pm-123/status",
        json={"status": "published"},
    )
    assert retried.status_code == 200, retried.text
    assert len(outbox_store.list_pending_and_failed()) == 1
    assert len(outbox_store.list_prepared()) == 1
    assert reconcile_postmortems_outbox() == 0


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


def test_critical_frozen_normalization():
    from datetime import datetime, timezone
    inc = IncidentCase(
        incident_id="inc-critical-frozen",
        title="Critical Frozen Incident",
        status=IncidentStatus.OPEN.value,
        severity="critical",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        binding_id="binding-123",
        deployment_stage="frozen",
        deployment_plan_id="plan-123",
        capital_pool_id="pool-123",
        persona_capital_binding_id="pcb-123",
        artifact_id="artifact-123",
        artifact_version="1.0.0",
        runtime_id="runtime-123",
        trace_id="trace-123",
    )
    store._incidents["inc-critical-frozen"] = inc

    pm = Postmortem(
        postmortem_id="pm-critical-frozen",
        title="Test Postmortem",
        status="draft",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        incident_id="inc-critical-frozen",
        binding_id="binding-123",
        deployment_stage="frozen",
        deployment_plan_id="plan-123",
        capital_pool_id="pool-123",
        persona_capital_binding_id="pcb-123",
        artifact_id="artifact-123",
        artifact_version="1.0.0",
        runtime_id="runtime-123",
        trace_id="trace-123",
        root_cause="some root cause",
    )
    store._postmortems["pm-critical-frozen"] = pm

    r = client.post("/api/postmortems/pm-critical-frozen/status", json={"status": "published"})
    assert r.status_code == 200

    records = outbox_store.list_pending_and_failed()
    assert len(records) == 1
    proposal = records[0].event.payload["proposal"]
    assert proposal["action_type"] == "flag_for_review"
    assert proposal["target_stage"] == "frozen"
    assert proposal["metadata"]["bridge_proposed_action"] == "freeze"
    assert proposal["metadata"]["bridge_action_normalized"] is True
