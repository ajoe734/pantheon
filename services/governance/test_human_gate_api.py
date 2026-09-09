from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from services.governance import main
from services.governance.human_gate.decision_model import HumanGateSignature
from services.governance.human_gate_store import GovernanceHumanGateDecisionStore
from services.governance.promotion_readiness.signoff_api import SignoffAPI, SignoffApiError
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


def test_human_gate_multi_instance_isolation(tmp_path, monkeypatch) -> None:
    """Closes F09: verify human gate decisions across independent store instances coordinate without lost updates."""
    store_file = tmp_path / "human-gates.json"
    records_a = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    records_b = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))

    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_SECRET", "human-gate-test-secret")
    monkeypatch.delenv("PANTHEON_GOVERNANCE_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL", raising=False)

    # 1. Instance A creates gate 1
    monkeypatch.setattr(main, "human_gate_record_store", records_a)
    monkeypatch.setattr(main, "human_gate_api", SignoffAPI(store=GovernanceHumanGateDecisionStore(records_a)))
    d1 = f"hgd-iso-1-{uuid.uuid4().hex[:6]}"
    main.create_human_gate(
        body=_decision_payload(d1),
        authorization=_headers("approver-1", "approver")["Authorization"],
        x_mfa_token=None,
    )

    # 2. Instance B creates gate 2
    monkeypatch.setattr(main, "human_gate_record_store", records_b)
    monkeypatch.setattr(main, "human_gate_api", SignoffAPI(store=GovernanceHumanGateDecisionStore(records_b)))
    d2 = f"hgd-iso-2-{uuid.uuid4().hex[:6]}"
    main.create_human_gate(
        body=_decision_payload(d2),
        authorization=_headers("approver-2", "approver")["Authorization"],
        x_mfa_token=None,
    )

    # Both gates survive across both instances
    assert records_a.get(d1) is not None
    assert records_a.get(d2) is not None
    assert records_b.get(d1) is not None
    assert records_b.get(d2) is not None
    assert len(records_a.list_all()) == 2
    assert len(records_b.list_all()) == 2

    # Instance B reads and signs gate 1
    signed_g1 = main.sign_human_gate(
        decision_id=d1,
        body={"role": "approver"},
        authorization=_headers("approver-1", "approver", mfa=True)["Authorization"],
        x_mfa_token=None,
    )
    assert len(signed_g1["signatures"]) == 1

    # Instance A immediately observes the signature on gate 1
    g1_from_a = records_a.get(d1)
    assert len(g1_from_a["signatures"]) == 1


def test_mounted_human_gate_interleaved_conflict_isolation(tmp_path, monkeypatch) -> None:
    """Mounted human gate handlers coordinate duplicate conflict, concurrent CAS signatures, duplicate actor rejection, and revocation across independent store instances."""
    store_file = tmp_path / "human-gates-intl.json"
    records_a = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    records_b = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    api_a = SignoffAPI(store=GovernanceHumanGateDecisionStore(records_a))
    api_b = SignoffAPI(store=GovernanceHumanGateDecisionStore(records_b))

    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_SECRET", "human-gate-test-secret")
    monkeypatch.delenv("PANTHEON_GOVERNANCE_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL", raising=False)

    decision_id = f"hgd-intl-{uuid.uuid4().hex[:6]}"
    payload = _decision_payload(decision_id)

    # 1. Instance A creates the decision
    monkeypatch.setattr(main, "human_gate_record_store", records_a)
    monkeypatch.setattr(main, "human_gate_api", api_a)
    created = main.create_human_gate(
        body=payload,
        authorization=_headers("approver-1", "approver")["Authorization"],
        x_mfa_token=None,
    )
    assert created["decision_id"] == decision_id

    # 2. Instance B attempts duplicate creation with same decision_id -> 409 conflict
    monkeypatch.setattr(main, "human_gate_record_store", records_b)
    monkeypatch.setattr(main, "human_gate_api", api_b)
    with pytest.raises(HTTPException) as dup_exc:
        main.create_human_gate(
            body=payload,
            authorization=_headers("approver-2", "approver")["Authorization"],
            x_mfa_token=None,
        )
    assert dup_exc.value.status_code == 409
    assert f"decision already exists: {decision_id}" in str(dup_exc.value.detail)

    # 3. Controlled interleaving of signatures across independent instances
    # Instance B reads the initial decision snapshot
    snapshot_b = api_b.read_decision(decision_id)
    assert len(snapshot_b.signatures) == 0

    # Instance A signs as approver and commits to disk
    monkeypatch.setattr(main, "human_gate_record_store", records_a)
    monkeypatch.setattr(main, "human_gate_api", api_a)
    s_a = main.sign_human_gate(
        decision_id=decision_id,
        body={"role": "approver"},
        authorization=_headers("approver-1", "approver", mfa=True)["Authorization"],
        x_mfa_token=None,
    )
    assert len(s_a["signatures"]) == 1

    # Instance B uses mounted signing handler sharing the expected snapshot to prove 409 conflict
    monkeypatch.setattr(main, "human_gate_record_store", records_b)
    monkeypatch.setattr(main, "human_gate_api", api_b)
    orig_require_b = api_b.store.require
    api_b.store.require = lambda dec_id: snapshot_b
    with pytest.raises(HTTPException) as cas_exc:
        main.sign_human_gate(
            decision_id=decision_id,
            body={"role": "operator"},
            authorization=_headers("operator-1", "operator", mfa=True)["Authorization"],
            x_mfa_token=None,
        )
    assert cas_exc.value.status_code == 409
    assert "human gate changed concurrently" in str(cas_exc.value.detail)

    # Explicit retry from fresh state without sleep synchronization
    api_b.store.require = orig_require_b
    s_b = main.sign_human_gate(
        decision_id=decision_id,
        body={"role": "operator"},
        authorization=_headers("operator-1", "operator", mfa=True)["Authorization"],
        x_mfa_token=None,
    )
    assert len(s_b["signatures"]) == 2

    # 4. Duplicate actor signature rejection: approver-1 cannot sign a second role
    monkeypatch.setattr(main, "human_gate_record_store", records_a)
    monkeypatch.setattr(main, "human_gate_api", api_a)
    with pytest.raises(HTTPException) as dup_actor_exc:
        main.sign_human_gate(
            decision_id=decision_id,
            body={"role": "risk_owner"},
            authorization=_headers("approver-1", "risk_owner", mfa=True)["Authorization"],
            x_mfa_token=None,
        )
    assert dup_actor_exc.value.status_code == 409
    assert "one authenticated actor may sign only one role" in str(dup_actor_exc.value.detail)

    # 5. Revocation transition: Instance A revokes the gate
    revoked = main.revoke_human_gate(
        decision_id=decision_id,
        body={"reason": "audit clearance revoked"},
        authorization=_headers("admin-1", "admin", mfa=True)["Authorization"],
        x_mfa_token=None,
    )
    assert revoked["status"] == "revoked"

    # 6. Durable reread by fresh instance C confirms terminal status and all signatures retained
    records_c = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    final = records_c.get(decision_id)
    assert final is not None
    assert final["status"] == "revoked"
    assert len(final["signatures"]) == 2
    roles = {s["role"] for s in final["signatures"]}
    assert roles == {"approver", "operator"}
