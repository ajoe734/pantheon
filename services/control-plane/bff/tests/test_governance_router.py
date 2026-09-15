"""Characterization and contract tests for the extracted Governance router."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router


EXPECTED_ROUTES = {
    ("GET", "/api/v1/approval-decisions"),
    ("POST", "/api/v1/approval-decisions"),
    ("GET", "/api/v1/approval-decisions/{decision_id}"),
    ("GET", "/api/v1/workbench/consultation"),
    ("POST", "/api/v1/consult/requests"),
    ("GET", "/api/v1/consult/requests"),
    ("GET", "/api/v1/consult/requests/{request_id}"),
    ("POST", "/api/v1/consult/requests/{request_id}/cancel"),
    ("GET", "/api/v1/committees"),
    ("GET", "/api/v1/committees/{committee_id}"),
    ("GET", "/api/v1/consult/memos"),
    ("GET", "/api/v1/consult/memos/{memo_id}"),
    ("GET", "/api/v1/operator/governance/review-queue"),
    ("GET", "/api/v1/operator/governance/approval-queue"),
    ("GET", "/api/v1/operator/governance/audit"),
    ("GET", "/api/v1/operator/mutation-review/{decision_id}"),
    ("GET", "/api/v1/personas/{persona_id}/consultations"),
    ("GET", "/api/v1/consultations/{session_id}"),
    ("GET", "/api/v1/consultations/{session_id}/participants"),
    ("GET", "/api/v1/consultations/{session_id}/outcome"),
    ("GET", "/api/v1/consultations/{session_id}/evidence"),
    ("GET", "/api/v1/consultations/{session_id}/transcript"),
    ("GET", "/api/v1/personas/{persona_id}/consult-policy"),
    ("GET", "/bff/approvals"),
    ("GET", "/bff/management/governance-ledger"),
    ("GET", "/bff/reviews"),
    ("POST", "/bff/reviews"),
    ("GET", "/bff/reviews/{review_id}"),
    ("POST", "/bff/reviews/{review_id}/actions/{action_id}"),
    ("GET", "/bff/reviews/{review_id}/validators"),
    ("GET", "/bff/reviews/{review_id}/audit"),
    ("GET", "/bff/approvals/{approval_id}/evidence"),
    ("GET", "/bff/approvals/{approval_id}"),
    ("POST", "/bff/approvals/{approval_id}/decide"),
    ("POST", "/bff/approvals/batch-decide"),
    ("GET", "/api/v1/operator/rollback-review/{rollback_id}"),
}


class MockGovernanceStore:
    def __init__(self) -> None:
        self.sources: Dict[str, str] = {}
        self.approval_decisions = {
            "approval-1": {
                "id": "approval-1",
                "decision_id": "approval-1",
                "decision_type": "DeploymentPlan",
                "decision_state": "pending",
                "risk_level": "high",
                "submitted_at": "2026-08-30T10:00:00Z",
                "evidence_refs": [{"ref_id": "evidence-1"}],
            },
            "approval-2": {
                "id": "approval-2",
                "decision_id": "approval-2",
                "decision_type": "StrategySpec",
                "decision_state": "approved",
                "outcome": "approved",
                "decided_at": "2026-08-30T11:00:00Z",
            },
        }
        self.approval_queue = [dict(record) for record in self.approval_decisions.values()]
        self.review_queue = [
            {
                "id": "review-1",
                "item_id": "review-1",
                "item_type": "deployment",
                "risk_level": "high",
                "status": "pending",
                "review_summary": {"validators": [{"id": "validator-1"}]},
            }
        ]
        self.audit_events = [
            {
                "id": "audit-1",
                "action_type": "approval.reviewed",
                "target_type": "Review",
                "target_id": "review-1",
                "actor": "operator-1",
                "timestamp": "2026-08-30T12:00:00Z",
            }
        ]
        self.consult_requests = {
            "request-1": {
                "request_id": "request-1",
                "from_persona_id": "persona-1",
                "target_type": "committee",
                "target_ref": "committee-1",
                "task": "Review allocation",
                "priority": "high",
                "consultation_type": "strategy_review",
                "status": "submitted",
                "created_at": "2026-08-30T09:00:00Z",
                "linked_session_id": "session-1",
                "request_to_session_status": "linked",
                "allowedActions": {"canCancel": True},
            }
        }
        self.committees = {
            "committee-1": {
                "committee_id": "committee-1",
                "status": "active",
                "consensus_state": "deliberating",
                "participant_roster": ["persona-1"],
            }
        }
        self.memos = {
            "memo-1": {
                "memo_id": "memo-1",
                "status": "published",
                "summary": "Proceed with conditions",
            }
        }
        self.personas = {"persona-1": {"id": "persona-1", "persona_id": "persona-1"}}
        self.consultations = {
            "session-1": {
                "session_id": "session-1",
                "persona_id": "persona-1",
                "consultation_type": "strategy_review",
                "status": "completed",
            }
        }
        self.evolution_decisions = {
            "mutation-1": {
                "decision_id": "mutation-1",
                "target_type": "artifact",
                "target_id": "artifact-1",
                "target_version": "v1",
                "action_type": "promote",
                "decision_state": "proposed",
                "risk_level": "medium",
                "created_at": "2026-08-30T08:00:00Z",
                "approval_decision_id": "approval-1",
            }
        }

    def dataset_source(self, dataset: str) -> str:
        return self.sources.get(dataset, "ok")

    def list_approval_decisions(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.approval_decisions.values())

    def get_approval_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.approval_decisions.get(decision_id)

    def list_approval_queue_items(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.approval_queue)

    def list_governance_review_queue_items(self, **kwargs: Any) -> List[Dict[str, Any]]:
        items = list(self.review_queue)
        if kwargs.get("statuses"):
            items = [item for item in items if item["status"] in kwargs["statuses"]]
        return items

    def list_governance_audit_events(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.audit_events)

    def create_consult_request(self, **payload: Any) -> Dict[str, Any]:
        request_id = f"request-{len(self.consult_requests) + 1}"
        record = {
            "request_id": request_id,
            **payload,
            "status": "created",
            "linked_session_id": None,
            "request_to_session_status": "pending",
            "allowedActions": {"canCancel": True},
        }
        self.consult_requests[request_id] = record
        return record

    def list_consult_requests(self, **kwargs: Any) -> List[Dict[str, Any]]:
        items = list(self.consult_requests.values())
        statuses = kwargs.get("statuses")
        if statuses:
            items = [item for item in items if item["status"] in statuses]
        return items

    def get_consult_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self.consult_requests.get(request_id)

    def cancel_consult_request(self, request_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        record = self.consult_requests.get(request_id)
        if record:
            record.update(status="canceled", canceled_at=kwargs.get("canceled_at"))
        return record

    def list_committees(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.committees.values())

    def get_committee(self, committee_id: str) -> Optional[Dict[str, Any]]:
        return self.committees.get(committee_id)

    def list_consult_memos(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.memos.values())

    def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
        return self.memos.get(memo_id)

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self.personas.get(persona_id)

    def list_consultations_for_persona(self, persona_id: str, **_: Any) -> List[Dict[str, Any]]:
        return [item for item in self.consultations.values() if item["persona_id"] == persona_id]

    def get_consultation(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.consultations.get(session_id)

    def get_consultation_participants(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        if session_id not in self.consultations:
            return None
        return [{"session_id": "participant-session-1", "persona_id": "persona-1", "role": "requester"}]

    def get_consultation_outcome(self, session_id: str) -> Optional[Dict[str, Any]]:
        return {"decision": "approve"} if session_id in self.consultations else None

    def get_consultation_evidence(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        return [{"ref_id": "evidence-1"}] if session_id in self.consultations else None

    def get_consult_transcript(self, session_id: str, **_: Any) -> Optional[Dict[str, Any]]:
        return {"items": [{"sequence_no": 1, "content": "Review"}]} if session_id in self.consultations else None

    def get_consult_policy(self, persona_id: str) -> Optional[Dict[str, Any]]:
        if persona_id != "persona-1":
            return None
        return {"id": "policy-1", "persona_id": persona_id, "required_reviewers": 1}

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.evolution_decisions.get(decision_id)


def build_client(store: Optional[MockGovernanceStore] = None, **router_kwargs: Any) -> TestClient:
    store = store or MockGovernanceStore()
    app = FastAPI()
    app.include_router(create_governance_router(get_read_store=lambda: store, **router_kwargs))
    return TestClient(app)


def test_router_registers_exactly_the_35_catalog_routes() -> None:
    router = create_governance_router()
    routes = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert routes == EXPECTED_ROUTES
    assert len(router.routes) == 36


def test_typed_approval_detail_replaces_generic_alias_and_preserves_envelope() -> None:
    client = build_client()

    listed = client.get("/api/v1/approval-decisions")
    assert listed.status_code == 200
    assert {item["decision_id"] for item in listed.json()["data"]} == {"approval-1", "approval-2"}

    canonical = client.get("/api/v1/approval-decisions/approval-1")
    compatibility = client.get("/bff/approvals/approval-1")
    assert canonical.status_code == compatibility.status_code == 200
    assert canonical.json()["data"]["decision_id"] == "approval-1"
    assert compatibility.json()["data"]["decision_id"] == "approval-1"
    assert client.get("/bff/approvals/missing").status_code == 404


def test_create_approval_decision_validation_dry_run_and_idempotent_replay() -> None:
    client = build_client()
    headers = {"Idempotency-Key": "approval-create-1"}
    payload = {"plan_id": "plan-1", "decision": "approve", "memo": "Approved with evidence"}

    created = client.post("/api/v1/approval-decisions", json=payload, headers=headers)
    replayed = client.post("/api/v1/approval-decisions", json=payload, headers=headers)
    assert created.status_code == replayed.status_code == 202
    assert created.json() == replayed.json()

    dry_run = client.post(
        "/api/v1/approval-decisions",
        json={**payload, "plan_id": "plan-2"},
        headers={"Idempotency-Key": "approval-create-2", "X-Dry-Run": "true"},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["meta"]["dryRun"] is True
    assert client.post("/api/v1/approval-decisions", json={"plan_id": "plan-1"}).status_code == 422


def test_consult_request_committee_memo_and_workbench_routes() -> None:
    client = build_client()
    workbench = client.get("/api/v1/workbench/consultation")
    assert workbench.status_code == 200
    assert workbench.json()["data"]["summary"] == {
        "request_count": 1,
        "committee_count": 1,
        "memo_count": 1,
    }

    payload = {
        "from_persona_id": "persona-1",
        "target_type": "committee",
        "target_ref": "committee-1",
        "task": "Review allocation",
        "context_refs": [{"type": "artifact", "id": "artifact-1"}],
        "priority": "high",
        "consultation_type": "pre_deployment",
    }
    created = client.post("/api/v1/consult/requests", json=payload)
    assert created.status_code == 200
    request_id = created.json()["request_id"]
    assert client.get(f"/api/v1/consult/requests/{request_id}").status_code == 200
    assert client.post(f"/api/v1/consult/requests/{request_id}/cancel").json()["status"] == "canceled"

    requests = client.get("/api/v1/consult/requests?page_size=1")
    assert requests.status_code == 200
    assert len(requests.json()["data"]) == 1
    assert client.get("/api/v1/committees").json()["page_info"]["total"] == 1
    assert client.get("/api/v1/committees/committee-1").json()["committee_id"] == "committee-1"
    assert client.get("/api/v1/consult/memos").json()["page_info"]["total"] == 1
    assert client.get("/api/v1/consult/memos/memo-1").json()["memo_id"] == "memo-1"


def test_governance_queues_audit_and_mutation_review() -> None:
    client = build_client()
    review_queue = client.get("/api/v1/operator/governance/review-queue?status=pending")
    approval_queue = client.get("/api/v1/operator/governance/approval-queue")
    audit = client.get("/api/v1/operator/governance/audit")
    mutation = client.get("/api/v1/operator/mutation-review/mutation-1")

    assert review_queue.status_code == approval_queue.status_code == audit.status_code == mutation.status_code == 200
    assert review_queue.json()["items"][0]["item_id"] == "review-1"
    assert approval_queue.json()["page_info"]["total"] == 2
    assert audit.json()["items"][0]["id"] == "audit-1"
    assert mutation.json()["approval_decision"]["decision_id"] == "approval-1"
    assert client.get("/api/v1/operator/mutation-review/missing").status_code == 404


def test_consultation_session_and_policy_read_routes() -> None:
    client = build_client()
    listed = client.get("/api/v1/personas/persona-1/consultations")
    detail = client.get("/api/v1/consultations/session-1")
    participants = client.get("/api/v1/consultations/session-1/participants")
    outcome = client.get("/api/v1/consultations/session-1/outcome")
    evidence = client.get("/api/v1/consultations/session-1/evidence")
    transcript = client.get("/api/v1/consultations/session-1/transcript")
    policy = client.get("/api/v1/personas/persona-1/consult-policy")

    assert listed.json()["data"][0]["_links"]["self"].endswith("session-1")
    assert detail.json()["data"]["session_id"] == "session-1"
    assert participants.json()["meta"]["total"] == 1
    assert outcome.json()["data"]["decision"] == "approve"
    assert evidence.json()["meta"]["supporting_counts"]["redacted_evidence_count"] == 0
    assert transcript.json()["items"][0]["sequence_no"] == 1
    assert policy.json()["data"]["id"] == "policy-1"
    assert client.get("/api/v1/personas/missing/consultations").status_code == 404


def test_bff_approval_review_and_governance_ledger_compatibility() -> None:
    client = build_client()

    approvals = client.get("/bff/approvals")
    evidence = client.get("/bff/approvals/approval-1/evidence")
    ledger = client.get("/bff/management/governance-ledger?source_type=approval")
    reviews = client.get("/bff/reviews")
    review = client.get("/bff/reviews/review-1")
    validators = client.get("/bff/reviews/review-1/validators")
    audit = client.get("/bff/reviews/review-1/audit")

    assert approvals.json()["count"] == 1
    assert evidence.json()["evidence"][0]["ref_id"] == "evidence-1"
    # Two approval records plus the approval-scoped audit entry.
    assert ledger.json()["data"]["summary"]["approval_count"] == 3
    assert reviews.json()["items"][0]["item_id"] == "review-1"
    assert review.json()["data"]["item_id"] == "review-1"
    assert validators.json()["validators"][0]["id"] == "validator-1"
    assert audit.json()["events"][0]["id"] == "audit-1"

    created = client.post("/bff/reviews", json={"id": "review-2"}, headers={"Idempotency-Key": "review-create-1"})
    acted = client.post(
        "/bff/reviews/review-1/actions/approve",
        json={"reason": "Evidence verified"},
        headers={"Idempotency-Key": "review-action-1"},
    )
    assert created.status_code == acted.status_code == 202
    assert created.json()["status"] == acted.json()["status"] == "accepted"


def test_single_and_batch_approval_decisions_validate_and_report_partial_results() -> None:
    client = build_client()
    single = client.post(
        "/bff/approvals/approval-1/decide",
        json={"decision": "approve"},
        headers={"Idempotency-Key": "approval-decide-1"},
    )
    assert single.status_code == 202
    assert single.json()["data"]["action"] == "approve"
    assert client.post(
        "/bff/approvals/approval-1/decide",
        json={"decision": "reject"},
        headers={"Idempotency-Key": "approval-decide-2"},
    ).status_code == 422

    batch = client.post(
        "/bff/approvals/batch-decide",
        json={
            "decisions": [
                {"id": "approval-1", "decision": "approve"},
                {"id": "missing", "decision": "approve"},
            ]
        },
        headers={"Idempotency-Key": "approval-batch-1"},
    )
    assert batch.status_code == 207
    assert batch.json()["status"] == "partial"
    assert batch.json()["summary"] == {"total": 2, "accepted": 1, "failed": 1}


def test_unavailable_approval_detail_fails_closed_instead_of_returning_placeholder() -> None:
    store = MockGovernanceStore()
    store.sources["approval_decisions"] = "missing"
    client = build_client(store)

    response = client.get("/api/v1/approval-decisions/missing")
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
