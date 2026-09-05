from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}


def test_incidents_and_reviews_read_live_service_data_without_snapshot_fallback() -> None:
    responses = {
        ("http://incidents:8090", "/api/incidents"): [
            {
                "incident_id": "inc-console-data-001",
                "title": "Paper runtime drawdown threshold breach",
                "severity": "high",
                "status": "open",
                "created_at": "2026-06-15T10:00:00Z",
                "binding_id": "rb-console-data-001",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-console-data-001",
                "capital_pool_id": "pool-console-data-001",
                "persona_capital_binding_id": "pcb-console-data-001",
                "artifact_id": "artifact-console-data-001",
                "artifact_version": "2026.06.15",
                "runtime_id": "runtime-console-data-001",
                "trace_id": "trace-console-data-001",
                "telemetry_event_ids": ["tel-console-data-001"],
                "evidence_summary": "Produced by the incidents service threshold consumer.",
            }
        ],
        ("http://governance:8082", "/api/governance/approvals"): [
            {
                "decision_id": "apv-console-data-review-001",
                "target_type": "model_artifact",
                "target_id": "artifact-console-data-001",
                "target_version": "2026.06.15",
                "decision": None,
                "decision_state": "under_review",
                "actor_role": "governance_reviewer",
                "actor_id": "reviewer-console-data",
                "rationale": None,
                "created_at": "2026-06-15T10:01:00Z",
                "decided_at": None,
                "risk_level": "medium",
                "evidence_refs": [
                    {
                        "ref_type": "incident",
                        "ref_id": "inc-console-data-001",
                    }
                ],
            }
        ],
    }

    original_store = bff_main.read_store
    ports = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "incidents": {
                record["incident_id"]: record
                for record in responses[("http://incidents:8090", "/api/incidents")]
            },
        },
        ooda_management_kwargs={
            "approval_decisions": responses[("http://governance:8082", "/api/governance/approvals")],
        },
    )
    ports.dataset_source = lambda _dataset: "service_client"
    bff_main.read_store = ports
    try:
        client = TestClient(bff_main.app)
        incidents = client.get("/bff/incidents", headers=HEADERS)
        reviews = client.get("/bff/reviews", headers=HEADERS)
    finally:
        bff_main.read_store = original_store

    assert incidents.status_code == 200, incidents.text
    incident_payload = incidents.json()
    assert incident_payload["meta"]["surfaces"]["incidents"]["status"] == "ok"
    assert incident_payload["meta"]["surfaces"]["incidents"]["source"] == "service_client"
    assert incident_payload["page_info"]["total"] == 1
    incident = incident_payload["items"][0]
    assert incident["incident_id"] == "inc-console-data-001"
    assert incident["runtime_id"] == "runtime-console-data-001"
    assert incident["title"] == "Paper runtime drawdown threshold breach"

    assert reviews.status_code == 200, reviews.text
    review_payload = reviews.json()
    assert review_payload["meta"]["surfaces"]["review_queue"]["status"] == "ok"
    assert review_payload["meta"]["surfaces"]["review_queue"]["source"] == "service_client"
    assert len(review_payload["items"]) == 1
    review = review_payload["items"][0]
    assert review["item_id"] == "review-apv-console-data-review-001"
    assert review["item_type"] == "ApprovalDecision"
    assert review["risk_level"] == "medium"
    assert review["review_summary"]["linked_approval_decision_id"] == "apv-console-data-review-001"
