"""
Unit tests for OODA packet stage transition validation.

Run:
    python3 -m unittest discover -s services/control-plane/ooda -p 'test_*.py'
"""
from __future__ import annotations

import unittest

from stage_transition import (
    OodaStageEvent,
    OodaStatus,
    OodaTransitionError,
    apply_stage_event,
    assert_complete_replay_path,
    assert_packet_stage_invariants,
    transition_for_event,
    validate_packet_stage_invariants,
    validate_status_transition,
)
from ooda_loop_packet import (
    OodaLoopPacket,
    OodaLoopStore,
    LoopEnvironment,
    LoopStatus,
    LoopType,
)


class TestOodaStatusTransitions(unittest.TestCase):
    def test_canonical_stage_order(self):
        transitions = [
            validate_status_transition(OodaStatus.OPEN, OodaStatus.OBSERVING),
            validate_status_transition(OodaStatus.OBSERVING, OodaStatus.ORIENTED),
            validate_status_transition(OodaStatus.ORIENTED, OodaStatus.DECIDED),
            validate_status_transition(OodaStatus.DECIDED, OodaStatus.ACTED),
            validate_status_transition(OodaStatus.ACTED, OodaStatus.EVOLVING),
            validate_status_transition(OodaStatus.EVOLVING, OodaStatus.CLOSED),
        ]

        self.assertEqual([item.to_status.value for item in transitions], [
            "observing",
            "oriented",
            "decided",
            "acted",
            "evolving",
            "closed",
        ])

    def test_skipped_stage_is_rejected(self):
        with self.assertRaisesRegex(OodaTransitionError, "Forbidden OODA status transition: open -> decided"):
            validate_status_transition("open", "decided")

    def test_regression_is_rejected(self):
        with self.assertRaisesRegex(OodaTransitionError, "Forbidden OODA status transition: decided -> oriented"):
            validate_status_transition("decided", "oriented")

    def test_terminal_status_rejects_transitions(self):
        with self.assertRaisesRegex(OodaTransitionError, "terminal at closed"):
            validate_status_transition("closed", "observing")

        with self.assertRaisesRegex(OodaTransitionError, "terminal at failed"):
            transition_for_event("failed", "observe")

    def test_fail_event_is_allowed_before_terminal(self):
        transition = transition_for_event("oriented", OodaStageEvent.FAIL)

        self.assertEqual(transition.from_status, OodaStatus.ORIENTED)
        self.assertEqual(transition.to_status, OodaStatus.FAILED)
        self.assertTrue(transition.advanced)


class TestOodaStageEvents(unittest.TestCase):
    def test_event_sequence_advances_status(self):
        status = "open"
        for event, expected in [
            ("observe", "observing"),
            ("orient", "oriented"),
            ("decide", "decided"),
            ("act", "acted"),
            ("learn", "evolving"),
            ("close", "closed"),
        ]:
            transition = transition_for_event(status, event)
            self.assertEqual(transition.to_status.value, expected)
            self.assertTrue(transition.advanced)
            status = expected

    def test_close_can_follow_act_without_learn_stage(self):
        transition = transition_for_event("acted", "close")

        self.assertEqual(transition.from_status, OodaStatus.ACTED)
        self.assertEqual(transition.to_status, OodaStatus.CLOSED)
        self.assertTrue(transition.advanced)

    def test_same_stage_data_append_event_is_idempotent(self):
        transition = transition_for_event("observing", "observe")

        self.assertEqual(transition.to_status, OodaStatus.OBSERVING)
        self.assertFalse(transition.advanced)

    def test_wrong_event_for_current_stage_is_rejected(self):
        with self.assertRaisesRegex(OodaTransitionError, "Forbidden OODA status transition: observing -> acted"):
            transition_for_event("observing", "act")

    def test_unknown_status_has_helpful_message(self):
        with self.assertRaisesRegex(OodaTransitionError, "Unknown OODA status 'paused'"):
            transition_for_event("paused", "observe")


class TestOodaPacketHelpers(unittest.TestCase):
    def test_apply_stage_event_updates_known_packet_fields(self):
        packet = {
            "packet_id": "ooda-001",
            "status": "open",
            "environment": "paper",
            "updated_at": "2026-05-15T00:00:00Z",
            "closed_at": None,
            "act": {"live_capital_side_effects": False},
        }

        transition = apply_stage_event(packet, "observe", transitioned_at="2026-05-15T14:00:00Z")

        self.assertEqual(transition.to_status, OodaStatus.OBSERVING)
        self.assertEqual(packet["status"], "observing")
        self.assertEqual(packet["updated_at"], "2026-05-15T14:00:00Z")
        self.assertIsNone(packet["closed_at"])

    def test_apply_close_sets_closed_at_once(self):
        packet = {
            "status": "evolving",
            "updated_at": "2026-05-15T14:00:00Z",
            "closed_at": None,
        }

        apply_stage_event(packet, "close", transitioned_at="2026-05-15T14:10:00Z")

        self.assertEqual(packet["status"], "closed")
        self.assertEqual(packet["updated_at"], "2026-05-15T14:10:00Z")
        self.assertEqual(packet["closed_at"], "2026-05-15T14:10:00Z")

    def test_non_live_environment_rejects_live_side_effects(self):
        packet = {
            "status": "acted",
            "environment": "canary",
            "act": {"live_capital_side_effects": True},
        }

        errors = validate_packet_stage_invariants(packet)

        self.assertIn(
            "act.live_capital_side_effects must be false in dev, paper, sandbox, and canary environments",
            errors,
        )

    def test_closed_packet_requires_closed_at(self):
        with self.assertRaisesRegex(OodaTransitionError, "closed_at"):
            assert_packet_stage_invariants({"status": "closed", "environment": "paper"})

    def test_complete_replay_path_accepts_closed_sequence(self):
        assert_complete_replay_path([
            {"to_status": "observing"},
            {"to_status": "oriented"},
            {"to_status": "decided"},
            {"to_status": "acted"},
            {"to_status": "evolving"},
            {"to_status": "closed"},
        ])

    def test_complete_replay_path_accepts_act_to_close_short_path(self):
        assert_complete_replay_path([
            {"to_status": "observing"},
            {"to_status": "oriented"},
            {"to_status": "decided"},
            {"to_status": "acted"},
            {"to_status": "closed"},
        ])

    def test_incomplete_replay_path_is_rejected(self):
        with self.assertRaisesRegex(OodaTransitionError, "must end at closed"):
            assert_complete_replay_path([
                {"to_status": "observing"},
                {"to_status": "oriented"},
            ])


class TestOodaLoopPacketIntegration(unittest.TestCase):
    def test_packet_advance_uses_stage_validator(self):
        packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
            strategy_id="strategy-rs-003",
        )

        with self.assertRaisesRegex(ValueError, "Forbidden OODA status transition: open -> oriented"):
            packet.advance(LoopStatus.ORIENTED)

    def test_packet_advance_closes_with_timestamp(self):
        packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
        )
        for status in [LoopStatus.OBSERVING, LoopStatus.ORIENTED, LoopStatus.DECIDED, LoopStatus.ACTED]:
            packet.advance(status)

        # Populate minimal evidence to satisfy closed-packet bundle invariants
        packet.observe.source_refs.append("source://note-001")
        packet.orient.regime_state_ref = "regime-bull-2026q2"
        packet.decide.approval_decision_id = "approval-001"
        packet.act.runtime_binding_id = "runtime-paper-001"

        packet.advance(LoopStatus.CLOSED)

        self.assertEqual(packet.status, LoopStatus.CLOSED)
        self.assertIsNotNone(packet.closed_at)
        self.assertEqual(packet.validate(), [])

    def test_packet_validation_uses_non_live_side_effect_guard(self):
        packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.CANARY,
        )
        packet.act.live_capital_side_effects = True

        self.assertIn(
            "act.live_capital_side_effects must be false in dev, paper, sandbox, and canary environments",
            packet.validate(),
        )

    def test_closed_packet_without_stage_evidence_fails_validation(self):
        packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
        )
        for status in [LoopStatus.OBSERVING, LoopStatus.ORIENTED, LoopStatus.DECIDED, LoopStatus.ACTED, LoopStatus.CLOSED]:
            packet.advance(status)

        errors = packet.validate()
        self.assertTrue(any("observe bundle" in e for e in errors), f"Expected observe bundle error, got: {errors}")
        self.assertTrue(any("orient bundle" in e for e in errors), f"Expected orient bundle error, got: {errors}")
        self.assertTrue(any("decide bundle" in e for e in errors), f"Expected decide bundle error, got: {errors}")
        self.assertTrue(any("act bundle" in e for e in errors), f"Expected act bundle error, got: {errors}")

    def test_json_schema_rejects_non_live_packet_with_live_side_effects(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        import json
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent / "ooda_loop_packet.schema.json"
        schema = json.loads(schema_path.read_text())

        invalid_packet = {
            "packet_id": "ooda-schema-test-001",
            "loop_type": "paper_strategy",
            "status": "acted",
            "environment": "paper",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T00:00:00Z",
            "act": {
                "live_capital_side_effects": True,
                "runtime_binding_id": "rb-001",
            },
        }

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid_packet, schema)

    def test_json_schema_accepts_live_packet_with_live_side_effects(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        import json
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent / "ooda_loop_packet.schema.json"
        schema = json.loads(schema_path.read_text())

        valid_packet = {
            "packet_id": "ooda-schema-test-002",
            "loop_type": "paper_strategy",
            "status": "acted",
            "environment": "live",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T00:00:00Z",
            "act": {
                "live_capital_side_effects": True,
                "runtime_binding_id": "rb-live-001",
            },
        }

        jsonschema.validate(valid_packet, schema)

    def test_json_schema_rejects_closed_packet_with_empty_bundle_evidence(self):
        """Regression: closed packet with all empty bundles must be rejected by JSON Schema."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        import json
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent / "ooda_loop_packet.schema.json"
        schema = json.loads(schema_path.read_text())

        closed_empty_packet = {
            "packet_id": "ooda-schema-test-closed-empty",
            "loop_type": "paper_strategy",
            "status": "closed",
            "environment": "paper",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T01:00:00Z",
            "closed_at": "2026-05-15T01:00:00Z",
            "observe": {
                "source_refs": [],
                "telemetry_refs": [],
                "signal_refs": [],
                "market_refs": [],
                "incident_refs": [],
                "human_feedback_refs": [],
            },
            "orient": {
                "regime_state_ref": None,
                "universe_selection_ref": None,
                "signal_inference_refs": [],
                "allocation_proposal_refs": [],
                "risk_adjudication_ref": None,
                "persona_proposal_refs": [],
                "evidence_bundle_refs": [],
            },
            "decide": {
                "approval_decision_id": None,
                "deployment_plan_id": None,
                "evolution_decision_id": None,
                "sponsor_persona_id": None,
                "decision_rationale_ref": None,
                "policy_decision_refs": [],
            },
            "act": {
                "runtime_binding_id": None,
                "command_receipt_refs": [],
                "broker_evidence_refs": [],
                "rollback_refs": [],
                "safe_mode_refs": [],
                "live_capital_side_effects": False,
            },
        }

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(closed_empty_packet, schema)

    def test_json_schema_rejects_closed_act_bundle_with_live_capital_side_effects_only(self):
        """Regression: live_capital_side_effects alone must not count as act bundle evidence."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        import json
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent / "ooda_loop_packet.schema.json"
        schema = json.loads(schema_path.read_text())

        packet = {
            "packet_id": "ooda-schema-test-lcs-only",
            "loop_type": "paper_strategy",
            "status": "closed",
            "environment": "live",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T01:00:00Z",
            "closed_at": "2026-05-15T01:00:00Z",
            "observe": {"source_refs": ["obs-ref-001"]},
            "orient": {"regime_state_ref": "regime-bull-001"},
            "decide": {"approval_decision_id": "approval-001"},
            "act": {
                "runtime_binding_id": None,
                "command_receipt_refs": [],
                "broker_evidence_refs": [],
                "rollback_refs": [],
                "safe_mode_refs": [],
                "live_capital_side_effects": True,
            },
        }

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(packet, schema)

    def test_json_schema_accepts_closed_packet_with_valid_bundle_evidence(self):
        """Closed packet with proper evidence in all four bundles must pass JSON Schema."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        import json
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent / "ooda_loop_packet.schema.json"
        schema = json.loads(schema_path.read_text())

        valid_closed_packet = {
            "packet_id": "ooda-schema-test-closed-valid",
            "loop_type": "paper_strategy",
            "status": "closed",
            "environment": "paper",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T01:00:00Z",
            "closed_at": "2026-05-15T01:00:00Z",
            "observe": {"source_refs": ["strategy-spec://test-001@1.0.0"]},
            "orient": {"regime_state_ref": "regime-neutral-001"},
            "decide": {"approval_decision_id": "approval-test-001"},
            "act": {"runtime_binding_id": "runtime-paper-test-001"},
        }

        jsonschema.validate(valid_closed_packet, schema)

    def test_python_validation_rejects_act_bundle_with_live_capital_side_effects_only(self):
        """Python validate() must reject closed act bundle where only live_capital_side_effects is set."""
        packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.LIVE,
        )
        for status in [LoopStatus.OBSERVING, LoopStatus.ORIENTED, LoopStatus.DECIDED, LoopStatus.ACTED]:
            packet.advance(status)

        packet.observe.source_refs.append("obs-ref-001")
        packet.orient.regime_state_ref = "regime-bull-001"
        packet.decide.approval_decision_id = "approval-001"
        packet.act.live_capital_side_effects = True

        packet.advance(LoopStatus.CLOSED)

        errors = packet.validate()
        self.assertTrue(
            any("act bundle" in e for e in errors),
            f"expected act bundle evidence error, got: {errors}",
        )


class TestOodaLoopStore(unittest.TestCase):
    def test_list_string_filter_matches_enum_created_packets(self):
        store = OodaLoopStore()
        paper_packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
        )
        evo_packet = OodaLoopPacket.create(
            loop_type=LoopType.EVOLUTION,
            environment=LoopEnvironment.LIVE,
        )
        store._packets[paper_packet.packet_id] = paper_packet
        store._packets[evo_packet.packet_id] = evo_packet

        # Raw string filter must match enum-stored packet
        result = store.list(loop_type="paper_strategy")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].packet_id, paper_packet.packet_id)

        # Enum filter must also work
        result = store.list(loop_type=LoopType.PAPER_STRATEGY)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].packet_id, paper_packet.packet_id)

    def test_list_status_string_filter_matches_enum_created_packets(self):
        store = OodaLoopStore()
        open_packet = OodaLoopPacket.create(
            loop_type=LoopType.REBALANCE,
            environment=LoopEnvironment.DEV,
        )
        store._packets[open_packet.packet_id] = open_packet

        result = store.list(status="open")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].packet_id, open_packet.packet_id)

        result = store.list(status="evolving")
        self.assertEqual(result, [])

    def test_list_environment_string_filter_matches_enum_created_packets(self):
        store = OodaLoopStore()
        paper_packet = OodaLoopPacket.create(
            loop_type=LoopType.PAPER_STRATEGY,
            environment=LoopEnvironment.PAPER,
        )
        store._packets[paper_packet.packet_id] = paper_packet

        result = store.list(environment="paper")
        self.assertEqual(len(result), 1)

        result = store.list(environment="live")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
