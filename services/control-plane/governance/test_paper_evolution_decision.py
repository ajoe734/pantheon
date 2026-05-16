"""
Unit tests for paper_evolution_decision (MGMT-PAPER-006).

Verifies:
- Paper telemetry becomes a low-risk revalidate EvolutionDecision.
- Review/approval authority is backed by an ApprovalDecision.
- Execution is research-plane only and cannot mutate runtime/broker/capital.
- Evidence packet links into OODA decide/learn refs.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from approval_decision import DecisionOutcome, DecisionState, TargetType, validate_decision
from evolution_decision import (
    ExecutionPlane,
    EvolutionActionType,
    EvolutionDecision,
    EvolutionDecisionState,
    ReviewStepType,
    RiskLevel,
    validate_evolution_decision,
)
from paper_evolution_decision import (
    PaperEvolutionDecisionContext,
    build_evidence_packet,
    build_evolution_approval_decision,
    build_paper_evolution_decision,
    validate_evidence_packet,
    write_evidence_packet,
)


def _sample_telemetry_evidence(**overrides):
    packet = {
        "task_id": "MGMT-PAPER-005",
        "environment": "paper",
        "live_capital_side_effects": False,
        "runtime_binding_id": "8d706784-2678-4c49-9b70-e05d89f7b001",
        "telemetry_packet": {
            "packet_id": "telemetry-packet-paper-mgmt-001",
            "event_count": 4,
            "event_ids": [
                "631f17dc-bbba-4593-9ad3-9ed24c5b3001",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3002",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3003",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3004",
            ],
            "events": [
                {
                    "event_id": "631f17dc-bbba-4593-9ad3-9ed24c5b3001",
                    "event_type": "heartbeat",
                    "deployment_stage": "paper",
                    "execution_mode": "paper",
                    "binding_id": "8d706784-2678-4c49-9b70-e05d89f7b001",
                }
            ],
        },
        "ingest_validation": {
            "accepted_event_ids": [
                "631f17dc-bbba-4593-9ad3-9ed24c5b3001",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3002",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3003",
                "631f17dc-bbba-4593-9ad3-9ed24c5b3004",
            ],
            "heartbeat_first_accepted": True,
            "heartbeat_duplicate_accepted": True,
            "stage_mismatch_rejected": True,
            "missing_binding_rejected": True,
        },
        "runtime_summary_projection": {
            "runtime_id": "rt-paper-mgmt-001",
            "runtime_binding_id": "8d706784-2678-4c49-9b70-e05d89f7b001",
            "deployment_stage": "paper",
            "capital_pool_id": "capital-pool-paper-001",
            "deployment_plan_id": "deployment-plan-paper-001",
            "last_heartbeat_at": "2026-05-15T15:05:00Z",
            "state": "degraded",
        },
        "safety_assertions": {
            "paper_stage_only": True,
            "live_broker_telemetry_disabled": True,
            "capital_binding_live_disabled": True,
            "bracket_logged_only": True,
            "no_real_order": True,
            "no_real_capital": True,
        },
    }
    packet.update(overrides)
    return packet


class PaperEvolutionDecisionTests(unittest.TestCase):
    def test_factory_executes_low_risk_revalidation(self):
        decision = build_paper_evolution_decision(
            telemetry_evidence=_sample_telemetry_evidence()
        )

        self.assertEqual(decision.decision_state, EvolutionDecisionState.EXECUTED)
        self.assertEqual(decision.action_type, EvolutionActionType.REVALIDATE)
        self.assertEqual(decision.risk_level, RiskLevel.LOW)
        self.assertEqual(decision.execution_result.plane, ExecutionPlane.RESEARCH)
        self.assertEqual(validate_evolution_decision(decision), [])
        self.assertEqual(decision.cooldown_ends_at, "2026-05-18T15:06:10Z")
        self.assertEqual(decision.observation_window_ends_at, "2026-05-22T15:06:10Z")
        self.assertEqual(decision.metadata["live_mutation_allowed"], False)
        self.assertEqual(decision.metadata["runtime_binding_mutation_allowed"], False)

        steps = [step.step_type for step in decision.review_chain]
        self.assertIn(ReviewStepType.REVIEWED, steps)
        self.assertIn(ReviewStepType.APPROVED, steps)
        self.assertIn(ReviewStepType.EXECUTED, steps)

    def test_approval_decision_backs_evolution_review(self):
        ctx = PaperEvolutionDecisionContext()
        approval = build_evolution_approval_decision(ctx)

        self.assertEqual(approval.target_type, TargetType.EVOLUTION_PROPOSAL)
        self.assertEqual(approval.target_id, ctx.decision_id)
        self.assertEqual(approval.decision_state, DecisionState.DECIDED)
        self.assertEqual(approval.decision, DecisionOutcome.APPROVED)
        self.assertEqual(validate_decision(approval), [])

    def test_evidence_packet_links_telemetry_and_ooda_refs(self):
        packet = build_evidence_packet(
            telemetry_evidence=_sample_telemetry_evidence(),
            generated_at="2026-05-15T15:07:00Z",
        )

        self.assertEqual(packet["validation_errors"], [])
        self.assertFalse(packet["live_capital_side_effects"])
        self.assertEqual(packet["telemetry_review_input"]["deployment_stage"], "paper")
        self.assertEqual(
            packet["ooda_decide_ref"]["evolution_decision_id"],
            packet["evolution_decision"]["decision_id"],
        )
        self.assertTrue(packet["ooda_learn_ref"]["evolution_followthrough_refs"])
        self.assertTrue(all(packet["safety_assertions"].values()))

    def test_packet_mutation_guards(self):
        packet = build_evidence_packet(telemetry_evidence=_sample_telemetry_evidence())

        proposed_mutation = json.loads(json.dumps(packet))
        proposed_mutation["evolution_decision"]["decision_state"] = "proposed"
        errors = validate_evidence_packet(proposed_mutation)
        self.assertTrue(any("must be executed" in error for error in errors), errors)

        runtime_mutation = json.loads(json.dumps(packet))
        runtime_mutation["evolution_decision"]["execution_result"]["plane"] = "runtime"
        errors = validate_evidence_packet(runtime_mutation)
        self.assertTrue(any("plane must be research" in error for error in errors), errors)

        live_stage_mutation = json.loads(json.dumps(packet))
        live_stage_mutation["telemetry_review_input"]["deployment_stage"] = "live"
        errors = validate_evidence_packet(live_stage_mutation)
        self.assertTrue(any("deployment_stage must be paper" in error for error in errors), errors)

        live_mutation_allowed = json.loads(json.dumps(packet))
        live_mutation_allowed["evolution_decision"]["metadata"]["live_mutation_allowed"] = True
        errors = validate_evidence_packet(live_mutation_allowed)
        self.assertTrue(any("live_mutation_allowed" in error for error in errors), errors)

    def test_write_evidence_packet(self):
        packet = build_evidence_packet(
            telemetry_evidence=_sample_telemetry_evidence(),
            generated_at="2026-05-15T15:07:00Z",
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            tmp = Path(handle.name)

        try:
            write_evidence_packet(packet, tmp)
            raw = json.loads(tmp.read_text(encoding="utf-8"))
            self.assertEqual(raw["task_id"], "MGMT-PAPER-006")
            self.assertEqual(raw["validation_errors"], [])
            restored = EvolutionDecision.from_dict(raw["evolution_decision"])
            self.assertEqual(validate_evolution_decision(restored), [])
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
