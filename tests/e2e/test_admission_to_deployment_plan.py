"""OODA-E2E-004: CandidateArtifact -> ApprovalDecision -> DeploymentPlan(paper).

Decide-stage E2E proof that a candidate registry artifact can receive a
formal ApprovalDecision, advance to artifact_state=approved, then produce a
paper DeploymentPlan whose pool/runtime compatibility check passes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from approval_decision import (  # noqa: E402
    ActorRole,
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    RiskLevel,
    TargetType,
)
from deployment_plan import (  # noqa: E402
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentStage,
    RollbackRef,
    StagePlanner,
    validate_plan,
)
from pool_runtime_compat import check_compatibility  # noqa: E402
from services.registry.models import ArtifactState, RegistryEntryCreate, RegistryEntryView  # noqa: E402
from services.registry.split_api import RegistryService  # noqa: E402
from services.registry.storage import RegistryStore  # noqa: E402


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "candidate_artifact_for_decision.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_registry_service() -> RegistryService:
    return RegistryService(RegistryStore())


def _register_candidate(fixture: dict[str, Any], service: RegistryService) -> RegistryEntryView:
    candidate = fixture["candidate_artifact"]
    payload = RegistryEntryCreate(**candidate["registry_entry_create"])
    return service.register(payload, candidate["registry_id"])


def _build_proposed_decision(fixture: dict[str, Any]) -> ApprovalDecision:
    candidate = fixture["candidate_artifact"]
    payload = candidate["registry_entry_create"]
    approval = fixture["approval_decision"]
    binding = fixture["persona_capital_binding"]
    pool = fixture["capital_pool"]

    return ApprovalDecision.create_proposed(
        decision_id=approval["decision_id"],
        target_type=TargetType.REGISTRY_ENTRY,
        target_id=candidate["registry_id"],
        target_version=payload["version"],
        risk_level=RiskLevel(approval["risk_level"]),
        capital_pool_id=pool["pool_id"],
        persona_id=binding["persona_id"],
    )


def _evidence_refs(fixture: dict[str, Any]) -> list[EvidenceRef]:
    return [
        EvidenceRef.from_dict(ref)
        for ref in fixture["approval_decision"].get("evidence_refs", [])
    ]


def _approve_candidate(
    fixture: dict[str, Any],
    *,
    registry_service: RegistryService,
    decision_store: ApprovalDecisionStore,
) -> tuple[ApprovalDecision, RegistryEntryView]:
    candidate_view = _register_candidate(fixture, registry_service)
    assert candidate_view.entry.artifact_state == ArtifactState.CANDIDATE

    approval = fixture["approval_decision"]
    decision = _build_proposed_decision(fixture)
    assert decision.decision_state == DecisionState.PROPOSED

    decision.accept_review(ActorRole(approval["actor_role"]), approval["actor_id"])
    assert decision.decision_state == DecisionState.UNDER_REVIEW

    decision.decide(
        DecisionOutcome.APPROVED,
        approval["rationale"],
        evidence_refs=_evidence_refs(fixture),
    )
    assert decision.decision_state == DecisionState.DECIDED
    assert decision.decision == DecisionOutcome.APPROVED
    decision_store.put(decision)

    approved_view = registry_service.advance_artifact_state(
        candidate_view.entry.registry_id,
        ArtifactState.APPROVED,
        approver=str(decision.actor_id),
    )
    return decision, approved_view


def _registry_entry_for_plan(view: RegistryEntryView) -> dict[str, Any]:
    entry = view.entry
    return {
        "registry_id": entry.registry_id,
        "artifact_type": entry.artifact_type.value,
        "strategy_id": entry.strategy_id,
        "version": entry.version,
        "artifact_state": entry.artifact_state.value,
        "checksum": entry.checksum,
        "approved_at": entry.approved_at,
        "lineage": entry.lineage.to_dict(),
        "deployment_summary": (
            entry.deployment_summary.to_dict()
            if entry.deployment_summary
            else {"current_stage": "none"}
        ),
        "created_at": entry.created_at,
    }


def _create_paper_plan(
    fixture: dict[str, Any],
    *,
    decision: ApprovalDecision,
    registry_entry: dict[str, Any],
):
    planner = StagePlanner()
    deployment = fixture["deployment_plan"]
    runtime_requirements = fixture["runtime_requirements"]
    binding = fixture["persona_capital_binding"]

    return planner.create_plan(
        plan_id=deployment["plan_id"],
        approval_decision_id=decision.decision_id,
        approval_decision=decision.to_dict(),
        registry_entry=registry_entry,
        capital_pool_id=fixture["capital_pool"]["pool_id"],
        target_stage=DeploymentStage.PAPER,
        current_stage=deployment["current_stage"],
        created_by=deployment["created_by"],
        sponsor_persona_id=binding["persona_id"],
        runtime_config_ref=deployment["runtime_config_ref"],
        rollback=RollbackRef.from_dict(deployment["rollback"]),
        pre_checks=deployment["pre_checks"],
        post_checks=deployment["post_checks"],
        metadata={
            "target_size": deployment["target_size"],
            "runtime_requirements": runtime_requirements,
            "persona_capital_binding_id": binding["binding_id"],
        },
    )


def test_approval_decision_lifecycle_advances_candidate_artifact_to_approved() -> None:
    fixture = _load_fixture()
    registry_service = _make_registry_service()
    decision_store = ApprovalDecisionStore()

    decision, approved_view = _approve_candidate(
        fixture,
        registry_service=registry_service,
        decision_store=decision_store,
    )

    persisted = decision_store.get(decision.decision_id)
    assert persisted is not None
    assert persisted.decision_state == DecisionState.DECIDED
    assert persisted.decision == DecisionOutcome.APPROVED
    assert approved_view.entry.artifact_state == ArtifactState.APPROVED
    assert approved_view.entry.artifact_state != ArtifactState.CANDIDATE
    assert approved_view.entry.approver == fixture["approval_decision"]["actor_id"]
    assert approved_view.entry.approved_at is not None


def test_approved_artifact_creates_paper_deployment_plan_and_dep004_passes() -> None:
    fixture = _load_fixture()
    registry_service = _make_registry_service()
    decision_store = ApprovalDecisionStore()
    plan_store = DeploymentPlanStore()

    decision, approved_view = _approve_candidate(
        fixture,
        registry_service=registry_service,
        decision_store=decision_store,
    )
    plan = _create_paper_plan(
        fixture,
        decision=decision,
        registry_entry=_registry_entry_for_plan(approved_view),
    )
    plan_store.put(plan)

    assert validate_plan(plan) == []
    persisted = plan_store.get(plan.plan_id)
    assert persisted is not None
    assert persisted.target_stage == DeploymentStage.PAPER
    assert persisted.approval_decision_id == decision.decision_id
    assert persisted.artifact_id == approved_view.entry.registry_id
    assert persisted.status == "approved" or getattr(persisted.status, "value", None) == "approved"

    compat = check_compatibility(
        fixture["capital_pool"]["pool_id"],
        plan.plan_id,
        capital_pool=fixture["capital_pool"],
        deployment_plan=plan,
        runtime_requirements=fixture["runtime_requirements"],
        persona_capital_binding=fixture["persona_capital_binding"],
    )

    assert compat["passed"] is True
    assert compat["rejection_reasons"] == []
    assert compat["details"]["deployment_stage"] == "paper"
    assert compat["details"]["runtime_mode"] == "paper"
    assert compat["details"]["target_size"] == fixture["deployment_plan"]["target_size"]
    assert compat["details"]["persona_capital_binding_id"] == fixture["persona_capital_binding"]["binding_id"]


def test_rejects_creating_deployment_plan_for_non_approved_artifact() -> None:
    fixture = _load_fixture()
    registry_service = _make_registry_service()
    decision_store = ApprovalDecisionStore()
    candidate_view = _register_candidate(fixture, registry_service)
    decision = _build_proposed_decision(fixture)
    approval = fixture["approval_decision"]
    decision.accept_review(ActorRole(approval["actor_role"]), approval["actor_id"])
    decision.decide(
        DecisionOutcome.APPROVED,
        approval["rationale"],
        evidence_refs=_evidence_refs(fixture),
    )
    decision_store.put(decision)

    with pytest.raises(DeploymentPlanError, match="requires artifact_state=approved"):
        _create_paper_plan(
            fixture,
            decision=decision,
            registry_entry=_registry_entry_for_plan(candidate_view),
        )
