"""Unit tests for PreferenceExample and CorrectionTrace contracts (IMT-002)."""

from __future__ import annotations

import unittest

from jsonschema import Draft7Validator

from preference_models import (
    CorrectionTrace,
    GovernedTrainingTarget,
    PreferenceExample,
    PreferenceValidationError,
    load_correction_trace_schema,
    load_preference_example_schema,
    validate_correction_trace_payload,
    validate_preference_example_against_correction_trace,
    validate_preference_example_payload,
)


_TARGET = {
    "registry_id": "reg-alpha-v2",
    "strategy_id": "alpha-mean-reversion",
    "artifact_version": "2.0.0",
    "artifact_type": "strategy_spec",
    "promotion_state": "candidate",
}

_BEFORE_ARTIFACT = {
    "artifact_id": "reg-alpha-v2",
    "risk_limit_bps": 120,
    "rebalance_cadence": "1d",
}

_AFTER_ARTIFACT = {
    "artifact_id": "reg-alpha-v2:edited",
    "risk_limit_bps": 80,
    "rebalance_cadence": "1d",
}


def _trace_payload(**overrides: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "trace_id": "corr-trace-001",
        "strategy_id": "alpha-mean-reversion",
        "source_feedback_event_id": "fb-002-001",
        "actor_id": "operator-01",
        "actor_role": "operator",
        "target": _TARGET,
        "before_artifact": _BEFORE_ARTIFACT,
        "after_artifact": _AFTER_ARTIFACT,
        "operations": [
            {
                "path": "/risk_limit_bps",
                "operation": "replace",
                "old_value": 120,
                "new_value": 80,
                "rationale": "Cap the strategy risk before behavior cloning.",
            }
        ],
        "rationale": "Tighter risk limit is the corrected trader instruction.",
        "task_ref": "IMT-002",
        "decision_ref": "approval-dec-001",
        "created_at": "2026-05-16T09:00:00Z",
        "metadata": {
            "research_only": True,
        },
    }
    payload.update(overrides)
    return payload


def _preference_payload(**overrides: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "example_id": "pref-ex-001",
        "strategy_id": "alpha-mean-reversion",
        "source_feedback_event_id": "fb-002-001",
        "correction_trace_id": "corr-trace-001",
        "actor_id": "operator-01",
        "actor_role": "operator",
        "action": "edit",
        "target": _TARGET,
        "chosen_artifact": _AFTER_ARTIFACT,
        "rejected_artifact": _BEFORE_ARTIFACT,
        "confidence": 0.82,
        "rationale": "The edited strategy is preferred because the risk limit is lower.",
        "observed_at": "2026-05-16T09:00:00Z",
        "created_at": "2026-05-16T09:01:00Z",
        "context": {
            "source_channel": "console",
        },
        "metadata": {
            "research_only": True,
        },
    }
    payload.update(overrides)
    return payload


class TestPreferenceCorrectionSchemas(unittest.TestCase):
    def test_schemas_are_valid_draft7(self) -> None:
        Draft7Validator.check_schema(load_preference_example_schema())
        Draft7Validator.check_schema(load_correction_trace_schema())

    def test_preference_example_round_trips_through_model_and_schema(self) -> None:
        payload = _preference_payload()

        validate_preference_example_payload(payload)
        example = PreferenceExample.from_dict(payload)

        self.assertEqual(example.action, "edit")
        self.assertEqual(example.target.promotion_state, "candidate")
        self.assertEqual(example.to_dict(), payload)
        validate_preference_example_payload(example.to_dict())

    def test_correction_trace_round_trips_through_model_and_schema(self) -> None:
        payload = _trace_payload()

        validate_correction_trace_payload(payload)
        trace = CorrectionTrace.from_dict(payload)

        self.assertEqual(trace.trace_id, "corr-trace-001")
        self.assertEqual(trace.operations[0].operation, "replace")
        self.assertEqual(trace.to_dict(), payload)
        validate_correction_trace_payload(trace.to_dict())

    def test_approve_example_requires_chosen_artifact(self) -> None:
        payload = _preference_payload(
            action="approve",
            correction_trace_id=None,
            chosen_artifact=None,
            rejected_artifact=None,
        )
        payload.pop("correction_trace_id")

        with self.assertRaisesRegex(PreferenceValidationError, "chosen_artifact"):
            PreferenceExample.from_dict(payload)

    def test_reject_example_requires_rejected_artifact(self) -> None:
        payload = _preference_payload(
            action="reject",
            correction_trace_id=None,
            chosen_artifact=None,
            rejected_artifact=None,
        )
        payload.pop("correction_trace_id")

        with self.assertRaisesRegex(PreferenceValidationError, "rejected_artifact"):
            PreferenceExample.from_dict(payload)

    def test_edit_example_requires_correction_trace_id(self) -> None:
        payload = _preference_payload()
        payload.pop("correction_trace_id")

        with self.assertRaisesRegex(PreferenceValidationError, "correction_trace_id"):
            PreferenceExample.from_dict(payload)

    def test_schema_rejects_system_actor_role(self) -> None:
        with self.assertRaisesRegex(PreferenceValidationError, "actor_role"):
            PreferenceExample.from_dict(_preference_payload(actor_role="system"))

    def test_schema_rejects_live_promotion_state(self) -> None:
        target = {**_TARGET, "promotion_state": "live"}
        with self.assertRaisesRegex(PreferenceValidationError, "promotion_state"):
            PreferenceExample.from_dict(_preference_payload(target=target))

    def test_model_rejects_target_strategy_mismatch(self) -> None:
        target = {**_TARGET, "strategy_id": "other-strategy"}
        with self.assertRaisesRegex(PreferenceValidationError, "target.strategy_id"):
            PreferenceExample.from_dict(_preference_payload(target=target))

    def test_trace_rejects_empty_operations(self) -> None:
        with self.assertRaisesRegex(PreferenceValidationError, "operations"):
            CorrectionTrace.from_dict(_trace_payload(operations=[]))

    def test_trace_rejects_noop_before_after_artifact(self) -> None:
        with self.assertRaisesRegex(PreferenceValidationError, "before_artifact and after_artifact"):
            CorrectionTrace.from_dict(_trace_payload(after_artifact=_BEFORE_ARTIFACT))

    def test_trace_target_can_use_lineage_ref_and_artifact_type(self) -> None:
        target = {
            "strategy_id": "alpha-mean-reversion",
            "artifact_type": "strategy_spec",
            "promotion_state": "paper",
            "lineage_ref": "lineage://run-001",
        }

        trace = CorrectionTrace.from_dict(_trace_payload(target=target))

        self.assertEqual(trace.target.lineage_ref, "lineage://run-001")
        self.assertEqual(trace.target.promotion_state, "paper")

    def test_target_requires_governed_linkage(self) -> None:
        with self.assertRaisesRegex(PreferenceValidationError, "target"):
            GovernedTrainingTarget.from_dict(
                {
                    "strategy_id": "alpha-mean-reversion",
                    "promotion_state": "candidate",
                }
            )

    def test_cross_validation_accepts_matching_edit_trace(self) -> None:
        example = PreferenceExample.from_dict(_preference_payload())
        trace = CorrectionTrace.from_dict(_trace_payload())

        self.assertEqual(validate_preference_example_against_correction_trace(example, trace), [])

    def test_cross_validation_reports_lineage_mismatch(self) -> None:
        example = PreferenceExample.from_dict(_preference_payload(source_feedback_event_id="fb-other"))
        trace = CorrectionTrace.from_dict(_trace_payload())

        errors = validate_preference_example_against_correction_trace(example, trace)

        self.assertIn("example.source_feedback_event_id must match trace.source_feedback_event_id", errors)

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = _preference_payload(unexpected=True)

        with self.assertRaisesRegex(PreferenceValidationError, "Additional properties"):
            PreferenceExample.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
