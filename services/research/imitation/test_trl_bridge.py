"""Unit tests for the TRL preference-pair bridge (IMT-008)."""

from __future__ import annotations

import json
import unittest

from preference_models import CorrectionTrace, PreferenceExample
from trl_bridge import TrlBridgeError, from_correction_traces, to_trl_pairs


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _target(index: int) -> dict:
    return {
        "registry_id": f"reg-alpha-v{index}",
        "strategy_id": "alpha-mean-reversion",
        "artifact_version": f"{index}.0.0",
        "artifact_type": "strategy_spec",
        "promotion_state": "candidate" if index % 2 else "paper",
    }


def _before_artifact(index: int) -> dict:
    return {
        "artifact_id": f"alpha-spec-{index}",
        "risk_limit_bps": 120 + index,
        "rebalance_cadence": "1d",
        "signal": "mean_reversion",
    }


def _after_artifact(index: int) -> dict:
    return {
        "artifact_id": f"alpha-spec-{index}:edited",
        "risk_limit_bps": 80 + index,
        "rebalance_cadence": "1d",
        "signal": "mean_reversion",
    }


def _trace_payload(index: int, **overrides: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "trace_id": f"corr-trace-{index:03d}",
        "strategy_id": "alpha-mean-reversion",
        "source_feedback_event_id": f"fb-008-{index:03d}",
        "actor_id": f"operator-{index:02d}",
        "actor_role": "operator" if index % 2 else "approver",
        "target": _target(index),
        "before_artifact": _before_artifact(index),
        "after_artifact": _after_artifact(index),
        "operations": [
            {
                "path": "/risk_limit_bps",
                "operation": "replace",
                "old_value": 120 + index,
                "new_value": 80 + index,
                "rationale": f"Reduce risk envelope for sample {index}.",
            }
        ],
        "rationale": f"Corrected risk limit for IMT-008 sample {index}.",
        "task_ref": "IMT-008",
        "decision_ref": f"approval-dec-{index:03d}",
        "created_at": f"2026-05-17T00:{index:02d}:00Z",
        "metadata": {"research_only": True, "sample_index": index},
    }
    payload.update(overrides)
    return payload


def _preference_payload(index: int, **overrides: object) -> dict:
    payload = {
        "schema_version": "1.0",
        "example_id": f"pref-ex-{index:03d}",
        "strategy_id": "alpha-mean-reversion",
        "source_feedback_event_id": f"fb-008-{index:03d}",
        "correction_trace_id": f"corr-trace-{index:03d}",
        "actor_id": f"operator-{index:02d}",
        "actor_role": "operator" if index % 2 else "approver",
        "action": "edit",
        "target": _target(index),
        "chosen_artifact": _after_artifact(index),
        "rejected_artifact": _before_artifact(index),
        "confidence": 0.75 + (index / 100),
        "rationale": f"The edited strategy is preferred for sample {index}.",
        "observed_at": f"2026-05-17T00:{index:02d}:00Z",
        "created_at": f"2026-05-17T00:{index:02d}:30Z",
        "context": {"source_channel": "console", "sample_index": index},
        "metadata": {"research_only": True},
    }
    payload.update(overrides)
    return payload


class TestTrlPreferenceBridge(unittest.TestCase):
    def test_to_trl_pairs_converts_five_preference_examples(self) -> None:
        examples = [_preference_payload(index) for index in range(1, 6)]

        rows = to_trl_pairs(examples)

        self.assertEqual(len(rows), 5)
        for index, row in enumerate(rows, start=1):
            self.assertEqual(set(row), {"prompt", "chosen", "rejected"})
            self.assertEqual(row["chosen"], _canonical_json(_after_artifact(index)))
            self.assertEqual(row["rejected"], _canonical_json(_before_artifact(index)))
            prompt = json.loads(row["prompt"])
            self.assertEqual(prompt["source_type"], "preference_example")
            self.assertEqual(prompt["example_id"], f"pref-ex-{index:03d}")
            self.assertEqual(prompt["correction_trace_id"], f"corr-trace-{index:03d}")
            self.assertEqual(prompt["target"]["promotion_state"], "candidate" if index % 2 else "paper")

    def test_to_trl_pairs_accepts_preference_model_instances(self) -> None:
        examples = [PreferenceExample.from_dict(_preference_payload(index)) for index in range(1, 6)]

        rows = to_trl_pairs(examples)

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["chosen"], _canonical_json(_after_artifact(1)))

    def test_from_correction_traces_converts_five_traces(self) -> None:
        traces = [_trace_payload(index) for index in range(1, 6)]

        rows = from_correction_traces(traces)

        self.assertEqual(len(rows), 5)
        for index, row in enumerate(rows, start=1):
            self.assertEqual(set(row), {"prompt", "chosen", "rejected"})
            self.assertEqual(row["chosen"], _canonical_json(_after_artifact(index)))
            self.assertEqual(row["rejected"], _canonical_json(_before_artifact(index)))
            prompt = json.loads(row["prompt"])
            self.assertEqual(prompt["source_type"], "correction_trace")
            self.assertEqual(prompt["trace_id"], f"corr-trace-{index:03d}")
            self.assertEqual(prompt["task_ref"], "IMT-008")
            self.assertEqual(prompt["operations"][0]["path"], "/risk_limit_bps")

    def test_from_correction_traces_accepts_model_instances(self) -> None:
        traces = [CorrectionTrace.from_dict(_trace_payload(index)) for index in range(1, 6)]

        rows = from_correction_traces(traces)

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["rejected"], _canonical_json(_before_artifact(5)))

    def test_unpaired_preference_example_is_rejected(self) -> None:
        payload = _preference_payload(
            1,
            action="approve",
            correction_trace_id=None,
            rejected_artifact=None,
        )
        payload.pop("correction_trace_id")

        with self.assertRaisesRegex(TrlBridgeError, "chosen_artifact and rejected_artifact"):
            to_trl_pairs([payload])

    def test_single_mapping_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(TrlBridgeError, "iterable of examples"):
            to_trl_pairs(_preference_payload(1))


if __name__ == "__main__":
    unittest.main()
