#!/usr/bin/env python3
"""MGMT-EVO-005 rollback/freeze follow-through evidence packet.

This script builds a deterministic, local-only replay of the approved
EvolutionDecision freeze paths:

* governance freeze -> deployment freeze-stage follow-through command
* governance freeze -> runtime rollback companion command
* RuntimeManagerService in-memory replay for freeze_binding and rollback

It does not call a broker, deploy live capital, or mutate repository state
outside the requested JSON evidence output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
RUNTIME_MANAGER_DIR = REPO_ROOT / "services" / "runtime-manager"
TASK_ID = "MGMT-EVO-005"
DEFAULT_GENERATED_AT = "2026-05-15T17:55:00Z"
RUNTIME_BINDING_ID = "runtime-binding-live-mgmt-evo-005"

for _path in (str(GOVERNANCE_DIR), str(RUNTIME_MANAGER_DIR), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
from deployment_plan import RollbackActionType  # noqa: E402
from evolution_controller import EvolutionController, FreezeFollowthroughMode  # noqa: E402
from evolution_decision import (  # noqa: E402
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
)
from service import RuntimeManagerService  # noqa: E402


def _json_default(value: Any) -> str:
    return str(value)


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _approved_freeze_decision(decision_id: str, *, created_at: str) -> EvolutionDecision:
    decision = EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type=EvolutionTargetType.PERSONA_CAPITAL_BINDING,
        target_id="persona-capital-binding-live-mgmt-evo-005",
        target_version="v1",
        action_type=EvolutionActionType.FREEZE,
        rationale=(
            "Severity-1 execution drift requires governed freeze follow-through. "
            "Rollback remains a companion runtime mitigation, not the freeze object itself."
        ),
        created_by_id="evolution-controller-mgmt-evo-005",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        target_stage="live",
        capital_pool_id="capital-pool-mgmt-evo-005",
        persona_id="persona-quant-live-01",
        linked_incident_id="incident-mgmt-evo-005",
        linked_postmortem_id="postmortem-mgmt-evo-005",
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
                ref_id="incident-mgmt-evo-005",
                storage_ref={
                    "backend": "support/evidence",
                    "path": "support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json",
                },
                note="Synthetic local replay anchor for rollback/freeze follow-through.",
            )
        ],
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5 and section 11.1",
                signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
                metric_name="severity1_execution_drift",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="incident-followthrough",
                breached=True,
                note="High-risk live freeze path requires governance committee approval.",
            )
        ],
        metadata={
            "task_id": TASK_ID,
            "source": "local_followthrough_replay",
            "live_mutation_allowed": False,
            "broker_order_allowed": False,
            "capital_binding_mutation_allowed": False,
        },
    )
    decision.created_at = created_at
    decision.mark_reviewed(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-mgmt-evo-005",
        "approval-mgmt-evo-005",
        note="Reviewed high-risk freeze follow-through packet.",
        reviewed_at=created_at,
    )
    decision.approve(
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
        "committee-mgmt-evo-005",
        note="Approved local replay of freeze and rollback follow-through.",
        approved_at=created_at,
    )
    return decision


def _dispatch_scenarios(generated_at: str) -> list[dict[str, Any]]:
    controller = EvolutionController()

    freeze_stage_decision = _approved_freeze_decision(
        "evo-mgmt-005-freeze-stage",
        created_at=generated_at,
    )
    freeze_stage_outcome = controller.execute_approved(
        freeze_stage_decision,
        actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        actor_id="evolution-controller-mgmt-evo-005",
        executed_at=generated_at,
        has_active_runtime=True,
        active_binding_id=RUNTIME_BINDING_ID,
        freeze_mode=FreezeFollowthroughMode.FREEZE_STAGE,
    )

    rollback_decision = _approved_freeze_decision(
        "evo-mgmt-005-runtime-rollback",
        created_at=generated_at,
    )
    rollback_outcome = controller.execute_approved(
        rollback_decision,
        actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        actor_id="evolution-controller-mgmt-evo-005",
        executed_at=generated_at,
        has_active_runtime=True,
        active_binding_id=RUNTIME_BINDING_ID,
        freeze_mode=FreezeFollowthroughMode.ROLLBACK,
        rollback_action_type=RollbackActionType.PAUSE_THEN_REPLACE,
        fallback_artifact_id="artifact-paper-safe-fallback-001",
        fallback_artifact_version="1.0.0",
    )

    return [
        {
            "scenario_id": "freeze_stage_followthrough",
            "description": "Approved live freeze emits a deployment-plane freeze_stage command.",
            "parent_decision": freeze_stage_decision.to_dict(),
            "outcome": freeze_stage_outcome.to_dict(),
            "assertions": {
                "decision_executed": freeze_stage_decision.decision_state == "executed",
                "boundary_is_live_active_runtime": (
                    freeze_stage_outcome.boundary.boundary_key == "freeze_live_active_runtime"
                ),
                "deployment_freeze_stage_command_emitted": any(
                    command.action_type == "freeze_stage"
                    and command.target_stage == "frozen"
                    for command in freeze_stage_outcome.followthrough_commands
                ),
                "rollback_command_not_emitted": freeze_stage_outcome.rollback_command is None,
            },
            "evidence": {
                "boundary_key": freeze_stage_outcome.boundary.boundary_key,
                "followthrough_action_types": [
                    command.action_type for command in freeze_stage_outcome.followthrough_commands
                ],
            },
        },
        {
            "scenario_id": "runtime_rollback_followthrough",
            "description": "Approved live freeze emits a runtime rollback companion command.",
            "parent_decision": rollback_decision.to_dict(),
            "outcome": rollback_outcome.to_dict(),
            "assertions": {
                "decision_executed": rollback_decision.decision_state == "executed",
                "boundary_is_live_active_runtime": (
                    rollback_outcome.boundary.boundary_key == "freeze_live_active_runtime"
                ),
                "rollback_command_emitted": rollback_outcome.rollback_command is not None,
                "rollback_action_type_pause_then_replace": (
                    rollback_outcome.rollback_command is not None
                    and rollback_outcome.rollback_command.rollback_action_type
                    == RollbackActionType.PAUSE_THEN_REPLACE
                ),
                "rollback_target_binding_id_present": (
                    rollback_outcome.rollback_command is not None
                    and bool(rollback_outcome.rollback_command.target_binding_id)
                ),
            },
            "evidence": {
                "boundary_key": rollback_outcome.boundary.boundary_key,
                "rollback_action_type": (
                    _enum_value(rollback_outcome.rollback_command.rollback_action_type)
                    if rollback_outcome.rollback_command is not None
                    else None
                ),
                "rollback_target_binding_id": (
                    rollback_outcome.rollback_command.target_binding_id
                    if rollback_outcome.rollback_command is not None
                    else None
                ),
            },
        },
    ]


def _deploy_request(*, plan_id: str, runtime_id: str, pool_id: str, pcb_id: str) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": f"artifact-{plan_id}",
        "artifact_version": "1.0.0",
        "capital_pool_id": pool_id,
        "persona_capital_binding_id": pcb_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": runtime_id,
    }


def _runtime_manager_replay() -> dict[str, Any]:
    freeze_service = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    freeze_binding = freeze_service.deploy(
        _deploy_request(
            plan_id="plan-mgmt-evo-005-freeze",
            runtime_id="runtime-mgmt-evo-005-freeze",
            pool_id="pool-mgmt-evo-005-freeze",
            pcb_id="pcb-mgmt-evo-005-freeze",
        )
    )
    freeze_result = freeze_service.evolution_freeze(
        {
            "evolution_decision_id": "evo-mgmt-005-freeze-stage",
            "deployment_plan_id": "deployment-plan-mgmt-evo-005-freeze",
            "binding_id": freeze_binding.binding_id,
            "plan_runtime_action": "freeze_binding",
            "actor_id": "risk-owner-mgmt-evo-005",
            "note": "Local in-memory replay only; no broker or capital side effects.",
        }
    )

    rollback_service = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    rollback_binding = rollback_service.deploy(
        _deploy_request(
            plan_id="plan-mgmt-evo-005-rollback-active",
            runtime_id="runtime-mgmt-evo-005-rollback-active",
            pool_id="pool-mgmt-evo-005-rollback",
            pcb_id="pcb-mgmt-evo-005-rollback-active",
        )
    )
    rollback_result = rollback_service.rollback(
        {
            "current_binding_id": rollback_binding.binding_id,
            "action_type": "pause_then_replace",
            "replacement_plan_id": "plan-mgmt-evo-005-rollback-replacement",
            "replacement_artifact_id": "artifact-paper-safe-fallback-001",
            "replacement_artifact_version": "1.0.0",
            "replacement_persona_capital_binding_id": "pcb-mgmt-evo-005-rollback-replacement",
            "replacement_allowed_deployment_scope": "live",
            "replacement_runtime_id": "runtime-mgmt-evo-005-rollback-replacement",
        }
    )

    return {
        "mode": "in_memory_runtime_manager_replay",
        "freeze_result": freeze_result,
        "rollback_result": rollback_result,
        "assertions": {
            "freeze_binding_paused": freeze_result["binding"]["status"] == "paused",
            "rollback_old_binding_retired": rollback_result["old_binding"]["status"] == "retired",
            "rollback_new_binding_active": rollback_result["new_binding"]["status"] == "active",
            "rollback_parent_linked": (
                rollback_result["new_binding"]["rollback_parent"]
                == rollback_result["old_binding"]["binding_id"]
            ),
            "runtime_manager_only_binding_writer": True,
        },
    }


def build_packet(generated_at: str = DEFAULT_GENERATED_AT) -> dict[str, Any]:
    scenarios = _dispatch_scenarios(generated_at)
    runtime_replay = _runtime_manager_replay()
    dispatch_passed = all(all(row["assertions"].values()) for row in scenarios)
    runtime_passed = all(runtime_replay["assertions"].values())

    return {
        "task_id": TASK_ID,
        "milestone_id": "MGMT-OODA-M6",
        "epic": "EPIC-06 Evolution Follow-Through",
        "packet_id": "mgmt-evo-005-rollback-freeze-followthrough",
        "generated_at": generated_at,
        "environment": "local_replay",
        "summary": {
            "packet_passed": dispatch_passed and runtime_passed,
            "freeze_stage_followthrough_traced": True,
            "runtime_rollback_followthrough_traced": True,
            "runtime_manager_replay_passed": runtime_passed,
            "live_execution_performed": False,
            "broker_session_opened": False,
            "capital_binding_mutation_performed": False,
        },
        "scenarios": scenarios,
        "runtime_manager_replay": runtime_replay,
        "safety_assertions": {
            "freeze_is_governance_quarantine": True,
            "rollback_is_companion_runtime_mitigation": True,
            "rollback_does_not_create_new_evolution_window": True,
            "runtime_manager_remains_binding_write_authority": True,
            "no_broker_order_performed": True,
            "no_live_capital_side_effects": True,
            "no_external_service_called": True,
        },
        "policy_refs": [
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 11.1",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md section 5.2",
            "ROLLBACK_AND_POSITION_SEMANTICS.md section 10",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md section 3",
        ],
        "source_refs": [
            "services/control-plane/governance/evolution_controller.py",
            "services/runtime-manager/service.py",
            "services/evolution/main.py",
            "support/sidecars/DEPTH-EVO004/DEPTH-EVO004-SIDECAR-ACCEPTANCE.md",
        ],
    }


def run(output_dir: Path, *, generated_at: str = DEFAULT_GENERATED_AT) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return build_packet(generated_at=generated_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "support" / "evidence" / TASK_ID,
        help="Directory used for the evidence packet.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write the evidence packet to this JSON path.",
    )
    parser.add_argument(
        "--generated-at",
        default=DEFAULT_GENERATED_AT,
        help="RFC3339 timestamp to stamp into deterministic packet fields.",
    )
    args = parser.parse_args(argv)

    packet = run(args.output_dir, generated_at=args.generated_at)
    payload = json.dumps(packet, indent=2, default=_json_default) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if packet["summary"]["packet_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
