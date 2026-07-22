"""API contract tests for Trade Lesson Candidates memory governance endpoints."""

from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("services.memory.main")
from services.persona.lesson_governance import utc_now


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Setup temporary store files
    candidate_store_path = tmp_path / "trade_lesson_candidates.json"
    persona_store_path = tmp_path / "persona_memory_entries.json"
    institutional_store_path = tmp_path / "institutional_memory_entries.json"

    # Use monkeypatch to redirect environment variables
    monkeypatch.setenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", str(candidate_store_path))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(persona_store_path))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(institutional_store_path))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")

    # Reload store singletons for the test
    main._candidate_store.cache_clear() if hasattr(main._candidate_store, "cache_clear") else None
    main._persona_store.cache_clear() if hasattr(main._persona_store, "cache_clear") else None
    main._store.cache_clear() if hasattr(main._store, "cache_clear") else None

    return TestClient(main.app)


def make_valid_candidate_payload(overrides: dict | None = None) -> dict:
    base = {
        "lesson_candidate_id": str(uuid.uuid4()),
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4())],
        "persona_id": "persona-macro",
        "scope": "strategy",
        "proposed_change": "Reduce holding period target by 2 hours",
        "confidence": 0.75,
        "review_state": "proposed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "expiry": utc_now(),
        "reflection_version": "v1",
    }
    if overrides:
        base.update(overrides)
    return base


def test_api_create_and_get_lesson_candidate(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    resp = client.post("/api/memory/trade-lessons", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["lesson_candidate_id"] == payload["lesson_candidate_id"]
    assert created["review_state"] == "proposed"

    resp = client.get(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}")
    assert resp.status_code == 200
    retrieved = resp.json()
    assert retrieved["lesson_candidate_id"] == payload["lesson_candidate_id"]

    resp = client.get("/api/memory/trade-lessons")
    assert resp.status_code == 200
    listed = resp.json()["candidates"]
    assert len(listed) == 1
    assert listed[0]["lesson_candidate_id"] == payload["lesson_candidate_id"]


def test_api_submit_review(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    client.post("/api/memory/trade-lessons", json=payload)

    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/submit-review")
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["review_state"] == "pending_review"


def test_api_decide_gate_check_failure(client: TestClient) -> None:
    # Pattern candidate (scope=strategy) but only 1 episode -> endorsement blocked
    payload = make_valid_candidate_payload({"scope": "strategy", "trade_episode_ids": [str(uuid.uuid4())]})
    client.post("/api/memory/trade-lessons", json=payload)

    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "episodes": [],
        "actor_roles": ["operator"],
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 422
    assert "Endorsement blocked by evaluation gates" in resp.json()["detail"]["message"]


def test_api_decide_and_merge_success(client: TestClient) -> None:
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bear_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    payload = make_valid_candidate_payload({
        "scope": "strategy",
        "trade_episode_ids": ["ep1", "ep2", "ep3"],
    })
    client.post("/api/memory/trade-lessons", json=payload)

    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "episodes": episodes,
        "actor_roles": ["operator"],
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "endorsed"

    # Merge to memory
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "merged"

    # Query persona memory entries to confirm it exists
    resp = client.get("/api/memory/retrieve", params={
        "actor_id": "op-alice",
        "actor_roles": "operator",
        "session_id": "session-123",
        "persona_id": "persona-macro",
        "scope": "persona",
    })
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["entry"]["memory_id"] == f"pmem-lesson-{payload['lesson_candidate_id']}"


def test_api_rbac_authorization_failures(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    client.post("/api/memory/trade-lessons", json=payload)

    # 1. Decide with unauthorized role in header (e.g. trainer_session) -> 403
    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "actor_roles": ["operator"], # Attempt spoof in body
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "trainer_session"}
    )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"]["message"]

    # 2. Decide without actor_roles Header (X-Actor-Roles) -> 403
    decide_payload_no_roles = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload_no_roles,
        headers={"X-Actor-ID": "op-alice"} # Missing X-Actor-Roles
    )
    assert resp.status_code == 403

    # 3. Merge with unauthorized role in header -> 403
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "trainer_session"}
    )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"]["message"]

    # 4. Merge without role in header -> 403
    resp = client.post(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge")
    assert resp.status_code == 403


def test_api_decide_receipt_validation_sensitive(client: TestClient, monkeypatch) -> None:
    # Sensitive change: scope is 'risk'
    payload = make_valid_candidate_payload({
        "scope": "risk",
        "proposed_change": "Change leverage limit from 2x to 3x",
    })
    client.post("/api/memory/trade-lessons", json=payload)

    audit_receipt_id = str(uuid.uuid4())
    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
    }

    # Case A: Mock governance approval to return None (not found / spoofed) -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: None)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "not found" in resp.json()["detail"]["message"]

    # Case B: Mock governance approval with mismatched persona_id -> 403
    mock_mismatched_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-micro",  # candidate is persona-macro
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_mismatched_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "persona mismatch" in resp.json()["detail"]["message"]

    # Case C: Mock governance approval in unapproved state -> 403
    mock_unapproved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "rejected",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_unapproved_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "is not approved" in resp.json()["detail"]["message"]

    # Case D: Mock governance approved decision matching persona_id -> 200
    mock_approved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "endorsed"


def test_api_promotion_gates(client: TestClient, monkeypatch) -> None:
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bear_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    payload = make_valid_candidate_payload({
        "scope": "strategy",
        "trade_episode_ids": ["ep1", "ep2", "ep3"],
        "target_env": "paper",
        "promotion_stage": "proposed",
    })
    client.post("/api/memory/trade-lessons", json=payload)

    # Set up mock governance decision
    audit_receipt_id = str(uuid.uuid4())
    mock_approved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)

    # 1. Promote to live directly from paper -> 422 (TradeLessonCandidateError block)
    decide_live_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
        "target_env": "live",
        "episodes": episodes,
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_live_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 422
    assert "Promotion to live is blocked" in resp.json()["detail"]["message"]

    # 2. Promote to canary -> 200
    decide_canary_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
        "target_env": "canary",
        "episodes": episodes,
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_canary_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["target_env"] == "canary"
    assert resp.json()["promotion_stage"] == "canary_approved"

    # 3. Promote to live from canary_approved -> 200
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_live_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["target_env"] == "live"
    assert resp.json()["promotion_stage"] == "live_approved"


def test_api_promotion_stage_bypass_repro(client: TestClient, monkeypatch) -> None:
    # Repro/Regression test for API-level promotion_stage bypass attempt
    episodes = [
        {"trade_episode_id": "ep1", "regime": "bull_market"},
        {"trade_episode_id": "ep2", "regime": "bear_market"},
        {"trade_episode_id": "ep3", "regime": "bull_market"},
    ]
    payload = make_valid_candidate_payload({
        "scope": "strategy",
        "trade_episode_ids": ["ep1", "ep2", "ep3"],
        "target_env": "paper",
        "promotion_stage": "proposed",
    })
    client.post("/api/memory/trade-lessons", json=payload)

    audit_receipt_id = str(uuid.uuid4())
    mock_approved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)

    # 1. Attempt to endorse target_env=paper with promotion_stage=canary_approved via API -> should return 422
    decide_bypass_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
        "target_env": "paper",
        "promotion_stage": "canary_approved",
        "episodes": episodes,
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_bypass_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 422
    assert "Invalid promotion_stage" in resp.json()["detail"]["message"]

    # 2. Verify that the candidate is still "proposed" stage
    resp = client.get(f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}")
    assert resp.status_code == 200
    assert resp.json()["target_env"] == "paper"
    assert resp.json()["promotion_stage"] == "proposed"

    # 3. Attempt to endorse directly to live bypassing canary stage -> should return 422
    decide_live_bypass_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
        "target_env": "live",
        "promotion_stage": "live_approved",
        "episodes": episodes,
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_live_bypass_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 422
    assert "Promotion to live is blocked" in resp.json()["detail"]["message"]


def test_api_create_candidate_fail_closed_canary_live(client: TestClient) -> None:
    # 1. Reject target_env="canary" via API -> 422
    payload_canary = make_valid_candidate_payload({"target_env": "canary"})
    resp = client.post("/api/memory/trade-lessons", json=payload_canary)
    assert resp.status_code == 422
    assert "Cannot create candidate with target_env 'canary'" in resp.json()["detail"]["message"]

    # 2. Reject target_env="live" via API -> 422
    payload_live = make_valid_candidate_payload({"target_env": "live"})
    resp = client.post("/api/memory/trade-lessons", json=payload_live)
    assert resp.status_code == 422
    assert "Cannot create candidate with target_env 'live'" in resp.json()["detail"]["message"]

    # 3. Reject promotion_stage="canary_approved" via API -> 422
    payload_stage = make_valid_candidate_payload({"promotion_stage": "canary_approved"})
    resp = client.post("/api/memory/trade-lessons", json=payload_stage)
    assert resp.status_code == 422
    assert "Cannot create candidate with promotion_stage 'canary_approved'" in resp.json()["detail"]["message"]


def test_api_spoofed_role_negative(client: TestClient) -> None:
    payload = make_valid_candidate_payload()
    client.post("/api/memory/trade-lessons", json=payload)

    # Attempt to spoof role via body but passing non-authorized roles in headers -> 403
    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
        "actor_roles": ["operator"], # Spoofing role in JSON body
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "trainer_session"} # Headers are authoritative!
    )
    assert resp.status_code == 403


def test_api_authz_dependency_unavailable_negative(client: TestClient, monkeypatch) -> None:
    payload = make_valid_candidate_payload()
    client.post("/api/memory/trade-lessons", json=payload)

    # Set authz mode to remote, but do not provide a URL or mock failure to simulate dependency unavailable
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "remote")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTHZ_URL", "") # Empty -> unconfigured

    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved",
        "audit_receipt_id": str(uuid.uuid4()),
    }
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "Governance authz unconfigured" in resp.json()["detail"]["message"]

    # Now provide a URL but simulate a connection failure
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTHZ_URL", "http://invalid-governance-service-url")
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "Governance authz service unavailable" in resp.json()["detail"]["message"]


def test_api_merge_revalidate_receipt_sensitive(client: TestClient, monkeypatch) -> None:
    # Sensitive change: scope is 'risk'
    payload = make_valid_candidate_payload({
        "scope": "risk",
        "proposed_change": "Change leverage limit from 2x to 3x",
    })
    client.post("/api/memory/trade-lessons", json=payload)

    audit_receipt_id = str(uuid.uuid4())
    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
    }

    # Endorse first with valid mocked approval
    mock_approved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200

    # Case A: At merge, mock decision to be None (revoked or not found) -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: None)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "not found at merge" in resp.json()["detail"]["message"]

    # Case B: At merge, mock decision is not approved (rejected) -> 403
    mock_rejected_decision = {
        "decision_id": audit_receipt_id,
        "decision": "rejected",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_rejected_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "is not approved at merge" in resp.json()["detail"]["message"]

    # Case C: At merge, mock decision matches approved -> 200
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "merged"


def test_api_decide_and_merge_target_validation_negative(client: TestClient, monkeypatch) -> None:
    # Create a candidate
    payload = make_valid_candidate_payload({
        "scope": "risk",
        "proposed_change": "Change leverage limit from 2x to 3x",
    })
    client.post("/api/memory/trade-lessons", json=payload)

    audit_receipt_id = str(uuid.uuid4())
    decide_payload = {
        "action": "endorse",
        "operator_id": "op-alice",
        "reason": "Approved decision app-123 and deployment plan-456",
        "audit_receipt_id": audit_receipt_id,
        "actor_roles": ["operator"],
    }

    # Case A: Missing target_id at decide -> 403
    mock_missing_target_id = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_missing_target_id)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_id is missing or empty" in resp.json()["detail"]["message"]

    # Case B: Mismatched target_id at decide -> 403
    mock_wrong_target_id = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": "wrong-id",
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_wrong_target_id)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_id mismatch" in resp.json()["detail"]["message"]

    # Case C: Missing target_version at decide -> 403
    mock_missing_target_version = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_missing_target_version)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_version is missing or empty" in resp.json()["detail"]["message"]

    # Case D: Mismatched target_version at decide -> 403
    mock_wrong_target_version = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": "v999",
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_wrong_target_version)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_version mismatch" in resp.json()["detail"]["message"]

    # Endorse it with correct mock to test merge negative cases
    mock_approved_decision = {
        "decision_id": audit_receipt_id,
        "decision": "approved",
        "decision_state": "decided",
        "persona_id": "persona-macro",
        "target_id": payload["lesson_candidate_id"],
        "target_version": payload["reflection_version"],
    }
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_approved_decision)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/decide",
        json=decide_payload,
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 200

    # Case E: Missing target_id at merge -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_missing_target_id)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_id is missing or empty at merge" in resp.json()["detail"]["message"]

    # Case F: Mismatched target_id at merge -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_wrong_target_id)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_id mismatch" in resp.json()["detail"]["message"]

    # Case G: Missing target_version at merge -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_missing_target_version)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_version is missing or empty at merge" in resp.json()["detail"]["message"]

    # Case H: Mismatched target_version at merge -> 403
    monkeypatch.setattr(main, "_fetch_governance_approval", lambda d_id: mock_wrong_target_version)
    resp = client.post(
        f"/api/memory/trade-lessons/{payload['lesson_candidate_id']}/merge",
        headers={"X-Actor-ID": "op-alice", "X-Actor-Roles": "operator"}
    )
    assert resp.status_code == 403
    assert "target_version mismatch" in resp.json()["detail"]["message"]
