"""
Unit tests for the Governance Service API.

Runs in-process via FastAPI TestClient — no server process required.
Each test uses an isolated temp directory for storage so tests do not
interfere with each other.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# ---- Isolate storage BEFORE importing main ----
_tmp = tempfile.mkdtemp(prefix="gov_test_")
os.environ["GOVERNANCE_DATA_DIR"] = _tmp

# ---- Make platform objects importable ----
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

# ---- Import after env vars are set ----
from fastapi.testclient import TestClient  # noqa: E402

from services.governance import main  # noqa: E402  -- re-reads GOVERNANCE_DATA_DIR at module level
from services.governance.main import app, store  # noqa: E402

client = TestClient(app)


def uid() -> str:
    return f"apv-t-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Health / write-authority
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_write_authority_matrix():
    r = client.get("/api/governance/write-authority")
    assert r.status_code == 200
    data = r.json()
    assert "matrix" in data
    risk_levels = {e["risk_level"] for e in data["matrix"]}
    assert {"low", "medium", "high", "critical"}.issubset(risk_levels)
    # Each entry must carry authorized_roles and revoke_roles
    for entry in data["matrix"]:
        assert "authorized_roles" in entry
        assert "revoke_roles" in entry


def test_authz_allows_institutional_memory_retrieval_for_operator():
    r = client.post("/api/governance/authz/check", json={
        "action": "memory.retrieve",
        "actor_id": "op-1",
        "actor_roles": ["operator"],
        "resource": {"scope": "institutional"},
        "context": {"session_id": "sess-1"},
    })
    assert r.status_code == 200
    assert r.json() == {
        "allowed": True,
        "reason": "authorized",
        "policy_version": "governance-authz.v1",
    }


def test_authz_allows_persona_memory_retrieval_for_operator():
    r = client.post("/api/governance/authz/check", json={
        "action": "memory.retrieve",
        "actor_id": "op-1",
        "actor_roles": ["operator"],
        "resource": {"scope": "persona", "persona_id": "persona-alpha"},
        "context": {"session_id": "sess-1"},
    })
    assert r.status_code == 200
    assert r.json() == {
        "allowed": True,
        "reason": "authorized",
        "policy_version": "governance-authz.v1",
    }


def test_authz_rejects_cross_persona_session_memory_retrieval():
    r = client.post("/api/governance/authz/check", json={
        "action": "memory.retrieve",
        "actor_id": "persona-session-1",
        "actor_roles": ["persona_session"],
        "resource": {"scope": "both", "persona_id": "persona-beta"},
        "context": {"session_id": "sess-1", "session_persona_id": "persona-alpha"},
    })
    assert r.status_code == 200
    assert r.json()["allowed"] is False
    assert r.json()["reason"] == "persona_scope_mismatch"


def test_authz_lesson_decide_and_merge():
    # 1. Allowed for operator
    for act in ["lesson.decide", "lesson.merge"]:
        r = client.post("/api/governance/authz/check", json={
            "action": act,
            "actor_id": "op-1",
            "actor_roles": ["operator"],
        })
        assert r.status_code == 200
        assert r.json()["allowed"] is True

    # 2. Denied for trainer_session
    for act in ["lesson.decide", "lesson.merge"]:
        r = client.post("/api/governance/authz/check", json={
            "action": act,
            "actor_id": "op-1",
            "actor_roles": ["trainer_session"],
        })
        assert r.status_code == 200
        assert r.json()["allowed"] is False
        assert r.json()["reason"] == "lesson_governance_role_denied"


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------

def test_propose_returns_proposed_state():
    did = uid()
    r = client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-001",
        "target_version": "v1",
        "risk_level":     "low",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["decision_id"]    == did
    assert body["decision_state"] == "proposed"
    assert body["decision"]       is None


def test_propose_accepts_strategy_workshop_target():
    did = uid()
    r = client.post("/api/governance/approvals", json={
        "decision_id": did,
        "target_type": "strategy_workshop",
        "target_id": "workshop-public-api",
        "target_version": "workshop-version-public-api",
        "risk_level": "low",
        "tenant_id": "tenant-public-api",
        "owner_user_id": "user-public-api",
    })

    assert r.status_code == 201, r.text
    assert r.json()["target_type"] == "strategy_workshop"


def test_propose_rejects_noncanonical_workshop_alias():
    r = client.post("/api/governance/approvals", json={
        "decision_id": uid(),
        "target_type": "workshop",
        "target_id": "workshop-alias",
        "target_version": "workshop-version-alias",
    })

    assert r.status_code == 422


def test_propose_autogenerates_decision_id():
    r = client.post("/api/governance/approvals", json={
        "target_type":    "strategy_spec",
        "target_id":      "art-auto",
        "target_version": "v1",
    })
    assert r.status_code == 201
    assert r.json()["decision_id"].startswith("apv-")


def test_duplicate_decision_id_rejected():
    did = uid()
    body = {
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-dup",
        "target_version": "v1",
    }
    assert client.post("/api/governance/approvals", json=body).status_code == 201
    assert client.post("/api/governance/approvals", json=body).status_code == 409


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------

def test_get_returns_decision():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-1",
        "target_version": "v2",
    })
    r = client.get(f"/api/governance/approvals/{did}")
    assert r.status_code == 200
    assert r.json()["decision_id"] == did


def test_get_not_found():
    r = client.get("/api/governance/approvals/nonexistent-99")
    assert r.status_code == 404


def test_list_filter_by_target_id():
    target_id = f"art-list-{uuid.uuid4().hex[:6]}"
    for i in range(3):
        client.post("/api/governance/approvals", json={
            "target_type":    "strategy_spec",
            "target_id":      target_id,
            "target_version": f"v{i}",
        })
    r = client.get("/api/governance/approvals", params={"target_id": target_id})
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_list_filter_by_state():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-state",
        "target_version": "v1",
        "risk_level":     "low",
    })
    # Only proposed so far
    r = client.get("/api/governance/approvals", params={"decision_state": "proposed", "target_id": "m-state"})
    ids = [d["decision_id"] for d in r.json()]
    assert did in ids


# ---------------------------------------------------------------------------
# Full lifecycle: propose → review → decide (approved)
# ---------------------------------------------------------------------------

def test_full_lifecycle_approved():
    did = uid()
    # Propose
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-lc",
        "target_version": "v3",
        "risk_level":     "medium",
    })

    # Accept review — risk_owner is authorized for medium
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "risk_owner",
        "actor_id":   "risk-owner-1",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "under_review"

    # Decide — risk_owner is authorized for medium
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "risk_owner",
        "outcome":    "approved",
        "rationale":  "All checks passed",
        "actor_id":   "risk-owner-1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]       == "approved"
    assert body["decision_state"] == "decided"
    assert body["decided_at"]     is not None


def test_approved_with_conditions():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "strategy_spec",
        "target_id":      "s-cond",
        "target_version": "v1",
        "risk_level":     "low",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved_with_conditions",
        "rationale":  "Conditional approval",
        "actor_id":   "rev-1",
        "conditions": ["Must pass canary gate within 5 trading days"],
    })
    assert r.status_code == 200
    assert r.json()["conditions"] == ["Must pass canary gate within 5 trading days"]


def test_rejected_decision():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-rej",
        "target_version": "v1",
        "risk_level":     "low",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "rejected",
        "rationale":  "Fails OOS validation",
        "actor_id":   "rev-1",
    })
    assert r.status_code == 200
    assert r.json()["decision"] == "rejected"


# ---------------------------------------------------------------------------
# Authorization enforcement
# ---------------------------------------------------------------------------

def test_unauthorized_decide_role_rejected():
    """A role not in the write-authority matrix for the risk level must get 400."""
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-decide-unauth",
        "target_version": "v1",
        "risk_level":     "high",  # requires risk_owner or governance_committee
    })
    # Accept review with an authorized role so we reach under_review
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "risk_owner",
        "actor_id":   "ro-1",
    })
    # governance_reviewer is NOT authorized to decide at high risk
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Should be rejected",
        "actor_id":   "rev-2",
    })
    assert r.status_code == 400


def test_unauthorized_role_for_high_risk_rejected():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-auth",
        "target_version": "v1",
        "risk_level":     "high",  # requires risk_owner or governance_committee
    })
    # governance_reviewer is not authorized at high risk level
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    assert r.status_code == 400


def test_unauthorized_role_for_critical_rejected():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-crit",
        "target_version": "v1",
        "risk_level":     "critical",  # requires governance_committee only
    })
    # risk_owner is not authorized at critical
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "risk_owner",
        "actor_id":   "ro-1",
    })
    assert r.status_code == 400


def test_governance_committee_authorized_for_critical():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-crit2",
        "target_version": "v1",
        "risk_level":     "critical",
    })
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id":   "gc-1",
    })
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------

def _make_decided(target_id: str = "m-rev") -> str:
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      target_id,
        "target_version": "v1",
        "risk_level":     "low",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "OK",
        "actor_id":   "rev-1",
    })
    return did


def test_revoke_by_risk_owner():
    did = _make_decided("m-rev-ro")
    r = client.post(f"/api/governance/approvals/{did}/revoke", json={
        "actor_role": "risk_owner",
        "actor_id":   "risk-1",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "revoked"


def test_revoke_by_unauthorized_role_rejected():
    did = _make_decided("m-rev-unauth")
    r = client.post(f"/api/governance/approvals/{did}/revoke", json={
        "actor_role": "governance_reviewer",  # not in REVOKE_AUTHORITY
        "actor_id":   "rev-1",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Latest-approved lookup
# ---------------------------------------------------------------------------

def test_latest_approved_returns_correct_decision():
    target_id = f"art-la-{uuid.uuid4().hex[:6]}"
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      target_id,
        "target_version": "v1",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Pass",
        "actor_id":   "rev-1",
    })
    r = client.get("/api/governance/approvals/latest-approved", params={
        "target_type": "registry_entry",
        "target_id":   target_id,
    })
    assert r.status_code == 200
    assert r.json()["decision_id"] == did


def test_latest_approved_returns_null_when_none():
    r = client.get("/api/governance/approvals/latest-approved", params={
        "target_type": "registry_entry",
        "target_id":   "does-not-exist-xyz",
    })
    assert r.status_code == 200
    assert r.json() is None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_records_events():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-aud",
        "target_version": "v1",
    })
    r = client.get("/api/governance/audit", params={"decision_id": did})
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 1
    assert any(e["event_type"] == "approval_decision_created" for e in events)


def test_audit_log_grows_through_lifecycle():
    did = uid()
    target_id = f"art-aud-lc-{uuid.uuid4().hex[:4]}"
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      target_id,
        "target_version": "v1",
        "risk_level":     "low",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "OK",
        "actor_id":   "rev-1",
    })
    r = client.get("/api/governance/audit", params={"decision_id": did})
    events = r.json()
    event_types = {e["event_type"] for e in events}
    assert "approval_decision_created"      in event_types
    assert "approval_decision_state_changed" in event_types
    assert "approval_decision_decided"       in event_types


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

def test_decide_from_proposed_raises_400():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-tr",
        "target_version": "v1",
    })
    # Skip the review step — decide directly from proposed (state check must reject)
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Should fail",
        "actor_id":   "rev-1",
    })
    assert r.status_code == 400


def test_review_from_under_review_raises_400():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-tr2",
        "target_version": "v1",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    # Second review call should fail
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    })
    assert r.status_code == 400


def test_decide_records_actual_approver_identity_and_audit():
    did = uid()
    client.post("/api/governance/approvals", json={
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-medium-1",
        "target_version": "v1",
        "risk_level":     "medium",
    })
    client.post(f"/api/governance/approvals/{did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "reviewer-1",
    })

    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "actor_role": "risk_owner",
        "actor_id":   "risk-owner-2",
        "outcome":    "approved",
        "rationale":  "Escalated approval recorded by risk owner",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["actor_role"] == "risk_owner"
    assert body["actor_id"] == "risk-owner-2"

    r = client.get("/api/governance/audit", params={"decision_id": did})
    assert r.status_code == 200
    decided_events = [e for e in r.json() if e["event_type"] == "approval_decision_decided"]
    assert len(decided_events) == 1
    assert decided_events[0]["actor_role"] == "risk_owner"
    assert decided_events[0]["actor_id"] == "risk-owner-2"
