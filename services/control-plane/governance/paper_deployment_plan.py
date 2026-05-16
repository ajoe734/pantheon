"""
paper_deployment_plan - MGMT-PAPER-003

Factory and evidence writer for a paper-mode DeploymentPlan packet used in
the Management Paper Loop Proof (Track E / EPIC-02).

Scope
-----
Creates a concrete DeploymentPlan for the approved paper StrategySpec target
from MGMT-PAPER-001 / MGMT-PAPER-002:

    none -> paper, transition=activate, runtime_action=deploy_new_binding

The packet keeps live broker and live capital side effects disabled and emits
a runtime bootstrap preview that can be consumed by later paper RuntimeBinding
and OODA proof tasks.

Usage
-----
Run as a standalone script to generate the evidence artifact:

    python3 services/control-plane/governance/paper_deployment_plan.py

Or import the factory from another module:

    from paper_deployment_plan import build_paper_deployment_plan, PaperDeploymentContext
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    from deployment_plan import (
        DeploymentPlan,
        DeploymentPlanError,
        DeploymentStage,
        RollbackRef,
        StagePlanner,
        validate_plan,
        validate_plan_json,
    )
except ImportError:
    from services.control_plane.governance.deployment_plan import (  # type: ignore
        DeploymentPlan,
        DeploymentPlanError,
        DeploymentStage,
        RollbackRef,
        StagePlanner,
        validate_plan,
        validate_plan_json,
    )


PAPER_ENVIRONMENT = "paper"
PAPER_RUNTIME_ROLE = "paper"
PAPER_RUNTIME_PROFILE = "pantheon_lean_paper_v1"
ENGINE_BRIDGE_REPO = "ajoe734/pantheon-lean.git"
ENGINE_BRIDGE_PATH = "pantheon/lean"
ENGINE_BRIDGE_COMMIT = "paper-loop-bridge-commit-001"


@dataclass(frozen=True)
class PaperDeploymentContext:
    """Stable identifiers for the paper-loop DeploymentPlan packet."""

    plan_id: str
    approval_decision_id: str
    artifact_id: str
    artifact_version: str
    artifact_type: str
    strategy_id: str
    artifact_checksum: str
    capital_pool_id: str
    sponsor_persona_id: str
    persona_capital_binding_id: str
    created_by: str = "operator-paper-loop-01"
    runtime_config_ref: str = "launch-manifest://paper-runtime-mgmt-001"
    rollback_artifact_id: str = "strategy-spec-paper-qlib-lgbm-000"
    rollback_artifact_version: str = "0.9.0"
    approved_at: Optional[str] = None
    source_run_ids: Optional[List[str]] = None


_DEFAULT_CONTEXT = PaperDeploymentContext(
    plan_id="deployment-plan-paper-001",
    approval_decision_id="approval-paper-strategy-001",
    artifact_id="strategy-spec-paper-qlib-lgbm-001",
    artifact_version="1.0.0",
    artifact_type="strategy_spec",
    strategy_id="paper-qlib-lgbm-tw-equity-alpha",
    artifact_checksum="sha256:mgmtpaper003paperdeployment001",
    capital_pool_id="capital-pool-paper-001",
    sponsor_persona_id="persona-quant-paper-01",
    persona_capital_binding_id="pcb-paper-quant-001",
    approved_at="2026-05-15T15:14:54Z",
    source_run_ids=["MGMT-PAPER-001", "MGMT-PAPER-002"],
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_context() -> PaperDeploymentContext:
    return _DEFAULT_CONTEXT


def build_approved_registry_entry(ctx: PaperDeploymentContext | None = None) -> Dict[str, Any]:
    """Build the approved registry entry consumed by StagePlanner."""
    ctx = ctx or default_context()
    return {
        "registry_id": ctx.artifact_id,
        "artifact_type": ctx.artifact_type,
        "strategy_id": ctx.strategy_id,
        "version": ctx.artifact_version,
        "artifact_state": "approved",
        "checksum": ctx.artifact_checksum,
        "approval_decision_id": ctx.approval_decision_id,
        "approved_at": ctx.approved_at or utc_now(),
        "lineage": {
            "source_run_ids": list(ctx.source_run_ids or []),
            "strategy_spec_ref": f"strategy-spec://{ctx.artifact_id}@{ctx.artifact_version}",
            "source_artifact_refs": [
                "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
                "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            ],
        },
        "deployment_summary": {"current_stage": "none"},
        "metadata": {
            "environment": PAPER_ENVIRONMENT,
            "paper_loop_candidate": True,
            "live_capital_side_effects": False,
        },
    }


def build_approval_decision_ref(ctx: PaperDeploymentContext | None = None) -> Dict[str, Any]:
    """Build the decided approval projection required by DeploymentPlan validation."""
    ctx = ctx or default_context()
    return {
        "decision_id": ctx.approval_decision_id,
        "target_type": ctx.artifact_type,
        "target_id": ctx.artifact_id,
        "target_version": ctx.artifact_version,
        "decision_state": "decided",
        "decision": "approved",
        "actor_role": "risk_owner",
        "actor_id": "governance-reviewer-paper-01",
        "capital_pool_id": ctx.capital_pool_id,
        "persona_id": ctx.sponsor_persona_id,
    }


def build_paper_deployment_plan(
    ctx: PaperDeploymentContext | None = None,
    *,
    approval_decision: Mapping[str, Any] | None = None,
    registry_entry: Mapping[str, Any] | None = None,
) -> DeploymentPlan:
    """Create the paper DeploymentPlan from approved StrategySpec evidence."""
    ctx = ctx or default_context()
    approval_decision = approval_decision or build_approval_decision_ref(ctx)
    registry_entry = registry_entry or build_approved_registry_entry(ctx)

    planner = StagePlanner()
    return planner.create_plan(
        plan_id=ctx.plan_id,
        approval_decision_id=ctx.approval_decision_id,
        approval_decision=approval_decision,
        registry_entry=registry_entry,
        capital_pool_id=ctx.capital_pool_id,
        target_stage=DeploymentStage.PAPER,
        created_by=ctx.created_by,
        sponsor_persona_id=ctx.sponsor_persona_id,
        runtime_config_ref=ctx.runtime_config_ref,
        rollback=RollbackRef(
            target_artifact_id=ctx.rollback_artifact_id,
            target_version=ctx.rollback_artifact_version,
            action_type="replace",
            reason="Previous paper baseline for rollback-only recovery.",
        ),
        pre_checks=[
            "strategy_spec approved for paper",
            "paper runtime package available",
            "pantheon/lean bridge identity recorded",
            "live broker disabled",
            "capital binding live writes disabled",
        ],
        post_checks=[
            "runtime binding is paper",
            "runtime bootstrap request references deployment plan",
            "telemetry heartbeat accepted",
            "logged-only order evidence emitted",
        ],
        metadata={
            "runtime_role": PAPER_RUNTIME_ROLE,
            "runtime_profile": PAPER_RUNTIME_PROFILE,
            "persona_capital_binding_id": ctx.persona_capital_binding_id,
            "engine_bridge_repo": ENGINE_BRIDGE_REPO,
            "engine_bridge_path": ENGINE_BRIDGE_PATH,
            "engine_bridge_commit": ENGINE_BRIDGE_COMMIT,
            "runtime_adapter_version": "0.1.0",
            "live_broker_enabled": False,
            "live_capital_binding_enabled": False,
            "live_capital_side_effects": False,
        },
    )


def build_runtime_bootstrap_preview(
    plan: DeploymentPlan,
    registry_entry: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a minimal paper RuntimeBootstrapRequest preview without secrets."""
    metadata = plan.metadata or {}
    return {
        "request_id": "runtime-bootstrap-paper-mgmt-001",
        "trace_id": "03829d25-2c9f-44f0-981e-56a94d8ff003",
        "runtime_binding_id": None,
        "deployment_plan_id": plan.plan_id,
        "runtime_role": PAPER_RUNTIME_ROLE,
        "deployment_stage": PAPER_ENVIRONMENT,
        "bridge": {
            "path": ENGINE_BRIDGE_PATH,
            "remote": ENGINE_BRIDGE_REPO,
            "commit": ENGINE_BRIDGE_COMMIT,
        },
        "artifact": {
            "artifact_id": plan.artifact_id,
            "artifact_version": plan.artifact_version,
            "checksum": registry_entry.get("checksum"),
            "strategy_id": plan.strategy_id,
            "artifact_type": plan.artifact_type,
        },
        "capital": {
            "capital_pool_id": plan.capital_pool_id,
            "persona_capital_binding_id": metadata.get("persona_capital_binding_id"),
            "capital_scale_pct": plan.scale.capital_scale_pct if plan.scale else None,
            "gross_scale_pct": plan.scale.gross_scale_pct if plan.scale else None,
        },
        "runtime_config": {
            "config_ref": plan.runtime_config_ref,
            "runtime_profile": PAPER_RUNTIME_PROFILE,
            "paper_mode": True,
            "live_broker_enabled": False,
            "live_capital_binding_enabled": False,
        },
        "secrets_included": False,
    }


def validate_paper_deployment_packet(packet: Mapping[str, Any]) -> List[str]:
    """Validate packet-level paper deployment invariants."""
    errors: List[str] = []
    if packet.get("task_id") != "MGMT-PAPER-003":
        errors.append("task_id must be MGMT-PAPER-003")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    plan_raw = packet.get("deployment_plan")
    if not isinstance(plan_raw, Mapping):
        errors.append("deployment_plan must be present")
        return errors

    plan_json_errors = validate_plan_json(plan_raw)
    errors.extend(f"deployment_plan_json: {error}" for error in plan_json_errors)

    try:
        plan = DeploymentPlan.from_dict(plan_raw)
    except Exception as exc:
        errors.append(f"deployment_plan restore failed: {exc}")
        return errors

    errors.extend(f"deployment_plan: {error}" for error in validate_plan(plan))
    if plan.current_stage != "none":
        errors.append("deployment_plan current_stage must be none")
    if plan.target_stage != PAPER_ENVIRONMENT:
        errors.append("deployment_plan target_stage must be paper")
    if plan.transition_type != "activate":
        errors.append("deployment_plan transition_type must be activate")
    if plan.runtime_action != "deploy_new_binding":
        errors.append("deployment_plan runtime_action must be deploy_new_binding")
    if plan.scale is None or plan.scale.capital_scale_pct != 0.0:
        errors.append("paper deployment requires capital_scale_pct 0.0")

    approval = packet.get("approval_decision_ref")
    registry_entry = packet.get("approved_registry_entry")
    if isinstance(approval, Mapping):
        if approval.get("decision_id") != plan.approval_decision_id:
            errors.append("approval_decision_ref decision_id mismatch")
        if approval.get("target_id") != plan.artifact_id:
            errors.append("approval_decision_ref target_id mismatch")
        if approval.get("target_version") != plan.artifact_version:
            errors.append("approval_decision_ref target_version mismatch")
    else:
        errors.append("approval_decision_ref must be present")

    if isinstance(registry_entry, Mapping):
        if registry_entry.get("artifact_state") != "approved":
            errors.append("approved_registry_entry artifact_state must be approved")
        if registry_entry.get("registry_id") != plan.artifact_id:
            errors.append("approved_registry_entry registry_id mismatch")
    else:
        errors.append("approved_registry_entry must be present")

    preview = packet.get("runtime_bootstrap_request_preview")
    if isinstance(preview, Mapping):
        if preview.get("deployment_plan_id") != plan.plan_id:
            errors.append("runtime_bootstrap_request_preview deployment_plan_id mismatch")
        if preview.get("deployment_stage") != PAPER_ENVIRONMENT:
            errors.append("runtime_bootstrap_request_preview deployment_stage must be paper")
        if preview.get("runtime_role") != PAPER_RUNTIME_ROLE:
            errors.append("runtime_bootstrap_request_preview runtime_role must be paper")
        if preview.get("bridge", {}).get("path") != ENGINE_BRIDGE_PATH:
            errors.append("runtime_bootstrap_request_preview bridge.path must be pantheon/lean")
        if preview.get("secrets_included") is not False:
            errors.append("runtime_bootstrap_request_preview must not include secrets")
    else:
        errors.append("runtime_bootstrap_request_preview must be present")

    safety = packet.get("safety_assertions")
    if isinstance(safety, Mapping):
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety_assertion failed: {key}")
    else:
        errors.append("safety_assertions must be present")

    return errors


def build_evidence_packet(
    ctx: PaperDeploymentContext | None = None,
    *,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Build the MGMT-PAPER-003 evidence packet."""
    ctx = ctx or default_context()
    approval_decision = build_approval_decision_ref(ctx)
    registry_entry = build_approved_registry_entry(ctx)
    plan = build_paper_deployment_plan(
        ctx,
        approval_decision=approval_decision,
        registry_entry=registry_entry,
    )
    planner = StagePlanner()
    projection = planner.build_execution_projection(plan, registry_entry)
    bootstrap_preview = build_runtime_bootstrap_preview(plan, registry_entry)

    packet: Dict[str, Any] = {
        "task_id": "MGMT-PAPER-003",
        "epic": "EPIC-02 Management Paper Loop Proof",
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or utc_now(),
        "live_capital_side_effects": False,
        "source_artifacts": {
            "strategy_spec": "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
            "approval_decision": "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
        },
        "approval_decision_ref": approval_decision,
        "approved_registry_entry": registry_entry,
        "deployment_plan": plan.to_dict(),
        "deployment_execution_projection": {
            "metadata_key": projection.metadata_key,
            "artifact_key": projection.artifact_key,
            "metadata": projection.metadata,
        },
        "runtime_bootstrap_request_preview": bootstrap_preview,
        "ooda_decide_ref": {
            "approval_decision_id": plan.approval_decision_id,
            "deployment_plan_id": plan.plan_id,
        },
        "runtime_binding_input_ref": {
            "deployment_plan_id": plan.plan_id,
            "target_stage": plan.target_stage,
            "runtime_action": plan.runtime_action,
            "capital_pool_id": plan.capital_pool_id,
            "persona_capital_binding_id": (plan.metadata or {}).get("persona_capital_binding_id"),
        },
        "safety_assertions": {
            "paper_environment": plan.target_stage == PAPER_ENVIRONMENT,
            "activate_from_none": plan.current_stage == "none" and plan.transition_type == "activate",
            "deploy_new_binding": plan.runtime_action == "deploy_new_binding",
            "zero_live_capital_scale": plan.scale is not None and plan.scale.capital_scale_pct == 0.0,
            "gross_scale_is_paper_simulation": plan.scale is not None and plan.scale.gross_scale_pct == 100.0,
            "live_broker_disabled": bootstrap_preview["runtime_config"]["live_broker_enabled"] is False,
            "live_capital_binding_disabled": bootstrap_preview["runtime_config"]["live_capital_binding_enabled"] is False,
            "bridge_points_to_pantheon_lean": bootstrap_preview["bridge"]["path"] == ENGINE_BRIDGE_PATH,
            "no_lean_platform_target": "lean-platform" not in bootstrap_preview["bridge"]["path"],
            "no_broker_secrets_included": bootstrap_preview["secrets_included"] is False,
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec",
            "MGMT-PAPER-002: ApprovalDecision packet",
            "MGMT-PAPER-003: DeploymentPlan packet <- this artifact",
            "MGMT-PAPER-004: paper RuntimeBinding packet",
            "MGMT-PAPER-005: telemetry packet",
            "MGMT-PAPER-006: EvolutionDecision review packet",
            "MGMT-PAPER-007: complete OODA packet",
        ],
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_paper_deployment_packet(packet)
    return packet


def write_evidence_packet(packet: Mapping[str, Any], out_path: Path) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(packet)
    out_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3]
    / "support"
    / "evidence"
    / "MGMT-PAPER-003-paper-deployment-plan.json"
)


def main() -> int:
    print("=== MGMT-PAPER-003: paper DeploymentPlan packet ===\n")
    try:
        packet = build_evidence_packet()
    except DeploymentPlanError as exc:
        print(f"FAIL: deployment plan error: {exc}")
        return 1

    errors = packet["validation_errors"]
    if errors:
        print(f"FAIL: validation errors: {errors}")
        return 1

    write_evidence_packet(packet, _EVIDENCE_PATH)
    plan = packet["deployment_plan"]

    print(f"  plan_id       : {plan['plan_id']}")
    print(f"  approval      : {plan['approval_decision_id']}")
    print(f"  artifact      : {plan['artifact_id']}@{plan['artifact_version']}")
    print(f"  stage         : {plan['current_stage']} -> {plan['target_stage']}")
    print(f"  runtime_action: {plan['runtime_action']}")
    print(f"  capital_scale : {plan['scale']['capital_scale_pct']}")
    print(f"  validation    : {'PASS (no errors)' if not errors else 'FAIL'}")
    print(f"\n  evidence packet written to: {_EVIDENCE_PATH}")
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
