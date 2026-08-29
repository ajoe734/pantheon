"""MPOS-P1-E2E-002: AllocationPolicyArtifact → DeploymentPlan → RuntimeBinding → paper LEAN → telemetry/lineage.

Closes Gap G1: proves that an approved multi-persona AllocationPolicyArtifact
can traverse the full allocation policy runtime loop end-to-end:

    AllocationPolicyArtifact (synthesised from Persona A/B/C proposals)
        -> registry registration (candidate)
        -> ApprovalDecision evidence
        -> registry advance (approved)
        -> DeploymentPlan  (artifact_type = allocation_policy)
        -> RuntimeBinding  (sponsor persona + persona capital binding attribution)
        -> paper LEAN run  (no live broker route)
        -> fills/telemetry capture
        -> lineage queries by runtime_binding / deployment_plan / capital_pool /
           artifact / persona_capital_binding

LEAN-dependent tests require the lean/ submodule to be initialised.
Non-LEAN tests (governance chain + lineage) always pass.
"""
from __future__ import annotations

import importlib
import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
for _p in (_GOVERNANCE_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from services.runtime_manager import (
    RuntimeBindingStatus,
    RuntimeBindingStore,
    RuntimeManagerService,
)


# ---------------------------------------------------------------------------
# Registry + governance imports
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from services.registry.service import app as _registry_app  # noqa: E402
from services.registry.storage import reset_store as _reset_registry_store  # noqa: E402

from approval_decision import (  # noqa: E402
    ActorRole,
    ApprovalDecision,
    DecisionOutcome,
    EvidenceRef,
    EvidenceRefType,
    RiskLevel,
    TargetType,
    validate_decision,
)
from deployment_plan import (  # noqa: E402
    DeploymentStage,
    RollbackRef,
    StagePlanner,
    validate_plan,
)

from services.telemetry.feedback_adapter import FeedbackStoreAdapter  # noqa: E402


# ---------------------------------------------------------------------------
# LEAN availability guard
# ---------------------------------------------------------------------------

def _lean_submodule_available() -> bool:
    lean_algo_path = REPO_ROOT / "lean" / "Algorithm.Python"
    if str(lean_algo_path) not in sys.path:
        sys.path.insert(0, str(lean_algo_path))
    try:
        importlib.import_module("pantheon_algo.smoke_loader_test")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


_LEAN_AVAILABLE = _lean_submodule_available()

from services.execution.lean_runtime.smoke_algorithm import (  # noqa: E402
    run_algorithm_smoke_from_binding,
)


# ---------------------------------------------------------------------------
# Constants for this E2E test
# ---------------------------------------------------------------------------

ALLOC_ARTIFACT_ID = "artifact-mpos-e2e-002"
ALLOC_REGISTRY_ID = "reg-alloc-mpos-e2e-002"
ALLOC_VERSION = "1.0.0"
ALLOC_CAPITAL_POOL_ID = "pool-mpos-alloc-e2e-002"
ALLOC_SPONSOR_PERSONA_ID = "persona-mpos-sponsor-e2e-002"
ALLOC_PERSONA_CAPITAL_BINDING_ID = "pcb-mpos-alloc-e2e-002"
ALLOC_APPROVAL_DECISION_ID = "approval-alloc-mpos-e2e-002"
ALLOC_DEPLOYMENT_PLAN_ID = "dp-alloc-mpos-e2e-002"
ALLOC_RUNTIME_ID = "rt-alloc-mpos-e2e-002"
ALLOC_ROLLBACK_ARTIFACT_ID = "reg-alloc-rollback-baseline-e2e-002"
ALLOC_ROLLBACK_VERSION = "0.9.0"

_registry_client = TestClient(_registry_app)


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------

def _build_allocation_policy_payload() -> dict[str, Any]:
    """Construct a multi-persona AllocationPolicyArtifact payload."""
    return {
        "version": ALLOC_VERSION,
        "registry_id": ALLOC_REGISTRY_ID,
        "allocation_policy_artifact": {
            "artifact_id": ALLOC_ARTIFACT_ID,
            "capital_pool_id": ALLOC_CAPITAL_POOL_ID,
            "scope_ref": "paper",
            "sponsor_persona_id": ALLOC_SPONSOR_PERSONA_ID,
            "synthesis_method": "weighted_fusion",
            "target_weights": {"AAPL": 0.5, "SPY": 0.3, "QQQ": 0.2},
            "created_at": "2026-06-10T00:00:00Z",
            "provenance_refs": [
                "proposal-persona-a-e2e-002",
                "proposal-persona-b-e2e-002",
                "proposal-persona-c-e2e-002",
            ],
            "conflict_resolution_log_id": "conflict-log-mpos-e2e-002",
        },
    }


def _register_artifact() -> dict[str, Any]:
    """Register the AllocationPolicyArtifact and return the candidate registry entry."""
    _reset_registry_store()
    resp = _registry_client.post(
        "/api/registry/allocation-policy-artifacts",
        json=_build_allocation_policy_payload(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["entry"]


def _register_and_approve_artifact() -> dict[str, Any]:
    """Register and advance the artifact to approved; return the approved registry entry."""
    _register_artifact()
    adv = _registry_client.post(
        f"/api/registry/allocation-policy-artifacts/{ALLOC_REGISTRY_ID}/advance",
        json={"target_state": "approved", "approver": "governance-operator-mpos"},
    )
    assert adv.status_code == 200, adv.text
    return adv.json()["entry"]


def _build_approval_decision() -> ApprovalDecision:
    """Build a fully decided ApprovalDecision targeting the allocation policy artifact."""
    decision = ApprovalDecision.create_proposed(
        decision_id=ALLOC_APPROVAL_DECISION_ID,
        target_type=TargetType.ALLOCATION_POLICY,
        target_id=ALLOC_REGISTRY_ID,
        target_version=ALLOC_VERSION,
        risk_level=RiskLevel.MEDIUM,
        capital_pool_id=ALLOC_CAPITAL_POOL_ID,
        persona_id=ALLOC_SPONSOR_PERSONA_ID,
    )
    decision.accept_review(
        actor_role=ActorRole.RISK_OWNER,
        actor_id="governance-reviewer-mpos-e2e-002",
    )
    decision.decide(
        outcome=DecisionOutcome.APPROVED,
        rationale=(
            "Multi-persona weighted fusion AllocationPolicyArtifact reviewed and approved "
            "for paper stage deployment. Conflict log and synthesis evidence confirmed. "
            "No live capital side effects permitted."
        ),
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.EVALUATOR_RESULT,
                ref_id="conflict-log-mpos-e2e-002",
                storage_ref={"backend": "inline", "path": "$.conflict_resolution_log"},
                note="Conflict resolution log from multi-persona synthesis reviewed.",
            ),
            EvidenceRef(
                ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
                ref_id=ALLOC_REGISTRY_ID,
                storage_ref={"backend": "inline", "path": "$.allocation_policy_artifact"},
                note="Allocation policy artifact registered and verified in registry.",
            ),
        ],
    )
    return decision


def _build_deployment_plan(approved_entry: dict[str, Any], decision: ApprovalDecision):
    """Create DeploymentPlan from the approved AllocationPolicyArtifact registry entry."""
    return StagePlanner().create_plan(
        plan_id=ALLOC_DEPLOYMENT_PLAN_ID,
        approval_decision_id=decision.decision_id,
        approval_decision=decision.to_dict(),
        registry_entry=approved_entry,
        capital_pool_id=ALLOC_CAPITAL_POOL_ID,
        target_stage=DeploymentStage.PAPER,
        sponsor_persona_id=ALLOC_SPONSOR_PERSONA_ID,
        metadata={"persona_capital_binding_id": ALLOC_PERSONA_CAPITAL_BINDING_ID},
        rollback=RollbackRef(
            target_artifact_id=ALLOC_ROLLBACK_ARTIFACT_ID,
            target_version=ALLOC_ROLLBACK_VERSION,
            action_type="replace",
            reason="Previous allocation policy baseline for paper rollback.",
        ),
    )


def _deploy_runtime_binding(plan):
    """Deploy a RuntimeBinding from the DeploymentPlan; return (store, binding)."""
    target_stage = plan.target_stage.value if hasattr(plan.target_stage, "value") else plan.target_stage
    plan_status = plan.status.value if hasattr(plan.status, "value") else plan.status

    service = RuntimeManagerService()
    binding = service.deploy({
        "plan_id": plan.plan_id,
        "plan_status": plan_status,
        "target_stage": target_stage,
        "artifact_id": plan.artifact_id,
        "artifact_version": plan.artifact_version,
        "capital_pool_id": plan.capital_pool_id,
        "persona_capital_binding_id": ALLOC_PERSONA_CAPITAL_BINDING_ID,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "runtime_id": ALLOC_RUNTIME_ID,
        "strategy_id": plan.strategy_id,
        "sponsor_persona_id": ALLOC_SPONSOR_PERSONA_ID,
        "metadata": {
            "persona_capital_binding_id": ALLOC_PERSONA_CAPITAL_BINDING_ID,
        },
    })
    return service.store, binding


# ---------------------------------------------------------------------------
# Test 1: Registry registration — candidate lifecycle
# ---------------------------------------------------------------------------

class TestAllocationPolicyRegistration:
    def setup_method(self):
        _reset_registry_store()

    def test_register_returns_candidate_allocation_policy(self):
        """Registering a synthesised AllocationPolicyArtifact produces a candidate entry."""
        entry = _register_artifact()
        assert entry["artifact_type"] == "allocation_policy"
        assert entry["artifact_state"] == "candidate"
        assert entry["registry_id"] == ALLOC_REGISTRY_ID
        assert entry["version"] == ALLOC_VERSION
        assert entry["strategy_id"] == ALLOC_CAPITAL_POOL_ID

    def test_lineage_maps_provenance_refs_to_source_run_ids(self):
        """Provenance refs from three persona proposals are preserved as lineage source_run_ids."""
        entry = _register_artifact()
        lineage = entry["lineage"]
        assert set(lineage["source_run_ids"]) == {
            "proposal-persona-a-e2e-002",
            "proposal-persona-b-e2e-002",
            "proposal-persona-c-e2e-002",
        }

    def test_lineage_maps_conflict_log_id_to_source_strategy_spec_id(self):
        """Conflict resolution log id is preserved in lineage for auditability."""
        entry = _register_artifact()
        assert entry["lineage"]["source_strategy_spec_id"] == "conflict-log-mpos-e2e-002"

    def test_evaluation_summary_carries_synthesis_evidence(self):
        """Evaluation summary records synthesis method, sponsor persona, and scope."""
        entry = _register_artifact()
        ev = entry["evaluation_summary"]
        assert ev["synthesis_method"] == "weighted_fusion"
        assert ev["sponsor_persona_id"] == ALLOC_SPONSOR_PERSONA_ID
        assert ev["scope_ref"] == "paper"


# ---------------------------------------------------------------------------
# Test 2: Registry advance to approved with ApprovalDecision evidence
# ---------------------------------------------------------------------------

class TestAllocationPolicyApproval:
    def setup_method(self):
        _reset_registry_store()

    def test_advance_candidate_to_approved(self):
        """AllocationPolicyArtifact advances candidate → approved."""
        approved = _register_and_approve_artifact()
        assert approved["artifact_state"] == "approved"
        assert approved["artifact_type"] == "allocation_policy"

    def test_approval_decision_targets_allocation_policy_type(self):
        """ApprovalDecision with target_type=allocation_policy validates and decides cleanly."""
        decision = _build_approval_decision()
        errors = validate_decision(decision)
        assert errors == [], f"ApprovalDecision validation errors: {errors}"
        assert (
            decision.target_type == TargetType.ALLOCATION_POLICY
            or str(decision.target_type).endswith("allocation_policy")
        )
        assert decision.target_id == ALLOC_REGISTRY_ID
        assert decision.capital_pool_id == ALLOC_CAPITAL_POOL_ID
        assert decision.persona_id == ALLOC_SPONSOR_PERSONA_ID
        assert len(decision.evidence_refs) >= 2

    def test_approved_entry_is_deployment_plan_ready(self):
        """Approved entry satisfies artifact_state gate and carries capital pool identity."""
        approved = _register_and_approve_artifact()
        assert approved["artifact_state"] == "approved"
        assert approved["artifact_type"] == "allocation_policy"
        assert approved["strategy_id"] == ALLOC_CAPITAL_POOL_ID
        assert approved["registry_id"] == ALLOC_REGISTRY_ID


# ---------------------------------------------------------------------------
# Test 3: DeploymentPlan carries artifact_type=allocation_policy
# ---------------------------------------------------------------------------

class TestDeploymentPlanFromAllocationPolicy:
    def setup_method(self):
        _reset_registry_store()

    def test_deployment_plan_artifact_type_is_allocation_policy(self):
        """StagePlanner.create_plan() builds a valid paper DeploymentPlan from the approved entry."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)

        assert validate_plan(plan) == [], f"Plan validation errors: {validate_plan(plan)}"
        assert plan.artifact_type == "allocation_policy"
        assert plan.artifact_id == ALLOC_REGISTRY_ID
        assert plan.artifact_version == ALLOC_VERSION
        assert plan.strategy_id == ALLOC_CAPITAL_POOL_ID
        assert plan.capital_pool_id == ALLOC_CAPITAL_POOL_ID
        assert plan.sponsor_persona_id == ALLOC_SPONSOR_PERSONA_ID
        assert (
            plan.target_stage == DeploymentStage.PAPER
            or str(plan.target_stage) == "paper"
            or getattr(plan.target_stage, "value", None) == "paper"
        )

    def test_deployment_plan_transition_is_activate_from_none(self):
        """Allocation policy paper deployment uses activate from none transition."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)

        transition = getattr(plan.transition_type, "value", plan.transition_type)
        runtime_action = getattr(plan.runtime_action, "value", plan.runtime_action)
        assert transition == "activate"
        assert runtime_action == "deploy_new_binding"

    def test_deployment_plan_paper_scale_zero_live_capital(self):
        """Paper DeploymentPlan requires capital_scale_pct = 0.0 (no live capital)."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)

        assert plan.scale is not None
        assert plan.scale.capital_scale_pct == 0.0
        assert plan.scale.gross_scale_pct == 100.0

    def test_deployment_plan_preserves_persona_capital_binding_in_metadata(self):
        """Persona capital binding ID is preserved in plan metadata for runtime attribution."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)

        assert plan.metadata is not None
        assert plan.metadata.get("persona_capital_binding_id") == ALLOC_PERSONA_CAPITAL_BINDING_ID


# ---------------------------------------------------------------------------
# Test 4: RuntimeBinding with sponsor persona and persona capital attribution
# ---------------------------------------------------------------------------

class TestRuntimeBindingFromAllocationPolicy:
    def setup_method(self):
        _reset_registry_store()

    def test_runtime_binding_is_paper_mode(self):
        """RuntimeManager creates an active paper RuntimeBinding for the allocation policy plan."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        store, binding = _deploy_runtime_binding(plan)

        assert binding.deployment_mode == "paper"
        assert binding.status == RuntimeBindingStatus.ACTIVE.value

    def test_runtime_binding_artifact_matches_allocation_policy_registry_id(self):
        """RuntimeBinding.artifact_id equals the approved allocation policy registry ID."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        assert binding.artifact_id == ALLOC_REGISTRY_ID
        assert binding.artifact_version == ALLOC_VERSION

    def test_runtime_binding_carries_persona_capital_binding_attribution(self):
        """RuntimeBinding carries persona_capital_binding_id for sponsor attribution."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        assert binding.persona_capital_binding_id == ALLOC_PERSONA_CAPITAL_BINDING_ID
        assert binding.plan_id == ALLOC_DEPLOYMENT_PLAN_ID
        assert binding.capital_pool_id == ALLOC_CAPITAL_POOL_ID

    def test_runtime_binding_stored_and_retrievable(self):
        """RuntimeManager persists the binding; it is retrievable by binding_id."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        store, binding = _deploy_runtime_binding(plan)

        retrieved = store.get(binding.binding_id)
        assert retrieved is not None
        assert retrieved.deployment_mode == "paper"
        assert retrieved.artifact_id == ALLOC_REGISTRY_ID


# ---------------------------------------------------------------------------
# Test 5: Paper LEAN run — requires LEAN submodule
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _LEAN_AVAILABLE,
    reason="LEAN submodule (lean/Algorithm.Python/pantheon_algo) not initialised",
)
class TestPaperLeanRunWithAllocationPolicyBinding:
    def setup_method(self):
        _reset_registry_store()

    def test_paper_run_fires_5_on_data_callbacks_and_records_fills(self):
        """Paper LEAN run through allocation policy binding produces ≥ 1 fill."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        result = run_algorithm_smoke_from_binding(plan.to_dict(), binding)

        assert result.synthetic_bar_count == 5
        assert result.raw_on_data_callbacks == 5
        assert result.fill_count >= 1

    def test_no_live_broker_order_route_during_paper_run(self):
        """BROKER_PRODUCTION_LIVE_ENABLED must be false during the allocation policy paper run."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        result = run_algorithm_smoke_from_binding(plan.to_dict(), binding)

        assert result.broker_production_live_enabled == "false"
        assert result.fill_count >= 1
        fill = result.fill_events[0]
        assert fill.get("submitted_to_broker") is False

    def test_runtime_context_carries_allocation_policy_plan_and_binding_ids(self):
        """LEAN runtime_context reflects the allocation policy DeploymentPlan and RuntimeBinding."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        result = run_algorithm_smoke_from_binding(plan.to_dict(), binding)

        ctx = result.runtime_context
        assert ctx["deployment_plan_id"] == ALLOC_DEPLOYMENT_PLAN_ID
        assert ctx["runtime_binding_id"] == binding.binding_id
        assert ctx["artifact_id"] == ALLOC_REGISTRY_ID
        assert ctx["artifact_version"] == ALLOC_VERSION
        assert ctx["deployment_stage"] == "paper"

    def test_loaded_metadata_artifact_type_is_allocation_policy(self):
        """Object store metadata loaded by LEAN algorithm carries artifact_type=allocation_policy."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        result = run_algorithm_smoke_from_binding(plan.to_dict(), binding)

        assert result.loaded_metadata["artifact_type"] == "allocation_policy"
        assert result.loaded_metadata["deployment_stage"] == "paper"
        assert result.loaded_metadata["artifact_state"] == "approved"
        assert result.loaded_metadata["deployment_plan_id"] == ALLOC_DEPLOYMENT_PLAN_ID
        assert result.loaded_metadata["runtime_binding_id"] == binding.binding_id


# ---------------------------------------------------------------------------
# Test 6: Fills telemetry capture and lineage queries on all five dimensions
#
# This test uses a synthetic fill event derived from the governance chain without
# requiring the LEAN submodule. Lineage correctness is independent of LEAN runtime.
# ---------------------------------------------------------------------------

class TestFillsTelemetryLineage:
    def setup_method(self):
        _reset_registry_store()

    def _build_fill_telemetry_event(self, plan, binding) -> dict[str, Any]:
        """Build a canonical fill_observation event from an allocation policy paper run."""
        return {
            "event_id": "fill-alloc-mpos-e2e-002-001",
            "event_type": "fill_observation",
            "created_at": "2026-06-10T00:00:00Z",
            "runtime_binding_id": binding.binding_id,
            "deployment_plan_id": plan.plan_id,
            "capital_pool_id": plan.capital_pool_id,
            "persona_capital_binding_id": ALLOC_PERSONA_CAPITAL_BINDING_ID,
            "artifact_id": plan.artifact_id,
            "artifact_version": plan.artifact_version,
            "submitted_to_broker": False,
            "target": {
                "strategy_id": plan.strategy_id,
                "registry_id": plan.artifact_id,
                "promotion_state": "paper",
                "artifact_version": plan.artifact_version,
            },
        }

    def test_fill_event_is_paper_only_no_live_broker(self):
        """Fill telemetry event captured from paper run carries submitted_to_broker=False."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        fill_event = self._build_fill_telemetry_event(plan, binding)
        adapter = FeedbackStoreAdapter()
        stored = adapter.ingest_telemetry_event(
            fill_event, strategy_id=plan.strategy_id, promotion_state="paper"
        )

        assert stored["event_id"] == "fill-alloc-mpos-e2e-002-001"
        assert stored.get("submitted_to_broker") is False
        assert stored["event_type"] == "fill_observation"

    def test_lineage_queryable_by_runtime_binding(self):
        """Fills telemetry is queryable by runtime_binding lineage dimension."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        records = adapter.query_lineage_records(
            target_type="runtime_binding", target_id=binding.binding_id
        )
        assert len(records) >= 1
        assert any(r["runtime_binding_id"] == binding.binding_id for r in records)

    def test_lineage_queryable_by_deployment_plan(self):
        """Fills telemetry is queryable by deployment_plan lineage dimension."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        records = adapter.query_lineage_records(
            target_type="deployment_plan", target_id=plan.plan_id
        )
        assert len(records) >= 1
        assert any(r["deployment_plan_id"] == plan.plan_id for r in records)

    def test_lineage_queryable_by_capital_pool(self):
        """Fills telemetry is queryable by capital_pool lineage dimension."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        records = adapter.query_lineage_records(
            target_type="capital_pool", target_id=ALLOC_CAPITAL_POOL_ID
        )
        assert len(records) >= 1
        assert any(r["capital_pool_id"] == ALLOC_CAPITAL_POOL_ID for r in records)

    def test_lineage_queryable_by_artifact(self):
        """Fills telemetry is queryable by artifact lineage dimension (registry_id@version)."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        artifact_ref = f"{ALLOC_REGISTRY_ID}@{ALLOC_VERSION}"
        records = adapter.query_lineage_records(
            target_type="artifact", target_id=artifact_ref
        )
        assert len(records) >= 1
        assert any(r["artifact_ref"] == artifact_ref for r in records)

    def test_lineage_queryable_by_persona_capital_binding(self):
        """Fills telemetry is queryable by persona_capital_binding lineage dimension."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        records = adapter.query_lineage_records(
            target_type="persona_capital_binding",
            target_id=ALLOC_PERSONA_CAPITAL_BINDING_ID,
        )
        assert len(records) >= 1
        assert any(
            r["persona_capital_binding_id"] == ALLOC_PERSONA_CAPITAL_BINDING_ID
            for r in records
        )

    def test_lineage_summary_covers_all_five_dimensions(self):
        """build_lineage_summary by runtime_binding returns refs for all five lineage dimensions."""
        approved = _register_and_approve_artifact()
        decision = _build_approval_decision()
        plan = _build_deployment_plan(approved, decision)
        _, binding = _deploy_runtime_binding(plan)

        adapter = FeedbackStoreAdapter()
        adapter.ingest_telemetry_event(
            self._build_fill_telemetry_event(plan, binding),
            strategy_id=plan.strategy_id,
            promotion_state="paper",
        )

        summary = adapter.build_lineage_summary(
            target_type="runtime_binding", target_id=binding.binding_id
        )
        refs = summary["refs"]
        assert binding.binding_id in refs["runtime_binding_ids"]
        assert ALLOC_DEPLOYMENT_PLAN_ID in refs["deployment_plan_ids"]
        assert ALLOC_CAPITAL_POOL_ID in refs["capital_pool_ids"]
        assert f"{ALLOC_REGISTRY_ID}@{ALLOC_VERSION}" in refs["artifact_refs"]
        assert ALLOC_PERSONA_CAPITAL_BINDING_ID in refs["persona_capital_binding_ids"]
