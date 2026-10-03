"""PERSONA-OWNER-READBACK-20261002: ranking and promotion reviews read the Governance ApprovalDecision."""
from __future__ import annotations

import hashlib
import json

import pytest

from services.control_plane.governance.approval_decision import ApprovalDecision
from services.control_plane.bff import test_bff_promotion_review_governance as gov_test
from services.control_plane.bff.pm12 import evaluator_results
from services.control_plane.bff.ports.ooda_management import ManagementReviewQueuePort

HEADERS = gov_test.OPERATOR_HEADERS
TENANT = gov_test._PM12_ELIGIBLE_TENANT_ID
FROM = "paper_owner"
URL = "/bff/management/quarterly-ranking/recommendations"
REVIEWS = "/bff/management/promotion-reviews"


def _items(client):
    response = client.get(URL, headers=HEADERS, params={"quarter": "2026-Q1", "page_size": 50})
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def _decision(rec, **over):
    row = {
        "decision_id": "pev-1", "tenant_id": TENANT, "target_id": rec["persona_id"],
        "target_type": "persona_lifecycle_transition", "proposal_id": rec["recommendation_id"],
        "proposal_content_digest": hashlib.sha256(json.dumps({
            "persona_id": rec["persona_id"], "action_id": "promote_to_canary_candidate", "from_state": FROM,
            "rationale": "Provider.", "evidence_ref_ids": [],
        }, sort_keys=True).encode()).hexdigest(), "decision_state": "proposed",
        "decision": None, "decided_at": None, "actor_id": None, "version": 0,
        "metadata": {"subject": {"persona_id": rec["persona_id"], "from_state": FROM, "to_state": "frozen"}},
    }
    over = {**over, "metadata": {**row["metadata"], **over.get("metadata", {})}}
    row.update(over)
    return row


@pytest.fixture
def saved_proposal(monkeypatch):
    def install(client, request):
        first = _items(client)[0]
        persona, snap = first["persona_id"], first["ranking_snapshot_id"]
        rec_id = f"pm12-2026-q1-{persona}-promote_to_canary_candidate"
        saved = {"run_id": "r1", "evaluated_at": "2026-01-01T00:00:00+00:00", "items": [{
            "persona_id": persona, "action_id": "promote_to_canary_candidate", "from_state": FROM, "rationale": "Provider.",
            "evidence_ref_ids": [], "ranking_snapshot_id": snap, "recommendation_id": rec_id,
            "governance_request": request,
        }]}
        monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *a, **k: saved)
        return {"persona_id": persona, "recommendation_id": rec_id}
    return install


def _both(client):
    rec = _items(client)[0]
    review = client.get(REVIEWS, headers=HEADERS, params={"quarter": "2026-Q1"}).json()["data"]["items"][0]
    return rec, review


def test_advisory_entry_is_a_non_executable_report(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        saved_proposal(client, None)
        rec, review = _both(client)
        assert rec["human_review_state"]["status"] == "advisory_report"
        assert review["status"] == "advisory_report" and review["allowedActions"]["canSubmit"] is False
        assert "recommended_not_submitted" not in str((rec, review))
        assert "submit" not in review["links"]


@pytest.mark.parametrize(("over", "status", "votes"), [
    ({}, "pending_human_gate", 0),
    ({"metadata": {"approvals": [{"actor_id": "a"}]}, "decision_state": "under_review"}, "pending_human_gate", 1),
    ({"decision_state": "decided", "decision": "approved", "decided_at": "t", "actor_id": "b",
      "metadata": {"approvals": [{"actor_id": "a"}, {"actor_id": "b"}]}}, "decision_accepted", 2),
])
def test_ranking_and_review_project_the_same_owner_decision(saved_proposal, over, status, votes):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        row = _decision({**ref}, **over)
        store.get_approval_decision = lambda decision_id: row if decision_id == "pev-1" else None
        rec, review = _both(client)
        assert rec["human_review_state"]["status"] == review["status"] == status
        owner = review["owner_decision"]
        assert owner == rec["human_review_state"]["owner_decision"]
        assert owner["decision_id"] == "pev-1" and owner["vote_count"] == votes and owner["available"] is True
        assert review["allowedActions"] == {k: False for k in review["allowedActions"]}
        assert rec["rationale"] == review["rationale"] == "Provider."
        # refresh and an independent app instance read the same authoritative record
        assert _both(client)[1]["owner_decision"] == owner
    with gov_test._isolated_client() as (other, other_store, _commands):
        saved_proposal(other, {"decision_id": "pev-1", "to_state": "frozen"})
        other_store.get_approval_decision = lambda decision_id: row
        assert _both(other)[1]["owner_decision"] == owner


@pytest.mark.parametrize("over", [
    {"proposal_id": "someone-else"},   # conflicting content/proposal identity
    {"tenant_id": "foreign-tenant"},   # unauthorized tenant
    {"target_id": "persona-other"},
    {"target_type": "deployment_plan"},
    {"metadata": {"subject": {"persona_id": "x", "from_state": FROM, "to_state": "canary_candidate"}}},
    {"proposal_content_digest": "0" * 64},   # source content changed after proposal
])
def test_conflicting_or_foreign_owner_record_is_unavailable_not_projected(saved_proposal, over):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        row = _decision(ref, decision_state="decided", decision="approved", **over)
        store.get_approval_decision = lambda decision_id: row
        rec, review = _both(client)
        assert review["status"] == "owner_unavailable" and review["owner_decision"] == {"decision_id": "pev-1", "available": False}
        assert "approved" not in str(review["owner_decision"])
        assert rec["links"]["human_inbox"] is None and review["links"]["human_inbox"] is None


def test_missing_or_failing_owner_is_unavailable(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        store.get_approval_decision = lambda decision_id: None
        assert _both(client)[1]["status"] == "owner_unavailable"

        def boom(decision_id):
            raise RuntimeError("owner down")

        store.get_approval_decision = boom
        assert _both(client)[1]["status"] == "owner_unavailable"


def test_unauthenticated_caller_gets_no_owner_record(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        store.get_approval_decision = lambda decision_id: _decision(ref)
        assert client.get(URL, params={"quarter": "2026-Q1"}).status_code in (401, 403)


def test_obsolete_submit_route_is_retired():
    with gov_test._isolated_client() as (client, _store, commands):
        response = client.post(f"{URL}/anything/submit", headers=HEADERS, json={})
        assert response.status_code == 410, response.text
        assert commands._get_all_commands() == []


@pytest.mark.parametrize(("over", "state"), [
    ({}, "proposed"),
    ({"decision_state": "under_review", "metadata": {"approvals": [{"actor_id": "a"}]}}, "under_review"),
])
def test_pending_ranking_link_resolves_through_real_inbox_port(saved_proposal, over, state):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        row = _decision(ref, **over)
        store.get_approval_decision = lambda decision_id: row if decision_id == "pev-1" else None
        queue = ManagementReviewQueuePort(approval_decisions_reader=lambda: [row])
        store.list_approval_queue_items = queue.list_approval_queue_items
        rec, review = _both(client)
        link = rec["links"]["human_inbox"]
        assert link == review["links"]["human_inbox"] == "/bff/management/human-inbox/approval:pev-1"
        assert review["human_inbox_id"] == "approval:pev-1"
        assert rec["links"]["owner_decision"] == review["links"]["owner_decision"] == "/api/v1/approval-decisions/pev-1"
        assert rec["governance"]["decision_type"] == "ApprovalDecision"
        detail = client.get(link, headers=HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["approval_decision_id"] == "pev-1"
        assert detail.json()["data"]["status"] == state


@pytest.mark.parametrize("terminal", ["revoked", "superseded"])
def test_revoked_or_superseded_owner_state_is_terminal_without_inbox_link(saved_proposal, terminal):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        row = _decision(ref, decision_state=terminal)
        store.get_approval_decision = lambda decision_id: row if decision_id == "pev-1" else None
        queue = ManagementReviewQueuePort(approval_decisions_reader=lambda: [row])
        store.list_approval_queue_items = queue.list_approval_queue_items
        rec, review = _both(client)
        assert rec["human_review_state"]["status"] == review["status"] == f"decision_{terminal}"
        assert rec["human_review_state"]["decision_status"] == terminal
        assert rec["links"]["human_inbox"] is None and review["human_inbox_id"] is None
        assert review["owner_decision"]["decision_state"] == terminal and review["owner_decision"]["available"] is True
        assert rec["links"]["owner_decision"] == "/api/v1/approval-decisions/pev-1"


def test_final_decision_hands_off_to_governance_owner_not_terminal_inbox(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        row = _decision(ref, decision_state="decided", decision="approved", decided_at="t", actor_id="b")
        store.get_approval_decision = lambda decision_id: row if decision_id == "pev-1" else None
        queue = ManagementReviewQueuePort(approval_decisions_reader=lambda: [row])
        store.list_approval_queue_items = queue.list_approval_queue_items
        rec, review = _both(client)
        assert rec["links"]["human_inbox"] is None and review["human_inbox_id"] is None
        assert rec["links"]["owner_decision"] == review["links"]["owner_decision"] == "/api/v1/approval-decisions/pev-1"
        assert rec["human_review_state"]["status"] == "decision_accepted"


def test_unavailable_owner_has_no_inbox_handoff(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        store.get_approval_decision = lambda decision_id: None
        _rec, review = _both(client)
        assert review["human_inbox_id"] is None and review["links"]["human_inbox"] is None


def test_caller_of_another_tenant_never_reads_the_owner_record(saved_proposal, monkeypatch):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        reads = []
        store.get_approval_decision = lambda decision_id: reads.append(decision_id) or _decision(ref)
        monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "foreign-tenant")
        response = client.get(URL, headers=HEADERS, params={"quarter": "2026-Q1", "page_size": 50})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == [] and reads == []


def test_real_approval_decision_model_serialization_is_readable(saved_proposal):
    with gov_test._isolated_client() as (client, store, _commands):
        ref = saved_proposal(client, {"decision_id": "pev-1", "to_state": "frozen"})
        base = _decision(ref)
        model = ApprovalDecision.create_proposed(
            decision_id="pev-1", target_type="persona_lifecycle_transition", target_id=ref["persona_id"],
            target_version="1", risk_level="medium", tenant_id=TENANT, proposal_id=ref["recommendation_id"],
            proposal_content_digest=base["proposal_content_digest"],
            subject={"persona_id": ref["persona_id"], "from_state": FROM, "to_state": "frozen"},
        )
        store.get_approval_decision = lambda decision_id: model.to_dict()
        assert _both(client)[1]["status"] == "pending_human_gate"
