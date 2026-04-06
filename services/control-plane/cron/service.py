from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from models import OpenClawRuntimePin, WorkflowDefinition, WorkflowRunResult, utc_now
from openclaw_client import OpenClawCronClient
from schema_validation import validate_workflow_handoff
from workflows import get_workflow_definition

_PROMOTION_GATE_DIR = Path(__file__).resolve().parents[2] / "registry" / "promotion"
if str(_PROMOTION_GATE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROMOTION_GATE_DIR))

from gate import PromotionError, PromotionGate, PromotionState  # noqa: E402


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


class CronOrchestrator:
    """Governed local wrapper around upstream OpenClaw cron workflows."""

    def __init__(
        self,
        client: OpenClawCronClient | None = None,
        runtime_pin: OpenClawRuntimePin | None = None,
        promotion_gate_factory: Callable[[], PromotionGate] = PromotionGate,
    ):
        self.client = client or OpenClawCronClient(runtime_pin=runtime_pin)
        self.promotion_gate_factory = promotion_gate_factory

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
        target_state = PromotionState(payload["target_state"])
        approver = payload.get("approver")

        gate = self.promotion_gate_factory()
        updated_entry = gate.promote(entry, target_state, approver=approver)

        deployment_request = {
            "strategy_id": updated_entry["strategy_id"],
            "version": updated_entry["version"],
            "target_state": updated_entry["lifecycle_state"],
            "execution_context": "live" if updated_entry["lifecycle_state"] == "live" else workflow.execution_context,
            "artifact_loader_contract": "EX-001",
            "promotion_gate": "REG-002",
        }

        return WorkflowRunResult(
            workflow_id=workflow.workflow_id,
            dispatch_request=dispatch_request,
            upstream_response=upstream_response,
            registry_entry=updated_entry,
            deployment_request=deployment_request,
            notes=[
                "Deploy is promotion-gated before any execution projection is emitted.",
                "No direct LEAN call is allowed from cron deploy.",
            ],
        )
