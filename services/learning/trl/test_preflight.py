"""Unit tests for the TRL pre-activation preflight scaffold."""
from __future__ import annotations

import unittest

from services.learning.trl.preflight import (
    FB002_MIN_EVENTS,
    PREFERENCE_PAIR_MIN,
    GateState,
    check_downstream_consumers,
    check_fb002_volume,
    check_imitation_artifact,
    check_preference_pairs,
    run_trl_preflight,
)


def _items(count: int) -> list[dict[str, int]]:
    return [{"idx": idx} for idx in range(count)]


class TestTRLPreflightRequiredGates(unittest.TestCase):
    def test_fb002_volume_blocks_below_threshold(self) -> None:
        result = check_fb002_volume(_items(FB002_MIN_EVENTS - 1))

        self.assertTrue(result.required)
        self.assertEqual(result.state, GateState.BLOCKED)

    def test_fb002_volume_opens_at_threshold(self) -> None:
        result = check_fb002_volume(_items(FB002_MIN_EVENTS))

        self.assertTrue(result.required)
        self.assertEqual(result.state, GateState.OPEN)

    def test_preference_pairs_blocks_below_threshold(self) -> None:
        result = check_preference_pairs(_items(PREFERENCE_PAIR_MIN - 1))

        self.assertTrue(result.required)
        self.assertEqual(result.state, GateState.BLOCKED)

    def test_preference_pairs_opens_at_threshold(self) -> None:
        result = check_preference_pairs(_items(PREFERENCE_PAIR_MIN))

        self.assertTrue(result.required)
        self.assertEqual(result.state, GateState.OPEN)


class TestTRLPreflightInformationalGates(unittest.TestCase):
    def test_imitation_artifact_unknown_when_probe_missing(self) -> None:
        result = check_imitation_artifact(None)

        self.assertFalse(result.required)
        self.assertEqual(result.state, GateState.UNKNOWN)

    def test_imitation_artifact_opens_for_approved_state(self) -> None:
        result = check_imitation_artifact({"artifact_state": "approved"})

        self.assertFalse(result.required)
        self.assertEqual(result.state, GateState.OPEN)

    def test_imitation_artifact_reports_blocked_without_blocking_activation(self) -> None:
        report = run_trl_preflight(
            fb002_events=_items(FB002_MIN_EVENTS),
            preference_pairs=_items(PREFERENCE_PAIR_MIN),
            imitation_registry_probe={"artifact_state": "draft"},
            downstream_consumer_probe={"registered_consumers": ["EV-001", "LP-005", "LP-001"]},
        )

        self.assertTrue(report.activation_allowed)
        gate = {item.name: item for item in report.gates}["imitation_artifact"]
        self.assertFalse(gate.required)
        self.assertEqual(gate.state, GateState.BLOCKED)

    def test_downstream_consumers_unknown_when_probe_missing(self) -> None:
        result = check_downstream_consumers(None)

        self.assertFalse(result.required)
        self.assertEqual(result.state, GateState.UNKNOWN)

    def test_downstream_consumers_opens_when_one_consumer_is_ready(self) -> None:
        result = check_downstream_consumers({"registered_consumers": ["EV-001", "LP-001"]})

        self.assertFalse(result.required)
        self.assertEqual(result.state, GateState.OPEN)
        self.assertIn("EV-001", result.detail)

    def test_downstream_consumers_reports_blocked_without_accepted_consumer(self) -> None:
        result = check_downstream_consumers({"registered_consumers": ["REG-001"]})

        self.assertFalse(result.required)
        self.assertEqual(result.state, GateState.BLOCKED)
        self.assertIn("EV-001", result.detail)


class TestRunTRLPreflight(unittest.TestCase):
    def test_required_gate_failure_is_fail_closed(self) -> None:
        report = run_trl_preflight(
            fb002_events=_items(FB002_MIN_EVENTS - 1),
            preference_pairs=_items(PREFERENCE_PAIR_MIN),
        )

        self.assertFalse(report.activation_allowed)
        self.assertIn("fb002_volume", report.summary)

    def test_required_gates_open_even_when_info_probes_unknown(self) -> None:
        report = run_trl_preflight(
            fb002_events=_items(FB002_MIN_EVENTS),
            preference_pairs=_items(PREFERENCE_PAIR_MIN),
        )

        self.assertTrue(report.activation_allowed)
        gates = {item.name: item for item in report.gates}
        self.assertEqual(gates["imitation_artifact"].state, GateState.UNKNOWN)
        self.assertEqual(gates["downstream_consumers"].state, GateState.UNKNOWN)

    def test_report_serializes_to_dict_without_side_effects(self) -> None:
        report = run_trl_preflight(
            fb002_events=_items(FB002_MIN_EVENTS),
            preference_pairs=_items(PREFERENCE_PAIR_MIN),
            imitation_registry_probe={"artifact_state": "approved"},
            downstream_consumer_probe={"registered_consumers": ["EV-001"]},
        )

        payload = report.to_dict()

        self.assertTrue(payload["activation_allowed"])
        self.assertEqual(len(payload["gates"]), 4)


if __name__ == "__main__":
    unittest.main()
