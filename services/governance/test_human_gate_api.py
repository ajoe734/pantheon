from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from services.governance import main
from services.governance.human_gate_store import GovernanceHumanGateDecisionStore
from services.governance.promotion_readiness.signoff_api import SignoffAPI
from services.governance.record_store import JsonGovernanceRecordStore
from services.runtime_auth_inbound import encode_jwt_hs256


def _headers(actor_id: str, role: str, *, mfa: bool = False) -> dict[str, str]:
    claims = {"sub": actor_id, "roles": [role]}
    if mfa:
        claims["amr"] = ["pwd", "mfa"]
    token = encode_jwt_hs256(claims, secret="human-gate-test-secret")
    return {"Authorization": f"Bearer {token}"}


def _decision_payload(decision_id: str) -> dict:
    evidence_keys = sorted(main._PROMOTION_HUMAN_GATE_EVIDENCE["canary"])
    return {
        "decision_id": decision_id,
        "target_type": "runtime_binding_promotion",
        "target_id": "plan-canary-001",
        "target_environment": "dev",
        "required_roles": ["approver", "operator", "risk_owner"],
        "evidence_reviewed": [
            {
                "key": key,
                "evidence_hash": "sha256:" + f"{index:064x}",
                "source_ref": f"evidence://{key}",
                "status": "passed",
            }
            for index, key in enumerate(evidence_keys, start=1)
        ],
        "can_proceed_input": {
            "readiness_packet_ref": "packet://paper-to-canary-001",
            "readiness_packet_can_proceed": True,
            "required_evidence": evidence_keys,
            "missing_evidence": [],
            "blocking_reasons": [],
            "unsafe_true_flags": [],
            "gate_results_blocking": [],
        },
        "metadata": {
            "target_stage": "canary",
            "source_binding_id": "rb-paper-001",
        },
    }


def test_human_gate_signatures_are_jwt_mfa_bound_and_actor_distinct(
    tmp_path, monkeypatch
):
    records = JsonGovernanceRecordStore(
        tmp_path / "human-gates.json", id_fields=("decision_id",)
    )
    monkeypatch.setattr(main, "human_gate_record_store", records)
    monkeypatch.setattr(
        main,
        "human_gate_api",
        SignoffAPI(store=GovernanceHumanGateDecisionStore(records)),
    )
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTH_MODE", "strict")
    monkeypatch.setenv(
        "PANTHEON_GOVERNANCE_JWT_SECRET", "human-gate-test-secret"
    )
    monkeypatch.delenv("PANTHEON_GOVERNANCE_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL", raising=False)
    decision_id = f"hgd-test-{uuid.uuid4().hex[:8]}"
    created = main.create_human_gate(
        body=_decision_payload(decision_id),
        authorization=_headers("reviewer-actor", "approver")["Authorization"],
        x_mfa_token=None,
    )
    assert created["status"] == "pending"
    assert created["signatures"] == []

    reviewer_signed = main.sign_human_gate(
        decision_id=decision_id,
        body={"role": "approver"},
        authorization=_headers("reviewer-actor", "approver", mfa=True)[
            "Authorization"
        ],
        x_mfa_token=None,
    )
    assert reviewer_signed["status"] == "pending"

    with pytest.raises(HTTPException) as header_only:
        main.sign_human_gate(
            decision_id=decision_id,
            body={"role": "risk_owner", "actor_id": "spoofed"},
            authorization=_headers("risk-actor", "risk_owner")["Authorization"],
            x_mfa_token="123456",
        )
    assert header_only.value.status_code == 401

    risk_signed = main.sign_human_gate(
        decision_id=decision_id,
        body={"role": "risk_owner", "actor_id": "spoofed"},
        authorization=_headers("risk-actor", "risk_owner", mfa=True)[
            "Authorization"
        ],
        x_mfa_token=None,
    )
    assert risk_signed["signatures"][1]["actor_id"] == "risk-actor"
    assert risk_signed["status"] == "pending"

    operator_signed = main.sign_human_gate(
        decision_id=decision_id,
        body={"role": "operator"},
        authorization=_headers("operator-a", "operator", mfa=True)[
            "Authorization"
        ],
        x_mfa_token=None,
    )
    assert operator_signed["status"] == "approved"
    assert operator_signed["can_proceed"] is True

    readback = main.get_human_gate(decision_id)
    assert {
        signature["actor_id"] for signature in readback["signatures"]
    } == {"reviewer-actor", "risk-actor", "operator-a"}

    revoked = main.revoke_human_gate(
        decision_id=decision_id,
        body={"reason": "fresh incident invalidated the evidence"},
        authorization=_headers("risk-actor", "risk_owner", mfa=True)[
            "Authorization"
        ],
        x_mfa_token=None,
    )
    assert revoked["status"] == "revoked"
    assert revoked["can_proceed"] is False
    assert revoked["revoked_by_actor_id"] == "risk-actor"


def test_human_gate_rejects_wrong_evidence_contract(tmp_path, monkeypatch):
    records = JsonGovernanceRecordStore(
        tmp_path / "human-gates.json", id_fields=("decision_id",)
    )
    monkeypatch.setattr(
        main,
        "human_gate_api",
        SignoffAPI(store=GovernanceHumanGateDecisionStore(records)),
    )
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTH_MODE", "strict")
    monkeypatch.setenv(
        "PANTHEON_GOVERNANCE_JWT_SECRET", "human-gate-test-secret"
    )
    payload = _decision_payload(f"hgd-test-{uuid.uuid4().hex[:8]}")
    payload["can_proceed_input"]["required_evidence"].remove(
        "broker_sandbox_smoke"
    )

    with pytest.raises(HTTPException) as rejected:
        main.create_human_gate(
            body=payload,
            authorization=_headers("reviewer-actor", "approver")["Authorization"],
            x_mfa_token=None,
        )
    assert rejected.value.status_code == 422
    assert "required_evidence" in str(rejected.value.detail)

    payload = _decision_payload(f"hgd-test-{uuid.uuid4().hex[:8]}")
    payload["evidence_reviewed"].append(
        {
            "key": "caller_supplied_extra",
            "evidence_hash": "sha256:" + "f" * 64,
            "source_ref": "evidence://untrusted-extra",
            "status": "passed",
        }
    )
    with pytest.raises(HTTPException) as extra_rejected:
        main.create_human_gate(
            body=payload,
            authorization=_headers("reviewer-actor", "approver")["Authorization"],
            x_mfa_token=None,
        )
    assert extra_rejected.value.status_code == 422
    assert "evidence_reviewed" in str(extra_rejected.value.detail)
