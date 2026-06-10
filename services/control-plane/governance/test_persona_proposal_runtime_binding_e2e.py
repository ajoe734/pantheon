"""E2E contract test for governed persona proposals reaching runtime binding.

MPOS-P0-E2E-001 proves that a persona-authored proposal is evidence only.  It
cannot create a RuntimeBinding directly; runtime impact appears only after the
proposal is linked through artifact lineage, ApprovalDecision, DeploymentPlan,
RuntimeBinding, telemetry ingest, and EvolutionDecision review evidence.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


GOVERNANCE_DIR = Path(__file__).parent
PERSONA_DIR = GOVERNANCE_DIR.parent / "persona"
for path in (GOVERNANCE_DIR, PERSONA_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from approval_decision import EvidenceRef, EvidenceRefType, validate_decision  # noqa: E402
from deployment_plan import StagePlanner  # noqa: E402
from evolution_decision import EvolutionDecisionState  # noqa: E402
from paper_approval_decision import (  # noqa: E402
    PaperApprovalContext,
    build_paper_approval_decision,
)
from paper_deployment_plan import (  # noqa: E402
    PaperDeploymentContext,
    build_approved_registry_entry,
    build_paper_deployment_plan,
    build_runtime_bootstrap_preview,
    validate_paper_deployment_packet,
)
from paper_evolution_decision import (  # noqa: E402
    PaperEvolutionDecisionContext,
    build_evidence_packet as build_evolution_packet,
)
from paper_runtime_binding import (  # noqa: E402
    PaperRuntimeBindingContext,
    build_evidence_packet as build_runtime_binding_packet,
    build_paper_runtime_binding,
)
from persona_registry import (  # noqa: E402
    Persona,
    PersonaLifecycleState,
    SessionPersona,
    validate_persona,
    validate_session,
)
from services.telemetry.paper_telemetry_packet import (  # noqa: E402
    PaperTelemetryContext,
    build_evidence_packet as build_telemetry_packet,
)


def _persona() -> Persona:
    return Persona(
        persona_id="persona-mpos-paper-01",
        name="MPOS Paper Sponsor",
        mandate=(
            "Propose paper-stage allocation evidence for governed review; never "
            "write runtime bindings, broker orders, or live capital state directly."
        ),
        lifecycle_state=PersonaLifecycleState.PAPER_OWNER.value,
        created_at="2026-05-15T15:00:00Z",
        strategy_family="qlib-lightgbm-paper",
        route_policy_id="route-policy:proposal-evidence-only",
        consult_policy_id="consult-policy:paper-risk-review-required",
        owner="governance-operator-mpos",
    )


def _proposal() -> dict[str, Any]:
    return {
        "proposal_id": "persona-proposal-mpos-paper-001",
        "proposal_type": "allocation_policy_candidate",
        "persona_id": "persona-mpos-paper-01",
        "capital_pool_id": "capital-pool-paper-001",
        "target_artifact_id": "strategy-spec-mpos-persona-paper-001",
        "target_artifact_version": "1.0.0",
        "target_artifact_type": "strategy_spec",
        "strategy_id": "mpos-persona-paper-alpha",
        "desired_deployment_stage": "paper",
        "created_at": "2026-05-15T15:01:00Z",
        "evidence_refs": [
            {
                "ref_type": EvidenceRefType.MANUAL_REVIEW_TICKET.value,
                "ref_id": "persona-proposal-ticket-mpos-001",
                "storage_ref": {
                    "backend": "inline",
                    "path": "$.persona_proposal",
                },
                "note": "Persona proposal captured as review evidence only.",
            }
        ],
        "lineage": {
            "source_run_ids": ["MPOS-P0-E2E-001"],
            "persona_proposal_ref": "persona-proposal://persona-proposal-mpos-paper-001",
        },
        "runtime_authority": {
            "can_create_runtime_binding": False,
            "can_submit_broker_order": False,
            "can_mutate_capital_state": False,
        },
    }


def _proposal_evidence_ref(proposal: Mapping[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.MANUAL_REVIEW_TICKET,
        ref_id=str(proposal["proposal_id"]),
        storage_ref={"backend": "inline", "path": "$.persona_proposal"},
        note="Persona proposal is evidence for governance approval, not a runtime command.",
    )


def _deployment_context(
    proposal: Mapping[str, Any],
    *,
    approval_decision_id: str,
) -> PaperDeploymentContext:
    return PaperDeploymentContext(
        plan_id="deployment-plan-mpos-persona-paper-001",
        approval_decision_id=approval_decision_id,
        artifact_id=str(proposal["target_artifact_id"]),
        artifact_version=str(proposal["target_artifact_version"]),
        artifact_type=str(proposal["target_artifact_type"]),
        strategy_id=str(proposal["strategy_id"]),
        artifact_checksum="sha256:mpospersona001paperproposalgoverned",
        capital_pool_id=str(proposal["capital_pool_id"]),
        sponsor_persona_id=str(proposal["persona_id"]),
        persona_capital_binding_id="pcb-mpos-persona-paper-001",
        approved_at="2026-05-15T15:03:00Z",
        source_run_ids=["MPOS-P0-E2E-001", str(proposal["proposal_id"])],
    )


def _deployment_packet(
    ctx: PaperDeploymentContext,
    proposal: Mapping[str, Any],
    approval_decision: Mapping[str, Any],
) -> dict[str, Any]:
    registry_entry = build_approved_registry_entry(ctx)
    lineage = dict(registry_entry["lineage"])
    lineage["persona_proposal_id"] = proposal["proposal_id"]
    lineage["persona_id"] = proposal["persona_id"]
    lineage["proposal_evidence_refs"] = [
        ref["ref_id"] for ref in proposal.get("evidence_refs", [])
    ]
    registry_entry["lineage"] = lineage

    plan = build_paper_deployment_plan(
        ctx,
        approval_decision=approval_decision,
        registry_entry=registry_entry,
    )
    projection = StagePlanner().build_execution_projection(plan, registry_entry)
    bootstrap_preview = build_runtime_bootstrap_preview(plan, registry_entry)

    packet: dict[str, Any] = {
        "task_id": "MGMT-PAPER-003",
        "epic": "MPOS-P0 governed persona proposal E2E",
        "environment": "paper",
        "generated_at": "2026-05-15T15:03:30Z",
        "live_capital_side_effects": False,
        "source_artifacts": {
            "persona_proposal": f"persona-proposal://{proposal['proposal_id']}",
            "approval_decision": f"approval-decision://{approval_decision['decision_id']}",
        },
        "approval_decision_ref": dict(approval_decision),
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
            "target_stage": plan.target_stage.value
            if hasattr(plan.target_stage, "value")
            else plan.target_stage,
            "runtime_action": plan.runtime_action.value
            if hasattr(plan.runtime_action, "value")
            else plan.runtime_action,
            "capital_pool_id": plan.capital_pool_id,
            "persona_capital_binding_id": (plan.metadata or {}).get(
                "persona_capital_binding_id"
            ),
        },
        "safety_assertions": {
            "paper_environment": plan.target_stage == "paper"
            or getattr(plan.target_stage, "value", None) == "paper",
            "activate_from_none": (
                (plan.current_stage == "none" or getattr(plan.current_stage, "value", None) == "none")
                and (
                    plan.transition_type == "activate"
                    or getattr(plan.transition_type, "value", None) == "activate"
                )
            ),
            "deploy_new_binding": (
                plan.runtime_action == "deploy_new_binding"
                or getattr(plan.runtime_action, "value", None) == "deploy_new_binding"
            ),
            "zero_live_capital_scale": plan.scale is not None
            and plan.scale.capital_scale_pct == 0.0,
            "gross_scale_is_paper_simulation": plan.scale is not None
            and plan.scale.gross_scale_pct == 100.0,
            "live_broker_disabled": bootstrap_preview["runtime_config"][
                "live_broker_enabled"
            ]
            is False,
            "live_capital_binding_disabled": bootstrap_preview["runtime_config"][
                "live_capital_binding_enabled"
            ]
            is False,
            "proposal_remains_evidence_only": proposal["runtime_authority"][
                "can_create_runtime_binding"
            ]
            is False,
        },
        "paper_loop_chain": [
            "persona proposal evidence",
            "artifact lineage",
            "approval decision",
            "deployment plan",
            "runtime binding",
            "telemetry",
            "evolution review",
        ],
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_paper_deployment_packet(packet)
    return packet


class GovernedPersonaProposalRuntimeBindingE2ETest(unittest.TestCase):
    def test_persona_proposal_cannot_directly_create_runtime_binding(self) -> None:
        persona = _persona()
        proposal = _proposal()

        self.assertEqual(validate_persona(persona), [])
        self.assertFalse(proposal["runtime_authority"]["can_create_runtime_binding"])

        advisory_session = SessionPersona(
            session_id="session-mpos-proposal-001",
            persona_id=persona.persona_id,
            session_type="research_task",
            status="active",
            started_at="2026-05-15T15:01:00Z",
            capability_snapshot_id="caps-mpos-proposal-001",
            trace_id="trace-mpos-proposal-001",
            request_id="request-mpos-proposal-001",
            task_ref="MPOS-P0-E2E-001",
        )
        self.assertEqual(validate_session(advisory_session), [])
        self.assertIsNone(advisory_session.runtime_binding_id)

        interactive_without_binding = SessionPersona(
            session_id="session-mpos-runtime-001",
            persona_id=persona.persona_id,
            session_type="interactive",
            status="active",
            started_at="2026-05-15T15:02:00Z",
            capability_snapshot_id="caps-mpos-proposal-001",
            trace_id="trace-mpos-runtime-001",
            request_id="request-mpos-runtime-001",
            task_ref="MPOS-P0-E2E-001",
        )
        self.assertTrue(
            any("runtime_binding_id" in error for error in validate_session(interactive_without_binding))
        )

        with self.assertRaisesRegex(ValueError, "deployment_plan must be present"):
            build_paper_runtime_binding(deployment_packet=proposal)

    def test_proposal_reaches_runtime_only_after_governed_chain(self) -> None:
        proposal = _proposal()
        approval_ctx = PaperApprovalContext(
            decision_id="approval-mpos-persona-paper-001",
            strategy_spec_id=str(proposal["target_artifact_id"]),
            strategy_version=str(proposal["target_artifact_version"]),
            reviewer_actor_id="governance-reviewer-mpos-01",
            capital_pool_id=str(proposal["capital_pool_id"]),
            persona_id=str(proposal["persona_id"]),
            evidence_refs=[_proposal_evidence_ref(proposal)],
        )
        approval = build_paper_approval_decision(approval_ctx)
        self.assertEqual(validate_decision(approval), [])
        self.assertEqual(approval.target_id, proposal["target_artifact_id"])
        self.assertEqual(approval.evidence_refs[0].ref_id, proposal["proposal_id"])

        deployment_ctx = _deployment_context(
            proposal,
            approval_decision_id=approval.decision_id,
        )
        deployment_packet = _deployment_packet(
            deployment_ctx,
            proposal,
            approval.to_dict(),
        )
        self.assertEqual(deployment_packet["validation_errors"], [])
        self.assertEqual(
            deployment_packet["approved_registry_entry"]["lineage"]["persona_proposal_id"],
            proposal["proposal_id"],
        )

        runtime_packet = build_runtime_binding_packet(
            PaperRuntimeBindingContext(
                binding_id="runtime-binding-mpos-persona-paper-001",
                runtime_id="lean-paper-runtime-mpos-persona-001",
                effective_at="2026-05-15T15:04:00Z",
                request_id="runtime-bootstrap-mpos-persona-001",
                trace_id="03829d25-2c9f-44f0-981e-56a94d8ff901",
            ),
            deployment_packet=deployment_packet,
            generated_at="2026-05-15T15:04:30Z",
        )
        self.assertEqual(runtime_packet["validation_errors"], [])
        binding = runtime_packet["runtime_binding"]
        self.assertEqual(binding["plan_id"], deployment_ctx.plan_id)
        self.assertEqual(binding["persona_capital_binding_id"], deployment_ctx.persona_capital_binding_id)
        self.assertEqual(binding["metadata"]["write_owner"], "runtime-manager")
        self.assertFalse(binding["metadata"]["live_capital_side_effects"])

        telemetry_packet = build_telemetry_packet(
            PaperTelemetryContext(
                binding_id=binding["binding_id"],
                runtime_id=binding["runtime_id"],
                capital_pool_id=binding["capital_pool_id"],
                artifact_id=binding["artifact_id"],
                artifact_version=binding["artifact_version"],
                plan_id=binding["plan_id"],
                persona_capital_binding_id=binding["persona_capital_binding_id"],
                strategy_id=str(proposal["target_artifact_id"]),
                deployment_stage="paper",
                effective_at=binding["effective_at"],
            ),
            generated_at="2026-05-15T15:05:30Z",
        )
        self.assertEqual(telemetry_packet["validation_errors"], [])
        self.assertEqual(
            telemetry_packet["runtime_summary_projection"]["runtime_binding_id"],
            binding["binding_id"],
        )
        self.assertTrue(telemetry_packet["safety_assertions"]["bracket_logged_only"])

        evolution_packet = build_evolution_packet(
            PaperEvolutionDecisionContext(
                decision_id="evolution-mpos-persona-paper-001",
                approval_decision_id="approval-evolution-mpos-persona-paper-001",
                strategy_spec_id=str(proposal["target_artifact_id"]),
                strategy_version=str(proposal["target_artifact_version"]),
                capital_pool_id=str(proposal["capital_pool_id"]),
                sponsor_persona_id=str(proposal["persona_id"]),
                source_ooda_packet_id="ooda-mpos-persona-paper-001",
                execution_ref_id="revalidate-job-mpos-persona-paper-001",
                telemetry_packet_path="inline://mpos-persona-paper-telemetry",
            ),
            telemetry_evidence=telemetry_packet,
            generated_at="2026-05-15T15:07:30Z",
        )
        self.assertEqual(evolution_packet["validation_errors"], [])
        evolution = evolution_packet["evolution_decision"]
        self.assertEqual(evolution["decision_state"], EvolutionDecisionState.EXECUTED.value)
        self.assertEqual(evolution["metadata"]["source_runtime_binding_id"], binding["binding_id"])
        self.assertFalse(evolution["metadata"]["runtime_binding_mutation_allowed"])
        self.assertFalse(evolution["metadata"]["live_mutation_allowed"])
        self.assertEqual(evolution["execution_result"]["plane"], "research")


if __name__ == "__main__":
    unittest.main()
