from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from services.governance.test_approval_authority import approval_snapshot, SnapshotApprovalReader

from services.governance.human_gate.decision_model import stable_hash
from services.governance.promotion_readiness.signoff_api import SignoffAPI
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    strategy_artifact_checksum,
)


SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import promotion_authority  # noqa: E402


def _facts(*, requesting_actor_id: str = "operator-b"):
    artifact = load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )["strategy_artifact"]
    request = {
        "current_binding_id": "rb-paper-001",
        "human_gate_decision_id": "hgd-canary-001",
        "environment": "dev",
        "plan_id": "plan-canary-001",
        "plan_status": "approved",
        "target_stage": "canary",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": "approval-artifact-001",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        # Caller assertions are intentionally false; canonical scale wins.
        "capital_scale_pct": 99,
        "gross_scale_pct": 99,
    }
    plan = {
        "plan_id": request["plan_id"],
        "status": "approved",
        "current_stage": "paper",
        "target_stage": "canary",
        "transition_type": "promote",
        "runtime_action": "replace_binding",
        "binding_id": request["current_binding_id"],
        "artifact_id": request["artifact_id"],
        "artifact_version": request["artifact_version"],
        "strategy_id": request["strategy_id"],
        "approval_decision_id": request["approval_decision_id"],
        "capital_pool_id": request["capital_pool_id"],
        "sponsor_persona_id": request["sponsor_persona_id"],
        "scale": {"capital_scale_pct": 5.0, "gross_scale_pct": 25.0},
        "rollback": {
            "target_artifact_id": request["artifact_id"],
            "target_version": request["artifact_version"],
            "action_type": "replace",
        },
    }
    registry = {
        "entry": {
            "registry_id": request["artifact_id"],
            "artifact_type": "execution_bundle",
            "strategy_id": request["strategy_id"],
            "version": request["artifact_version"],
            "artifact_state": "approved",
            "checksum": strategy_artifact_checksum(artifact),
            "approval_decision_id": request["approval_decision_id"],
            "metadata": {"strategy_artifact": artifact},
        },
        "deployment_stage": "paper",
    }
    approval = {
        "decision_id": request["approval_decision_id"],
        "decision_state": "decided",
        "decision": "approved",
        "target_type": "registry_entry",
        "target_id": request["artifact_id"],
        "target_version": request["artifact_version"],
        "capital_pool_id": request["capital_pool_id"],
        "persona_id": request["sponsor_persona_id"],
        "actor_id": "artifact-approver",
        "conditions": [],
        "revoked_at": None,
    }
    plan.setdefault("metadata", {})["tenant_id"] = "tenant-unit"
    approval = approval_snapshot(candidate_digest=registry["entry"]["checksum"], **approval)
    pool = {
        "pool_id": request["capital_pool_id"],
        "status": "active",
        "single_runtime_enforced": True,
    }
    binding = {
        "binding_id": request["persona_capital_binding_id"],
        "persona_id": request["sponsor_persona_id"],
        "capital_pool_id": request["capital_pool_id"],
        "status": "active",
        "allowed_deployment_scope": "canary",
    }
    admissibility = {
        "persona_id": request["sponsor_persona_id"],
        "capital_pool_id": request["capital_pool_id"],
        "target_stage": "canary",
        "permitted": True,
        "pool_status": "active",
        "single_runtime_enforced": True,
        "binding_id": request["persona_capital_binding_id"],
        "binding_status": "active",
        "allowed_deployment_scope": "canary",
    }

    evidence_keys = sorted(
        promotion_authority._REQUIRED_EVIDENCE["canary"]
    )
    api = SignoffAPI()
    decision = api.create_decision(
        {
            "decision_id": request["human_gate_decision_id"],
            "target_type": "runtime_binding_promotion",
            "target_id": request["plan_id"],
            "target_environment": "dev",
            "required_roles": ["approver", "risk_owner", "operator"],
            "evidence_reviewed": [
                {
                    "key": key,
                    "evidence_hash": stable_hash({"key": key}),
                    "source_ref": f"evidence://{key}",
                    "status": "passed",
                }
                for key in evidence_keys
            ],
            "can_proceed_input": {
                "readiness_packet_ref": "packet://canary-001",
                "readiness_packet_can_proceed": True,
                "required_evidence": evidence_keys,
                "missing_evidence": [],
                "blocking_reasons": [],
                "unsafe_true_flags": [],
                "gate_results_blocking": [],
            },
            "target_stage": "canary",
            "source_binding_id": request["current_binding_id"],
        }
    )
    decision = api.append_signature(
        decision.decision_id,
        {
            "role": "approver",
            "actor_id": "artifact-approver",
            "source_ref": "signature://promotion-reviewer",
            "authn_token_kind": "jwt",
            "mfa_proof": "jwt_claim",
        },
    )
    decision = api.append_signature(
        decision.decision_id,
        {
            "role": "risk_owner",
            "actor_id": "risk-owner",
            "source_ref": "signature://risk-owner",
            "authn_token_kind": "jwt",
            "mfa_proof": "jwt_claim",
        },
    )
    decision = api.append_signature(
        decision.decision_id,
        {
            "role": "operator",
            "actor_id": "operator-a",
            "source_ref": "signature://operator-a",
            "authn_token_kind": "jwt",
            "mfa_proof": "jwt_claim",
        },
    )
    human_gate = decision.to_dict()

    def fetch(url: str, _timeout: float):
        if "/human-gates/" in url:
            return human_gate
        if "/api/deployment/" in url:
            return plan
        if "/api/registry/" in url:
            return registry
        if "/api/governance/approvals/" in url:
            return approval
        if "/api/capital-pools/" in url:
            return pool
        if "/api/bindings/admissibility" in url:
            return admissibility
        if "/api/bindings/" in url:
            return binding
        raise AssertionError(url)

    return request, fetch, requesting_actor_id


def _verify(request, fetch, actor_id):
    return promotion_authority.verify_promotion_authorities(
        request,
        requesting_actor_id=actor_id,
        deployment_base_url="http://deployment:8095",
        registry_base_url="http://registry:8087",
        governance_base_url="http://governance:8082",
        capital_base_url="http://capital:8092",
        fetch_json=fetch,
        approval_reader=SnapshotApprovalReader(lambda: fetch("http://governance/api/governance/approvals/" + request["approval_decision_id"], 5)),
        now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
    )


def test_canary_authority_derives_scale_refs_and_four_distinct_actors():
    request, fetch, actor_id = _facts()
    verified = _verify(request, fetch, actor_id)

    canonical = verified["request"]
    report = verified["authority_report"]
    assert canonical["capital_scale_pct"] == 5.0
    assert canonical["gross_scale_pct"] == 25.0
    assert canonical["promotion_gate_decision_id"] == "hgd-canary-001"
    assert canonical["risk_owner_approval_ref"] == "signature://risk-owner"
    assert canonical["operator_approval_ref"] == "signature://operator-a"
    assert report["distinct_actor_count"] == 4
    assert report["promotion_reviewer_actor_id"] == "artifact-approver"
    assert report["requesting_actor_id"] == "operator-b"
    assert report["deploy_authority"]["registry_deployment_stage"] == "paper"


@pytest.mark.parametrize(
    "requesting_actor_id", ["artifact-approver", "risk-owner", "operator-a"]
)
def test_cutover_operator_must_be_distinct_from_all_prior_approvers(
    requesting_actor_id,
):
    request, fetch, _ = _facts(requesting_actor_id=requesting_actor_id)
    with pytest.raises(
        promotion_authority.PromotionAuthorityError,
        match="distinct",
    ):
        _verify(request, fetch, requesting_actor_id)


def test_plan_cannot_skip_paper_to_live():
    request, fetch, actor_id = _facts()
    request["target_stage"] = "live"
    with pytest.raises(promotion_authority.PromotionAuthorityError):
        _verify(request, fetch, actor_id)
