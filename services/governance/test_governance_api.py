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

from services.governance.test_approval_authority_postgres import owner_env, headers as jwt_headers
from services.governance.pg_store import PostgresApprovalDecisionStore, PostgresGovernanceAuditStore

client = TestClient(app)
_test_env = {}


def _signed_headers(*, actor='synthetic-reviewer', role='approval_reader', tenant='synthetic-tenant'):
    return jwt_headers(_test_env, uuid.uuid4().hex, sub=actor, roles=[role], tenant_id=tenant)


@pytest.fixture(autouse=True)
def isolated_approval_owner(owner_env, monkeypatch):
    global _test_env
    _test_env = owner_env
    for key, value in owner_env.items():
        if key.startswith('PANTHEON_GOVERNANCE_JWT_'):
            monkeypatch.setenv(key, value)
    schema = 'gov_api_' + uuid.uuid4().hex
    monkeypatch.setattr(main, 'store', PostgresApprovalDecisionStore(owner_env['GOVERNANCE_STORE_DSN'], schema+'.decisions'))
    monkeypatch.setattr(main, 'audit_store', PostgresGovernanceAuditStore(owner_env['GOVERNANCE_STORE_DSN'], schema+'.audit'))
    client.headers.update(_signed_headers())
    yield
    client.headers.clear()



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
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-001",
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    assert r.status_code == 201
    body = r.json()
    assert body["decision_id"]    == did
    assert body["decision_state"] == "proposed"
    assert body["decision"]       is None


def test_propose_accepts_strategy_workshop_target():
    did = uid()
    r = client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "decision_id": did,
        "target_type": "strategy_workshop",
        "target_id": "workshop-public-api",
        "target_version": "workshop-version-public-api",
        "risk_level": "low",
        "tenant_id": "tenant-public-api",
        "owner_user_id": "user-public-api",
    }, headers=_signed_headers(actor="user-public-api", role='approval_proposer', tenant="tenant-public-api"))

    assert r.status_code == 201, r.text
    assert r.json()["target_type"] == "strategy_workshop"


def test_propose_rejects_noncanonical_workshop_alias():
    r = client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id": uid(),
        "target_type": "workshop",
        "target_id": "workshop-alias",
        "target_version": "workshop-version-alias",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))

    assert r.status_code == 422


def test_propose_autogenerates_decision_id():
    r = client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "target_type":    "strategy_spec",
        "target_id":      "art-auto",
        "target_version": "v1",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
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
    assert client.post("/api/governance/approvals", json={"expected_version": 0, "expires_at": "2099-01-01T00:00:00Z", "tenant_id": "synthetic-tenant", "owner_user_id": "synthetic-reviewer", **body}, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant')).status_code == 201
    assert client.post("/api/governance/approvals", json={"expected_version": 0, "expires_at": "2099-01-01T00:00:00Z", "tenant_id": "synthetic-tenant", "owner_user_id": "synthetic-reviewer", **body}, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant')).status_code == 409


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------

def test_get_returns_decision():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-1",
        "target_version": "v2",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
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
            "expected_version": 0,
            "expires_at": "2099-01-01T00:00:00Z",
            "tenant_id": "synthetic-tenant",
            "owner_user_id": "synthetic-reviewer",
            "target_type":    "strategy_spec",
            "target_id":      target_id,
            "target_version": f"v{i}",
        }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    r = client.get("/api/governance/approvals", params={"target_id": target_id})
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_list_filter_by_state():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-state",
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
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
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      "m-lc",
        "target_version": "v3",
        "risk_level":     "medium",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))

    # Accept review — risk_owner is authorized for medium
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "risk_owner",
        "actor_id":   "risk-owner-1",
    }, headers=_signed_headers(actor="risk-owner-1", role="risk_owner", tenant='synthetic-tenant'))
    assert r.status_code == 200
    assert r.json()["decision_state"] == "under_review"

    # Decide — risk_owner is authorized for medium
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "risk_owner",
        "outcome":    "approved",
        "rationale":  "All checks passed",
        "actor_id":   "risk-owner-1",
    }, headers=_signed_headers(actor="risk-owner-1", role="risk_owner", tenant='synthetic-tenant'))
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]       == "approved"
    assert body["decision_state"] == "decided"
    assert body["decided_at"]     is not None


def test_approved_with_conditions():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "strategy_spec",
        "target_id":      "s-cond",
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "approved_with_conditions",
        "rationale":  "Conditional approval",
        "actor_id":   "rev-1",
        "conditions": ["Must pass canary gate within 5 trading days"],
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 200
    assert r.json()["conditions"] == ["Must pass canary gate within 5 trading days"]


def test_rejected_decision():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-rej",
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "rejected",
        "rationale":  "Fails OOS validation",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 200
    assert r.json()["decision"] == "rejected"


# ---------------------------------------------------------------------------
# Authorization enforcement
# ---------------------------------------------------------------------------

def test_unauthorized_decide_role_rejected():
    """A role not in the write-authority matrix for the risk level must get 400."""
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-decide-unauth",
        "target_version": "v1",
        "risk_level":     "high",  # requires risk_owner or governance_committee
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    # Accept review with an authorized role so we reach under_review
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "risk_owner",
        "actor_id":   "ro-1",
    }, headers=_signed_headers(actor="ro-1", role="risk_owner", tenant='synthetic-tenant'))
    # governance_reviewer is NOT authorized to decide at high risk
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Should be rejected",
        "actor_id":   "rev-2",
    }, headers=_signed_headers(actor="rev-2", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 400


def test_unauthorized_role_for_high_risk_rejected():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-auth",
        "target_version": "v1",
        "risk_level":     "high",  # requires risk_owner or governance_committee
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    # governance_reviewer is not authorized at high risk level
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 400


def test_unauthorized_role_for_critical_rejected():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-crit",
        "target_version": "v1",
        "risk_level":     "critical",  # requires governance_committee only
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    # risk_owner is not authorized at critical
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "risk_owner",
        "actor_id":   "ro-1",
    }, headers=_signed_headers(actor="ro-1", role="risk_owner", tenant='synthetic-tenant'))
    assert r.status_code == 400


def test_governance_committee_authorized_for_critical():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-crit2",
        "target_version": "v1",
        "risk_level":     "critical",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_committee",
        "actor_id":   "gc-1",
    }, headers=_signed_headers(actor="gc-1", role="governance_committee", tenant='synthetic-tenant'))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------

def _make_decided(target_id: str = "m-rev") -> str:
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      target_id,
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "OK",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    return did


def test_revoke_by_risk_owner():
    did = _make_decided("m-rev-ro")
    r = client.post(f"/api/governance/approvals/{did}/revoke", json={
        "expected_version": 3,
        "actor_role": "risk_owner",
        "actor_id":   "risk-1",
    }, headers=_signed_headers(actor="risk-1", role="risk_owner", tenant='synthetic-tenant'))
    assert r.status_code == 200
    assert r.json()["decision_state"] == "revoked"


def test_revoke_by_unauthorized_role_rejected():
    did = _make_decided("m-rev-unauth")
    r = client.post(f"/api/governance/approvals/{did}/revoke", json={
        "expected_version": 3,
        "actor_role": "governance_reviewer",  # not in REVOKE_AUTHORITY
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Latest-approved lookup
# ---------------------------------------------------------------------------

def test_latest_approved_returns_correct_decision():
    target_id = f"art-la-{uuid.uuid4().hex[:6]}"
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      target_id,
        "target_version": "v1",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Pass",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
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
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-aud",
        "target_version": "v1",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    r = client.get("/api/governance/audit", params={"decision_id": did})
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 1
    assert any(e["event_type"] == "approval_decision_created" for e in events)


def test_audit_log_grows_through_lifecycle():
    did = uid()
    target_id = f"art-aud-lc-{uuid.uuid4().hex[:4]}"
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "model_artifact",
        "target_id":      target_id,
        "target_version": "v1",
        "risk_level":     "low",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "OK",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
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
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-tr",
        "target_version": "v1",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    # Skip the review step — decide directly from proposed (state check must reject)
    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Should fail",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 400


def test_review_from_under_review_raises_400():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "registry_entry",
        "target_id":      "art-tr2",
        "target_version": "v1",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    # Second review call should fail
    r = client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "actor_id":   "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r.status_code == 400


def test_decide_records_actual_approver_identity_and_audit():
    did = uid()
    client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "expires_at": "2099-01-01T00:00:00Z",
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id":    did,
        "target_type":    "evolution_proposal",
        "target_id":      "evo-medium-1",
        "target_version": "v1",
        "risk_level":     "medium",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id":   "reviewer-1",
    }, headers=_signed_headers(actor="reviewer-1", role="governance_reviewer", tenant='synthetic-tenant'))

    r = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "risk_owner",
        "actor_id":   "risk-owner-2",
        "outcome":    "approved",
        "rationale":  "Escalated approval recorded by risk owner",
    }, headers=_signed_headers(actor="risk-owner-2", role="risk_owner", tenant='synthetic-tenant'))
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


def test_authoritative_approval_readback_and_anti_forgery():
    did = uid()
    r_prop = client.post("/api/governance/approvals", json={
        "expected_version": 0,
        "tenant_id": "synthetic-tenant",
        "owner_user_id": "synthetic-reviewer",
        "decision_id": did,
        "target_type": "registry_entry",
        "target_id": "target-123",
        "target_version": "v1",
        "risk_level": "low",
        "session_id": "sess-alpha",
        "candidate_digest": "cd" * 32,
        "proof_digest": "pd" * 32,
        "expires_at": "2026-12-31T23:59:59Z",
    }, headers=_signed_headers(actor='synthetic-reviewer', role='approval_proposer', tenant='synthetic-tenant'))
    assert r_prop.status_code == 201
    prop_body = r_prop.json()
    assert prop_body["session_id"] == "sess-alpha"
    assert prop_body["candidate_digest"] == "cd" * 32
    assert prop_body["proof_digest"] == "pd" * 32
    assert prop_body["expires_at"] == "2026-12-31T23:59:59Z"
    # Authority flags must NOT be set on proposed
    assert prop_body.get("authority_status") is None
    assert prop_body.get("controller_record_ref") is None

    client.post(f"/api/governance/approvals/{did}/review", json={
        "expected_version": 1,
        "actor_role": "governance_reviewer",
        "actor_id": "rev-1",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))

    # Decide with approval
    r_dec = client.post(f"/api/governance/approvals/{did}/decide", json={
        "expected_version": 2,
        "actor_role": "governance_reviewer",
        "actor_id": "rev-1",
        "outcome": "approved",
        "rationale": "Verified proofs and candidate",
    }, headers=_signed_headers(actor="rev-1", role="governance_reviewer", tenant='synthetic-tenant'))
    assert r_dec.status_code == 200
    dec_body = r_dec.json()
    assert dec_body["authority_status"] == "authoritative"
    assert dec_body["controller_record_ref"] == f"governance-controller://approval-{did}"
    assert dec_body["recorded_at"] is not None
    assert dec_body["recorded_at"] == dec_body["decided_at"]
    assert dec_body["session_id"] == "sess-alpha"
    assert dec_body["candidate_digest"] == "cd" * 32
    assert dec_body["proof_digest"] == "pd" * 32

    # Verify lookup by id maintains authoritative readback
    r_get = client.get(f"/api/governance/approvals/{did}")
    assert r_get.status_code == 200
    get_body = r_get.json()
    assert get_body["authority_status"] == "authoritative"
    assert get_body["controller_record_ref"] == f"governance-controller://approval-{did}"
    assert get_body["recorded_at"] == dec_body["recorded_at"]

