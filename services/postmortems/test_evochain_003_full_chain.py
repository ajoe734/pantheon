from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.evolution import main as evolution_main
from services.incident.incident import IncidentCase
from services.incidents import main as incidents_main
from services.postmortems import main as postmortems_main


class _AcceptAllValidator:
    def validate_incident(self, incident):
        return None


def _remove_store_file(store) -> None:
    path = getattr(store, "_path", None)
    if path is not None and path.exists():
        path.unlink()
    if hasattr(store, "_loaded_mtime_ns"):
        store._loaded_mtime_ns = None


def _remove_record_file(record_store) -> None:
    path = getattr(record_store.impl, "path", None)
    if path is not None and Path(path).exists():
        Path(path).unlink()


@pytest.fixture(autouse=True)
def clean_chain(monkeypatch):
    monkeypatch.setattr(incidents_main, "reference_validator", _AcceptAllValidator())
    monkeypatch.setattr(postmortems_main, "reference_validator", _AcceptAllValidator())
    monkeypatch.setattr(postmortems_main, "store", incidents_main.store)
    monkeypatch.setenv("INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS", "0")

    _remove_store_file(incidents_main.store)
    incidents_main.store._incidents.clear()
    incidents_main.store._postmortems.clear()
    _remove_record_file(incidents_main.outbox_store)
    _remove_record_file(postmortems_main.outbox_store)
    _remove_record_file(postmortems_main.inbox_store)

    evolution_main.store._decisions.clear()
    if evolution_main.store._storage_path and evolution_main.store._storage_path.exists():
        evolution_main.store._storage_path.unlink()
    evolution_main.proposal_inbox.clear()
    yield

    _remove_store_file(incidents_main.store)
    incidents_main.store._incidents.clear()
    incidents_main.store._postmortems.clear()
    _remove_record_file(incidents_main.outbox_store)
    _remove_record_file(postmortems_main.outbox_store)
    _remove_record_file(postmortems_main.inbox_store)
    evolution_main.store._decisions.clear()
    if evolution_main.store._storage_path and evolution_main.store._storage_path.exists():
        evolution_main.store._storage_path.unlink()
    evolution_main.proposal_inbox.clear()


def _seed_incident() -> IncidentCase:
    incident = IncidentCase(
        incident_id="inc-evochain-full",
        title="Live artifact regression",
        status="open",
        severity="high",
        created_at="2026-07-14T01:00:00Z",
        binding_id="binding-evochain-full",
        deployment_stage="live",
        deployment_plan_id="plan-evochain-full",
        capital_pool_id="pool-evochain-full",
        persona_capital_binding_id="pcb-evochain-full",
        artifact_id="artifact-evochain-full",
        artifact_version="7.0.0",
        runtime_id="runtime-evochain-full",
        trace_id="trace-evochain-full",
        telemetry_event_ids=["tel-evochain-full"],
        incident_cluster_id="cluster-evochain-full",
    )
    incidents_main.store.create_incident(incident)
    return incident


def test_resolve_publish_chain_creates_one_postmortem_and_one_proposal():
    incident = _seed_incident()
    incident_client = TestClient(incidents_main.app)
    postmortem_client = TestClient(postmortems_main.app)
    evolution_client = TestClient(evolution_main.app)

    resolved = incident_client.post(
        f"/api/incidents/{incident.incident_id}/status",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text

    first_hop_requests: list[dict] = []

    async def deliver_to_postmortems(url, *, json):
        first_hop_requests.append(json)
        return postmortem_client.post(
            "/api/postmortems/consume-resolved-incident",
            json=json,
        )

    with patch("httpx.AsyncClient.post", side_effect=deliver_to_postmortems):
        asyncio.run(incidents_main.process_incidents_outbox())

    postmortems = postmortem_client.get(
        "/api/postmortems",
        params={"incident_id": incident.incident_id},
    )
    assert postmortems.status_code == 200
    assert len(postmortems.json()) == 1
    postmortem_id = postmortems.json()[0]["postmortem_id"]
    assert postmortem_id == f"pm-{incident.incident_id}"

    duplicate_first_hop = postmortem_client.post(
        "/api/postmortems/consume-resolved-incident",
        json=first_hop_requests[0],
    )
    assert duplicate_first_hop.status_code == 200
    assert len(postmortem_client.get("/api/postmortems").json()) == 1
    assert len(postmortems_main.inbox_store.impl.list_all()) == 1

    published = postmortem_client.post(
        f"/api/postmortems/{postmortem_id}/status",
        json={"status": "published"},
    )
    assert published.status_code == 200, published.text

    second_hop_requests: list[dict] = []

    async def deliver_to_evolution(url, *, json, **kwargs):
        second_hop_requests.append(json)
        headers = kwargs.get("headers") or {}
        return evolution_client.post("/api/evolution/proposals", json=json, headers=headers)

    with patch("httpx.AsyncClient.post", side_effect=deliver_to_evolution):
        asyncio.run(postmortems_main.process_postmortems_outbox())

    assert len(second_hop_requests) == 1
    delivered = second_hop_requests[0]
    assert delivered["metadata"]["bridge_contract"] == "on_postmortem_published"
    assert delivered["delivery_event"]["payload"]["postmortem_id"] == postmortem_id
    assert delivered["delivery_event"]["payload"]["incident_id"] == incident.incident_id

    decisions = evolution_main.store.list_all()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.linked_postmortem_id == postmortem_id
    assert decision.linked_incident_id == incident.incident_id
    assert decision.target_id == incident.artifact_id
    assert decision.decision_state == "proposed"

    exact_replay = evolution_client.post("/api/evolution/proposals", json=delivered)
    assert exact_replay.status_code == 200, exact_replay.text
    assert exact_replay.json()["decision_id"] == decision.decision_id
    assert len(evolution_main.store.list_all()) == 1
