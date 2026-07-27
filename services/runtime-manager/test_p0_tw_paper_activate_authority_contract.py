"""Contract-only proof for the TW paper activation authority chain.

This test validates request models and the final Runtime Manager authority
readbacks entirely in memory. It must never call a deployed service or create a
RegistryEntry, ApprovalDecision, DeploymentPlan, capital binding, or
RuntimeBinding in an authoritative store.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.capital.models import (
    ActivateBindingRequest,
    CreateBindingRequest,
    CreateCapitalPoolRequest,
)
from services.deployment.models import (
    CreateDeploymentPlanRequest,
    DispatchDeploymentPlanRequest,
)
from services.governance.models import (
    AcceptReviewRequest,
    DecideRequest,
    ProposeApprovalRequest,
)
from services.registry.models import ArtifactState
from services.registry.service import AdvanceRequest, StrategyArtifactRegisterRequest
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    build_strategy_artifact_registry_payload,
    load_strategy_artifact_registration,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DOMAIN = REPO_ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DOMAIN) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DOMAIN))

from deployment_plan import DeploymentScale, RollbackRef, StagePlanner  # noqa: E402


AUTHORITY_MODULE_PATH = Path(__file__).with_name("deploy_authority.py")
AUTHORITY_SPEC = importlib.util.spec_from_file_location(
    "p0_tw_paper_activate_deploy_authority",
    AUTHORITY_MODULE_PATH,
)
assert AUTHORITY_SPEC and AUTHORITY_SPEC.loader
deploy_authority = importlib.util.module_from_spec(AUTHORITY_SPEC)
AUTHORITY_SPEC.loader.exec_module(deploy_authority)


ARTIFACT_ID = "artifact-tw-paper-authority-contract"
STRATEGY_ID = "tw_paper_authority_contract"
VERSION = "1.0.0"
APPROVAL_ID = "approval-tw-paper-authority-contract"
PLAN_ID = "plan-tw-paper-authority-contract"
POOL_ID = "pool-tw-paper-authority-contract"
PERSONA_ID = "persona-tw-equity"
BINDING_ID = "binding-tw-paper-authority-contract"
TENANT_ID = "tenant-tw-paper-authority-contract"


def _contract_registration() -> dict[str, Any]:
    registration = copy.deepcopy(
        load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])
    )
    artifact = registration["strategy_artifact"]
    artifact.update(
        {
            "artifact_id": ARTIFACT_ID,
            "strategy_id": STRATEGY_ID,
            "version": VERSION,
        }
    )
    artifact["lineage"] = {
        "source_run_ids": ["contract-proof-run-not-production"],
        "source_dataset_refs": ["contract-proof-dataset-not-production"],
    }
    artifact["binding_intent"].update(
        {
            "persona_id": PERSONA_ID,
            "persona_capital_binding_id": BINDING_ID,
            "observed_runtime_binding_id": "contract-proof-runtime-binding",
            "observed_runtime_id": "runtime-tw-equity-paper",
            "observed_deployment_plan_id": PLAN_ID,
            "observed_capital_pool_id": POOL_ID,
            "observed_placeholder_artifact_id": "contract-proof-placeholder",
            "observed_placeholder_artifact_version": "0.0.0",
            "observed_binding_status": "pending",
            "observed_deployment_mode": "paper",
            "replacement_task_id": "P0-TW-PAPER-ACTIVATE-001",
            "evidence_ref": (
                "docs/deployment/evidence/p0-tw-paper-activate-001/"
                "track_c_gonogo_packet.md"
            ),
        }
    )
    registration.update(
        {
            "registry_id": ARTIFACT_ID,
            "artifact_state": "draft",
            "producer_run_id": "contract-proof-run-not-production",
            "evaluation_summary": {
                "contract_only": True,
                "production_qlib_training_performed": False,
            },
        }
    )
    return registration


def _registry_entry(
    registration: Mapping[str, Any],
) -> tuple[dict[str, Any], StrategyArtifactRegisterRequest]:
    request = StrategyArtifactRegisterRequest(**registration)
    registry_id, create = build_strategy_artifact_registry_payload(
        request.model_dump(mode="json")
    )
    assert registry_id == ARTIFACT_ID
    assert create.artifact_type.value == "execution_bundle"
    assert create.storage_ref is not None
    assert create.storage_ref.to_dict() == {
        "backend": "inline",
        "path": "$.entry.metadata.strategy_artifact",
    }
    return (
        {
            "registry_id": registry_id,
            "artifact_type": create.artifact_type.value,
            "strategy_id": create.strategy_id,
            "version": create.version,
            "artifact_state": "approved",
            "lineage": create.lineage.to_dict(),
            "storage_ref": create.storage_ref.to_dict(),
            "checksum": create.checksum,
            "producer_run_id": create.producer_run_id,
            "evaluation_summary": create.evaluation_summary,
            "approval_decision_id": APPROVAL_ID,
            "approver": "risk-owner-tw-paper-contract",
            "deployment_summary": {"current_stage": "none"},
            "metadata": create.metadata,
        },
        request,
    )


def _fetcher(
    *,
    plan: Mapping[str, Any],
    registry_view: Mapping[str, Any],
    approval: Mapping[str, Any],
    pool: Mapping[str, Any],
    admissibility: Mapping[str, Any],
    binding: Mapping[str, Any],
):
    def fetch(url: str, _timeout: float) -> Mapping[str, Any]:
        if "/api/deployment/plans/" in url:
            return plan
        if "/api/registry/strategy-artifacts/" in url:
            return registry_view
        if "/api/governance/approvals/" in url:
            return approval
        if "/api/capital-pools/" in url:
            return pool
        if "/api/bindings/admissibility" in url:
            return admissibility
        if "/api/bindings/" in url:
            return binding
        raise AssertionError(f"unexpected authority read: {url}")

    return fetch


def test_canonical_track_c_sequence_satisfies_runtime_deploy_authority() -> None:
    registration = _contract_registration()
    registry_entry, register_request = _registry_entry(registration)
    assert register_request.artifact_state == ArtifactState.DRAFT

    candidate_advance = AdvanceRequest(target_state="candidate")
    approved_advance = AdvanceRequest(
        target_state="approved",
        approver="risk-owner-tw-paper-contract",
        approval_decision_id=APPROVAL_ID,
    )
    assert candidate_advance.target_state == ArtifactState.CANDIDATE
    assert approved_advance.target_state == ArtifactState.APPROVED
    assert approved_advance.approval_decision_id == APPROVAL_ID

    proposal = ProposeApprovalRequest(
        decision_id=APPROVAL_ID,
        target_type="registry_entry",
        target_id=ARTIFACT_ID,
        target_version=VERSION,
        risk_level="medium",
        capital_pool_id=POOL_ID,
        persona_id=PERSONA_ID,
        tenant_id=TENANT_ID,
        owner_user_id="operator-tw-paper-contract",
    )
    review = AcceptReviewRequest(
        actor_role="governance_reviewer",
        actor_id="reviewer-tw-paper-contract",
    )
    decision = DecideRequest(
        actor_role="risk_owner",
        outcome="approved",
        rationale="Approve the isolated paper-stage contract only.",
        actor_id="risk-owner-tw-paper-contract",
        conditions=[],
        evidence_refs=[
            {
                "ref_type": "task_evidence",
                "ref_id": "P0-TW-PAPER-ACTIVATE-001",
            }
        ],
    )
    assert proposal.target_id == ARTIFACT_ID
    assert review.actor_id != proposal.owner_user_id
    assert decision.actor_id != proposal.owner_user_id

    pool_request = CreateCapitalPoolRequest(
        actor_id="capital-admin-tw-paper-contract",
        actor_role="capital.admin",
        pool_id=POOL_ID,
        name="TW paper authority contract pool",
        owner_id="desk-tw-paper-contract",
        owner_type="desk",
        currency="TWD",
        budget=1_000_000.0,
        risk_policy_ref="risk-policy-tw-paper-contract",
        single_runtime_enforced=True,
        metadata={"capital_mode": "paper", "live_capital_enabled": False},
    )
    binding_request = CreateBindingRequest(
        actor_id="persona-admin-tw-paper-contract",
        actor_role="persona.admin",
        binding_id=BINDING_ID,
        persona_id=PERSONA_ID,
        capital_pool_id=POOL_ID,
        role="paper_owner",
        allowed_deployment_scope="paper",
        budget=1_000_000.0,
        metadata={"market_scope": ["TW"], "live_capital_enabled": False},
    )
    activation_request = ActivateBindingRequest(
        actor_id="persona-admin-tw-paper-contract",
        actor_role="persona.admin",
        approval_decision_id=APPROVAL_ID,
    )
    assert pool_request.single_runtime_enforced is True
    assert binding_request.allowed_deployment_scope == "paper"
    assert activation_request.approval_decision_id == APPROVAL_ID

    approval = {
        "decision_id": APPROVAL_ID,
        "target_type": "registry_entry",
        "target_id": ARTIFACT_ID,
        "target_version": VERSION,
        "decision_state": "decided",
        "decision": "approved",
        "actor_role": decision.actor_role.value,
        "actor_id": decision.actor_id,
        "rationale": decision.rationale,
        "conditions": [],
        "risk_level": proposal.risk_level.value,
        "capital_pool_id": POOL_ID,
        "persona_id": PERSONA_ID,
        "tenant_id": TENANT_ID,
        "owner_user_id": proposal.owner_user_id,
        "expires_at": "2026-08-31T00:00:00Z",
        "revoked_at": None,
    }
    plan_request = CreateDeploymentPlanRequest(
        plan_id=PLAN_ID,
        approval_decision_id=APPROVAL_ID,
        capital_pool_id=POOL_ID,
        target_stage="paper",
        registry_entry=registry_entry,
        approval_decision=approval,
        current_stage="none",
        sponsor_persona_id=PERSONA_ID,
        scale={"capital_scale_pct": 0.0, "gross_scale_pct": 100.0},
        rollback={
            "target_artifact_id": "artifact-approved-zero-capital-baseline",
            "target_version": "1.0.0",
            "action_type": "replace",
            "reason": "Fail closed to an independently approved baseline.",
        },
        pre_checks=["registry", "governance", "capital", "source_readiness"],
        post_checks=["runtime_binding", "fleet_worker", "telemetry_identity"],
        metadata={"tenant_id": TENANT_ID, "live_write_enabled": False},
        status="approved",
    )
    plan = StagePlanner().create_plan(
        plan_id=PLAN_ID,
        approval_decision_id=APPROVAL_ID,
        registry_entry=registry_entry,
        capital_pool_id=POOL_ID,
        target_stage=plan_request.target_stage.value,
        approval_decision=approval,
        current_stage=plan_request.current_stage.value,
        created_by="deployment-operator-tw-paper-contract",
        sponsor_persona_id=PERSONA_ID,
        scale=DeploymentScale(**plan_request.scale.model_dump()),
        rollback=RollbackRef(**plan_request.rollback.model_dump(mode="json")),
        pre_checks=list(plan_request.pre_checks),
        post_checks=list(plan_request.post_checks),
        metadata=dict(plan_request.metadata or {}),
        status=plan_request.status.value,
    ).to_dict()
    dispatch = DispatchDeploymentPlanRequest(
        idempotency_key="P0-TW-PAPER-ACTIVATE-001:contract-dispatch",
        source_task_id="P0-TW-PAPER-ACTIVATE-001",
        registry_entry=registry_entry,
        metadata={"tenant_id": TENANT_ID, "contract_only": True},
    )
    assert plan["artifact_type"] == "execution_bundle"
    assert plan["status"] == "approved"
    assert plan["runtime_action"] == "deploy_new_binding"
    assert dispatch.idempotency_key

    pool = {
        "pool_id": POOL_ID,
        "status": "active",
        "single_runtime_enforced": True,
    }
    binding = {
        "binding_id": BINDING_ID,
        "persona_id": PERSONA_ID,
        "capital_pool_id": POOL_ID,
        "status": "active",
        "allowed_deployment_scope": "paper",
        "approval_decision_id": APPROVAL_ID,
    }
    admissibility = {
        "persona_id": PERSONA_ID,
        "capital_pool_id": POOL_ID,
        "target_stage": "paper",
        "permitted": True,
        "pool_status": "active",
        "single_runtime_enforced": True,
        "binding_id": BINDING_ID,
        "binding_status": "active",
        "allowed_deployment_scope": "paper",
    }
    deploy_request = {
        "plan_id": PLAN_ID,
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": ARTIFACT_ID,
        "artifact_version": VERSION,
        "strategy_id": STRATEGY_ID,
        "approval_decision_id": APPROVAL_ID,
        "sponsor_persona_id": PERSONA_ID,
        "capital_pool_id": POOL_ID,
        "persona_capital_binding_id": BINDING_ID,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "runtime_id": "runtime-tw-equity-paper",
    }
    report = deploy_authority.verify_deploy_authorities(
        deploy_request,
        deployment_base_url="http://deployment.contract",
        registry_base_url="http://registry.contract",
        governance_base_url="http://governance.contract",
        capital_base_url="http://capital.contract",
        fetch_json=_fetcher(
            plan=plan,
            registry_view={
                "entry": registry_entry,
                "deployment_stage": "none",
            },
            approval=approval,
            pool=pool,
            admissibility=admissibility,
            binding=binding,
        ),
        now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
    )

    assert report["status"] == "passed"
    assert report["authority"] == (
        "canonical_deployment_registry_governance_capital"
    )
    assert report["registry_deployment_stage"] == "none"
    assert report["artifact_id"] == ARTIFACT_ID
    assert report["approval_decision_id"] == APPROVAL_ID
    assert report["persona_capital_binding_id"] == BINDING_ID
    assert report["deployment_plan_transition_type"] == "activate"
    assert report["deployment_plan_runtime_action"] == "deploy_new_binding"
    json.dumps(report, allow_nan=False)
