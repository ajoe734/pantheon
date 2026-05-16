"""Tests for MGMT-EVO-007 evolution OODA loop closure script."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"

for _path in (str(REPO_ROOT), str(OODA_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Import the module under test.
sys.path.insert(0, str(SCRIPTS_DIR))
mod = importlib.import_module("run_evolution_ooda_loop_closure")

EvoOodaClosureContext = mod.EvoOodaClosureContext
build_freeze_evolution_decision = mod.build_freeze_evolution_decision
build_complete_ooda_packet = mod.build_complete_ooda_packet
build_replay_validation = mod.build_replay_validation
build_evidence_packet = mod.build_evidence_packet
validate_evidence_packet = mod.validate_evidence_packet
TASK_ID = mod.TASK_ID
MILESTONE_ID = mod.MILESTONE_ID
PAPER_ENVIRONMENT = mod.PAPER_ENVIRONMENT


class TestEvoOodaClosureContext(unittest.TestCase):
    def test_defaults(self) -> None:
        ctx = EvoOodaClosureContext()
        self.assertEqual(ctx.packet_id, "ooda-evolution-mgmt-007-closure")
        self.assertEqual(ctx.freeze_decision_id, "evo-mgmt-005-freeze-stage")
        self.assertIn("live-mgmt-evo-005", ctx.persona_capital_binding_id)


class TestFreezDecisionBuilder(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = EvoOodaClosureContext()
        self.decision = build_freeze_evolution_decision(self.ctx)

    def test_decision_executed(self) -> None:
        self.assertEqual(self.decision.decision_state, "executed")

    def test_decision_action_freeze(self) -> None:
        self.assertEqual(self.decision.action_type.value, "freeze")

    def test_decision_id_matches_context(self) -> None:
        self.assertEqual(self.decision.decision_id, self.ctx.freeze_decision_id)

    def test_no_live_mutation(self) -> None:
        self.assertIsNotNone(self.decision.metadata)
        self.assertFalse(self.decision.metadata.get("live_mutation_allowed"))
        self.assertFalse(self.decision.metadata.get("broker_order_allowed"))
        self.assertFalse(self.decision.metadata.get("capital_binding_mutation_allowed"))

    def test_ooda_packet_id_in_metadata(self) -> None:
        self.assertEqual(
            self.decision.metadata.get("ooda_packet_id"), self.ctx.packet_id
        )


class TestCompleteOodaPacket(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = EvoOodaClosureContext()
        self.freeze_decision = build_freeze_evolution_decision(self.ctx)
        self.ooda_packet, self.transitions, self.snapshots = build_complete_ooda_packet(
            self.ctx, freeze_decision=self.freeze_decision
        )

    def test_packet_closed(self) -> None:
        self.assertEqual(self.ooda_packet.status.value, "closed")

    def test_loop_type_evolution(self) -> None:
        self.assertEqual(self.ooda_packet.loop_type.value, "evolution")

    def test_environment_paper(self) -> None:
        self.assertEqual(self.ooda_packet.environment.value, "paper")

    def test_no_live_capital_side_effects(self) -> None:
        self.assertFalse(self.ooda_packet.act.live_capital_side_effects)

    def test_observe_bundle_populated(self) -> None:
        self.assertTrue(self.ooda_packet.observe.incident_refs)
        self.assertTrue(self.ooda_packet.observe.telemetry_refs)
        self.assertTrue(self.ooda_packet.observe.source_refs)

    def test_orient_bundle_populated(self) -> None:
        self.assertTrue(self.ooda_packet.orient.evidence_bundle_refs)
        self.assertIsNotNone(self.ooda_packet.orient.risk_adjudication_ref)

    def test_decide_bundle_evolution_decision_set(self) -> None:
        self.assertEqual(
            self.ooda_packet.decide.evolution_decision_id,
            self.ctx.freeze_decision_id,
        )

    def test_act_bundle_rollback_refs(self) -> None:
        self.assertTrue(self.ooda_packet.act.rollback_refs)

    def test_learn_bundle_followthrough_refs(self) -> None:
        self.assertTrue(self.ooda_packet.learn.evolution_followthrough_refs)
        self.assertTrue(self.ooda_packet.learn.postmortem_refs)

    def test_learn_observation_window(self) -> None:
        self.assertIsNotNone(self.ooda_packet.learn.observation_window)

    def test_transitions_count(self) -> None:
        # open -> observing -> oriented -> decided -> acted -> evolving -> closed = 6
        self.assertEqual(len(self.transitions), 6)

    def test_transitions_end_closed(self) -> None:
        self.assertEqual(self.transitions[-1]["to_status"], "closed")

    def test_snapshots_length(self) -> None:
        # initial open + 6 transitions = 7
        self.assertEqual(len(self.snapshots), 7)

    def test_closed_at_set(self) -> None:
        self.assertIsNotNone(self.ooda_packet.closed_at)

    def test_packet_validates(self) -> None:
        from ooda_loop_packet import validate_packet
        errors = validate_packet(self.ooda_packet.to_dict())
        self.assertEqual(errors, [], msg=f"Unexpected packet validation errors: {errors}")


class TestReplayValidation(unittest.TestCase):
    def setUp(self) -> None:
        ctx = EvoOodaClosureContext()
        freeze_decision = build_freeze_evolution_decision(ctx)
        ooda_packet, transitions, snapshots = build_complete_ooda_packet(
            ctx, freeze_decision=freeze_decision
        )
        self.ctx = ctx
        self.ooda_packet = ooda_packet
        self.replay = build_replay_validation(ooda_packet, transitions, snapshots)

    def test_can_replay(self) -> None:
        self.assertTrue(self.replay["can_replay"])

    def test_latest_status_closed(self) -> None:
        self.assertEqual(self.replay["latest_status"], "closed")

    def test_record_count(self) -> None:
        # 1 initial + 6 transition records = 7
        self.assertEqual(self.replay["record_count"], 7)

    def test_stage_transition_count(self) -> None:
        self.assertEqual(self.replay["stage_transition_count"], 6)

    def test_query_evolution_decision(self) -> None:
        self.assertIn(
            self.ctx.packet_id,
            self.replay["query_matches"]["evolution_decision_id"],
        )

    def test_query_runtime_binding(self) -> None:
        self.assertIn(
            self.ctx.packet_id,
            self.replay["query_matches"]["runtime_binding_id"],
        )


class TestEvidencePacket(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = build_evidence_packet()

    def test_no_validation_errors(self) -> None:
        errors = self.packet["validation_errors"]
        self.assertEqual(errors, [], msg=f"Unexpected validation errors: {errors}")

    def test_task_id(self) -> None:
        self.assertEqual(self.packet["task_id"], TASK_ID)

    def test_milestone_id(self) -> None:
        self.assertEqual(self.packet["milestone_id"], MILESTONE_ID)

    def test_environment(self) -> None:
        self.assertEqual(self.packet["environment"], PAPER_ENVIRONMENT)

    def test_no_live_capital_side_effects(self) -> None:
        self.assertFalse(self.packet["live_capital_side_effects"])

    def test_ooda_packet_closed(self) -> None:
        self.assertEqual(self.packet["ooda_packet"]["status"], "closed")

    def test_evolution_chain_references(self) -> None:
        chain = self.packet["evolution_chain"]
        self.assertTrue(any("MGMT-EVO-001" in entry for entry in chain))
        self.assertTrue(any("MGMT-EVO-007" in entry for entry in chain))

    def test_safety_assertions_all_true(self) -> None:
        safety = self.packet["safety_assertions"]
        for key, value in safety.items():
            self.assertTrue(value, msg=f"Safety assertion failed: {key}")

    def test_replay_roundtrip(self) -> None:
        self.assertTrue(self.packet["replay_validation"]["can_replay"])


class TestValidateEvidencePacket(unittest.TestCase):
    def test_valid_packet(self) -> None:
        packet = build_evidence_packet()
        # clear existing errors and re-validate
        errors = validate_evidence_packet(packet)
        self.assertEqual(errors, [])

    def test_wrong_task_id(self) -> None:
        packet = build_evidence_packet()
        bad = dict(packet, task_id="WRONG")
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("task_id" in e for e in errors))

    def test_wrong_milestone_id(self) -> None:
        packet = build_evidence_packet()
        bad = dict(packet, milestone_id="WRONG")
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("milestone_id" in e for e in errors))

    def test_live_capital_side_effects_rejected(self) -> None:
        packet = build_evidence_packet()
        bad = dict(packet, live_capital_side_effects=True)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("live_capital_side_effects" in e for e in errors))

    def test_non_closed_ooda_rejected(self) -> None:
        packet = build_evidence_packet()
        ooda = dict(packet["ooda_packet"], status="acted")
        bad = dict(packet, ooda_packet=ooda)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("closed" in e for e in errors))

    def test_non_evolution_loop_type_rejected(self) -> None:
        packet = build_evidence_packet()
        ooda = dict(packet["ooda_packet"], loop_type="paper_strategy")
        bad = dict(packet, ooda_packet=ooda)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("loop_type" in e for e in errors))

    def test_missing_freeze_decision_rejected(self) -> None:
        packet = build_evidence_packet()
        bad = dict(packet, freeze_decision=None)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("freeze_decision" in e for e in errors))

    def test_non_executed_freeze_decision_rejected(self) -> None:
        packet = build_evidence_packet()
        evo = dict(packet["freeze_decision"], decision_state="proposed")
        bad = dict(packet, freeze_decision=evo)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("executed" in e for e in errors))

    def test_broken_replay_rejected(self) -> None:
        packet = build_evidence_packet()
        bad_replay = dict(packet["replay_validation"], can_replay=False)
        bad = dict(packet, replay_validation=bad_replay)
        errors = validate_evidence_packet(bad)
        self.assertTrue(any("can_replay" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
