"""Unit tests for the Qlib pre-activation preflight scaffold."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from preflight import GateState, load_preflight_packet, run_qlib_preflight


READY_PACKET = {
    "rs003_candidate": {
        "candidate_registry_id": "strategy-spec:rs003-alpha-v1",
        "artifact_state": "candidate",
        "rs003_evidence_ref": "evidence:rs003-pass-20260429",
    },
    "governed_dataset": {
        "source_dataset_refs": ["dataset:equity-top100-daily-v1"],
        "governed": True,
        "num_instruments": 100,
        "history_years": 2.5,
        "data_frequency": "daily",
        "ohlcv_fields": ["open", "high", "low", "close", "volume"],
    },
    "strategy_spec_binding": {
        "source_strategy_spec_id": "strategy-spec:rs003-alpha-v1",
        "strategy_id": "equity-cross-sectional-alpha",
        "label_definition": "5-day forward return rank within the governed universe",
        "supervised_target": "cross-sectional return ranking",
        "problem_type": "ranking",
    },
}


class TestQlibPreflight(unittest.TestCase):
    def test_missing_probes_fail_closed(self) -> None:
        report = run_qlib_preflight()

        self.assertFalse(report.activation_allowed)
        self.assertEqual([gate.state for gate in report.gates], [GateState.BLOCKED] * 3)
        self.assertIn("rs003_candidate", report.summary)
        self.assertIn("governed_dataset", report.summary)
        self.assertIn("strategy_spec_binding", report.summary)

    def test_rejects_missing_rs003_candidate_refs(self) -> None:
        packet = dict(READY_PACKET)
        packet["rs003_candidate"] = {"artifact_state": "candidate"}

        report = load_preflight_packet(packet)

        gate = _gate(report, "rs003_candidate")
        self.assertFalse(report.activation_allowed)
        self.assertEqual(gate.state, GateState.BLOCKED)
        self.assertIn("candidate registry ref missing", gate.detail)
        self.assertIn("RS-003 pass evidence ref missing", gate.detail)

    def test_rejects_insufficient_dataset_and_missing_strategy_binding(self) -> None:
        packet = dict(READY_PACKET)
        packet["governed_dataset"] = {
            "source_dataset_refs": ["dataset:smoke"],
            "governed": True,
            "num_instruments": 10,
            "history_years": 0.5,
            "data_frequency": "daily",
            "ohlcv_fields": ["open", "high", "low", "close", "volume"],
        }
        packet["strategy_spec_binding"] = {}

        report = load_preflight_packet(packet)

        dataset_gate = _gate(report, "governed_dataset")
        binding_gate = _gate(report, "strategy_spec_binding")
        self.assertFalse(report.activation_allowed)
        self.assertEqual(dataset_gate.state, GateState.BLOCKED)
        self.assertIn("num_instruments=10", dataset_gate.detail)
        self.assertIn("history_years=0.50", dataset_gate.detail)
        self.assertEqual(binding_gate.state, GateState.BLOCKED)
        self.assertIn("StrategySpec id missing", binding_gate.detail)
        self.assertIn("label_definition missing", binding_gate.detail)

    def test_ready_packet_opens_all_required_gates(self) -> None:
        report = load_preflight_packet(READY_PACKET)

        self.assertTrue(report.activation_allowed)
        self.assertTrue(all(gate.state == GateState.OPEN for gate in report.gates))

    def test_cli_prints_report_without_training_or_writes(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SERVICE_DIR / "preflight.py")],
            input=json.dumps({}),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["activation_allowed"])
        self.assertEqual({gate["state"] for gate in payload["gates"]}, {"blocked"})


def _gate(report, name: str):
    return next(gate for gate in report.gates if gate.name == name)


if __name__ == "__main__":
    unittest.main()
