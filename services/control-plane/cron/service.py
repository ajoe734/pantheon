from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import OpenClawRuntimePin, WorkflowDefinition, WorkflowRunResult, utc_now
from .openclaw_client import OpenClawCronClient
from .schema_validation import validate_workflow_handoff
from .workflows import PERSONA_FIRST_EVALUATION_WORKFLOW_ID, get_workflow_definition

_GOVERNANCE_DIR = Path(__file__).resolve().parents[1] / "governance"
if str(_GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE_DIR))

from deployment_plan import (  # noqa: E402
    DeploymentPlanError,
    DeploymentScale,
    DeploymentStage,
    RollbackRef,
    RuntimeAction,
    ScheduleWindow,
    StagePlanner,
)
from deployment_saga import DeploymentSagaOrchestrator  # noqa: E402
from pool_runtime_compat import enforce_compatibility  # noqa: E402

PromotionError = DeploymentPlanError


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _compatibility_context(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    explicit = payload.get("pool_runtime_compat") or payload.get("pool_runtime_compatibility")
    if isinstance(explicit, Mapping):
        return explicit

    keys = {
        "capital_pool",
        "runtime_requirements",
        "persona_capital_binding",
        "capital_pool_store",
        "persona_capital_binding_store",
    }
    context = {key: payload.get(key) for key in keys if payload.get(key) is not None}
    return context


class CronOrchestrator:
    """Governed local wrapper around upstream OpenClaw cron workflows."""

    def __init__(
        self,
        client: OpenClawCronClient | None = None,
        runtime_pin: OpenClawRuntimePin | None = None,
        stage_planner_factory: Callable[[], StagePlanner] = StagePlanner,
        saga_orchestrator_factory: Callable[[], DeploymentSagaOrchestrator] = DeploymentSagaOrchestrator,
        promotion_gate_factory: Callable[[], StagePlanner] | None = None,
    ):
        self.client = client or OpenClawCronClient(runtime_pin=runtime_pin)
        # `promotion_gate_factory` is kept as a compatibility alias while callers
        # migrate to canonical DeploymentPlan terminology.
        self.stage_planner_factory = promotion_gate_factory or stage_planner_factory
        self.saga_orchestrator_factory = saga_orchestrator_factory

    def run(self, workflow_id: str, payload: dict[str, Any], dry_run: bool = True) -> WorkflowRunResult:
        workflow = get_workflow_definition(workflow_id)
        dispatch_request = self.client.prepare_dispatch(workflow, payload)
        upstream_response = self.client.dispatch_prepared(workflow_id, dispatch_request, dry_run=dry_run)

        if workflow.workflow_id == "pantheon.ingest":
            handoff = self._build_ingest_handoff(workflow, payload)
            return WorkflowRunResult(
                workflow_id=workflow_id,
                dispatch_request=dispatch_request,
                upstream_response=upstream_response,
                handoff=handoff,
                notes=["Ingest emits a governed research_package handoff for downstream normalization."],
            )

        if workflow.workflow_id == "pantheon.review":
            handoff = self._build_review_handoff(workflow, payload)
            return WorkflowRunResult(
                workflow_id=workflow_id,
                dispatch_request=dispatch_request,
                upstream_response=upstream_response,
                handoff=handoff,
                notes=["Review creates an approval_request instead of approving candidate artifacts directly."],
            )

        if workflow.workflow_id == "pantheon.retrain":
            handoff = self._build_retrain_handoff(workflow, payload)
            return WorkflowRunResult(
                workflow_id=workflow_id,
                dispatch_request=dispatch_request,
                upstream_response=upstream_response,
                handoff=handoff,
                notes=["Retrain emits a registry_submission handoff and stays in research context."],
            )

        if workflow.workflow_id == "pantheon.deploy":
            return self._run_deploy(workflow, payload, dispatch_request, upstream_response)

        if workflow.workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
            return WorkflowRunResult(
                workflow_id=workflow_id,
                dispatch_request=dispatch_request,
                upstream_response=upstream_response,
                notes=[
                    "First evaluation resolves the persona's current canonical paper runtime at execution time."
                ],
            )

        raise ValueError(f"Unsupported workflow: {workflow_id}")

    def _build_ingest_handoff(self, workflow: WorkflowDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_spec = {
            "spec_version": "1.0",
            "strategy_id": payload["strategy_id"],
            "title": payload["title"],
            "hypothesis": payload["hypothesis"],
            "objective": payload["objective"],
            "market_scope": {
                "symbols": payload["symbols"],
                "asset_classes": payload.get("asset_classes", []),
                "venues": payload.get("venues", []),
                "frequency": payload["frequency"],
            },
            "data_dependencies": payload.get(
                "data_dependencies",
                [{"ref": source_ref, "kind": "paper"} for source_ref in payload["source_refs"]],
            ),
            "execution_profile": {
                "signal_schema_version": payload.get("signal_schema_version", "1.0"),
                "quantity_type": payload.get("quantity_type", "PERCENT_PORTFOLIO"),
                "rebalance_cadence": payload.get("rebalance_cadence", payload["frequency"]),
                "execution_mode_hint": "research",
            },
            "evaluation_plan": {
                "metrics": payload.get("metrics", ["sharpe_ratio", "max_drawdown"]),
                "candidate_gate": payload.get("candidate_gate", "replication_success"),
                "paper_gate": payload.get("paper_gate", "risk_review_passed"),
                "live_gate": payload.get("live_gate", "operator_approval_and_rollback_ready"),
            },
            "governance": {
                "approval_required": payload.get("approval_required", False),
                "policy_id": payload.get("policy_id", workflow.policy_id),
                "risk_profile": payload.get("risk_profile", "research-default"),
            },
            "provenance": {
                "source_kind": payload.get("source_kind", "workflow"),
                "created_at": utc_now(),
                "source_refs": list(payload["source_refs"]),
                "created_by": payload.get("created_by", workflow.workflow_id),
            },
        }
        return self._build_handoff(workflow, payload, strategy_spec)

    def _build_review_handoff(self, workflow: WorkflowDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        return self._build_handoff(
            workflow,
            payload,
            {
                "strategy_id": payload["strategy_id"],
                "spec_ref": payload["spec_ref"],
            },
        )

    def _build_retrain_handoff(self, workflow: WorkflowDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        return self._build_handoff(
            workflow,
            payload,
            {
                "strategy_id": payload["strategy_id"],
                "spec_ref": payload["spec_ref"],
            },
        )

    def _build_handoff(
        self,
        workflow: WorkflowDefinition,
        payload: dict[str, Any],
        strategy_spec: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = utc_now()
        registry_hints = _compact_dict(
            {
                "artifact_type": workflow.artifact_type,
                "initial_lifecycle_state": workflow.initial_lifecycle_state,
                "lineage_ref": payload.get("lineage_ref"),
                "producer_run_id": payload.get("producer_run_id"),
                "source_dataset_refs": payload.get("source_dataset_refs") or payload.get("feedback_dataset_refs", []),
            }
        )
        governance_context = _compact_dict(
            {
                "approval_required": payload.get("approval_required", workflow.approval_required),
                "execution_context": payload.get("execution_context", workflow.execution_context),
                "policy_id": payload.get("policy_id", workflow.policy_id),
                "risk_profile": payload.get("risk_profile", "governed-default"),
            }
        )
        provenance = _compact_dict(
            {
                "created_by": payload.get("created_by", workflow.workflow_id),
                "created_at": timestamp,
                "source_task_id": payload.get("source_task_id"),
                "source_channel": "cron",
                "source_persona": payload.get("source_persona"),
            }
        )

        handoff = {
            "handoff_version": "1.0",
            "handoff_id": payload.get("handoff_id", f"{workflow.workflow_id}-{uuid.uuid4()}"),
            "handoff_type": workflow.handoff_type,
            "from_stage": workflow.from_stage,
            "to_stage": workflow.to_stage,
            "created_at": timestamp,
            "strategy_spec": strategy_spec,
            "registry_hints": registry_hints,
            "governance_context": governance_context,
            "provenance": provenance,
        }
        validate_workflow_handoff(handoff)
        return handoff

    def _run_deploy(
        self,
        workflow: WorkflowDefinition,
        payload: dict[str, Any],
        dispatch_request: dict[str, Any],
        upstream_response: dict[str, Any],
    ) -> WorkflowRunResult:
        entry = copy.deepcopy(payload["registry_entry"])
        target_stage_raw = payload.get("target_stage") or payload.get("target_state")
        if not target_stage_raw:
            raise DeploymentPlanError("Deploy payload requires target_stage")

        planner = self.stage_planner_factory()
        target_stage = DeploymentStage(target_stage_raw)
        approval_decision = payload.get("approval_decision")
        approval_decision_id = (
            payload.get("approval_decision_id")
            or entry.get("approval_decision_id")
            or (approval_decision or {}).get("decision_id")
        )
        if not approval_decision_id:
            raise DeploymentPlanError(
                "Deploy payload requires approval_decision_id or registry_entry.approval_decision_id"
            )

        plan = planner.create_plan(
            plan_id=payload.get("plan_id", f"deployment-plan-{uuid.uuid4()}"),
            approval_decision_id=approval_decision_id,
            approval_decision=approval_decision,
            registry_entry=entry,
            capital_pool_id=payload["capital_pool_id"],
            target_stage=target_stage,
            current_stage=payload.get("current_stage"),
            created_by=payload.get("created_by", workflow.workflow_id),
            sponsor_persona_id=payload.get("sponsor_persona_id"),
            runtime_config_ref=payload.get("runtime_config_ref"),
            binding_id=payload.get("binding_id"),
            schedule_window=_coerce_schedule_window(payload.get("schedule_window")),
            scale=_coerce_scale(payload.get("scale")),
            rollback=_resolve_rollback_ref(payload, entry),
            pre_checks=payload.get("pre_checks"),
            post_checks=payload.get("post_checks"),
            metadata=payload.get("metadata"),
            supersedes_plan_id=payload.get("supersedes_plan_id"),
        )
        compatibility_result = None
        compat_context = _compatibility_context(payload)
        if compat_context:
            compatibility_result = enforce_compatibility(
                payload["capital_pool_id"],
                plan.plan_id,
                deployment_plan=plan,
                capital_pool=compat_context.get("capital_pool"),
                runtime_requirements=compat_context.get("runtime_requirements"),
                persona_capital_binding=compat_context.get("persona_capital_binding"),
                capital_pool_store=compat_context.get("capital_pool_store"),
                persona_capital_binding_store=compat_context.get("persona_capital_binding_store"),
                error_factory=DeploymentPlanError,
            )
        projection = planner.build_execution_projection(plan, entry)
        saga_bootstrap = self.saga_orchestrator_factory().bootstrap(
            plan,
            trace_id=dispatch_request.get("request_id", f"deploy-trace-{uuid.uuid4()}"),
            metadata={
                "workflow_id": workflow.workflow_id,
                "source_task_id": payload.get("source_task_id"),
            },
        )
        execution_projection = {
            "metadata_key": projection.metadata_key,
            "artifact_key": projection.artifact_key,
            "metadata": projection.metadata,
        }
        updated_entry = planner.normalize_registry_entry(entry)
        updated_entry["deployment_summary"] = {
            "current_stage": target_stage.value,
            "deployment_plan_id": plan.plan_id,
            "last_transition_at": plan.created_at,
        }

        deployment_request = {
            "plan": plan.to_dict(),
            "strategy_id": updated_entry["strategy_id"],
            "version": updated_entry["version"],
            "target_stage": target_stage.value,
            "execution_context": _execution_context_for_stage(target_stage),
            "artifact_loader_contract": "EX-001",
            "deployment_contract": "DEP-001",
            "consistency_contract": "DEP-002",
            "execution_projection": execution_projection,
            "deployment_saga": saga_bootstrap.to_dict(),
        }
        if compatibility_result is not None:
            deployment_request["pool_runtime_compatibility"] = compatibility_result

        return WorkflowRunResult(
            workflow_id=workflow.workflow_id,
            dispatch_request=dispatch_request,
            upstream_response=upstream_response,
            registry_entry=updated_entry,
            deployment_request=deployment_request,
            notes=[
                "Deploy creates a first-class DeploymentPlan before any execution projection is emitted.",
                "Deploy bootstraps a DeploymentSaga and first outbox event atomically with the saga aggregate.",
                "Rollback linkage is explicit on every active-stage DeploymentPlan.",
                "No direct LEAN call is allowed from cron deploy.",
            ],
        )


def _execution_context_for_stage(target_stage: DeploymentStage) -> str:
    if target_stage == DeploymentStage.PAPER:
        return "paper"
    if target_stage in {DeploymentStage.CANARY, DeploymentStage.LIVE}:
        return "live"
    return "status"


def _coerce_schedule_window(value: Any) -> ScheduleWindow | None:
    if isinstance(value, Mapping):
        return ScheduleWindow.from_dict(value)
    return None


def _coerce_scale(value: Any) -> DeploymentScale | None:
    if isinstance(value, Mapping):
        return DeploymentScale.from_dict(value)
    return None


def _resolve_rollback_ref(payload: Mapping[str, Any], entry: Mapping[str, Any]) -> RollbackRef | None:
    explicit = payload.get("rollback")
    if isinstance(explicit, Mapping):
        return RollbackRef.from_dict(explicit)

    action_type = payload.get("rollback_action", RuntimeAction.REPLACE_BINDING.value)
    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping):
        rollback = metadata.get("rollback")
        if isinstance(rollback, Mapping):
            return RollbackRef(
                target_artifact_id=str(rollback["target_registry_id"]),
                target_version=str(rollback["target_version"]),
                action_type=action_type,
                reason=rollback.get("reason"),
                verified_at=rollback.get("verified_at"),
            )

        if metadata.get("rollback_target_registry_id") and entry.get("rollback_target"):
            return RollbackRef(
                target_artifact_id=str(metadata["rollback_target_registry_id"]),
                target_version=str(entry["rollback_target"]),
                action_type=action_type,
                reason=metadata.get("rollback_reason"),
                verified_at=metadata.get("rollback_verified_at"),
            )

    return None
