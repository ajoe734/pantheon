"""Tests for the Qlib governed dataset manifest helper."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.learning.qlib.activation.dataset_manifest import (  # noqa: E402
    DatasetManifestError,
    build_dataset_manifest,
    governed_dataset_for_preflight,
)
from services.research.qlib.preflight import GateState, check_governed_dataset  # noqa: E402

PROOF_PATH = ROOT / "integrations" / "qlib" / "governed-dataset-proof-tw.json"
PERIOD_SOURCE = "support/sidecars/QLIB-ACT-002/QLIB-ACT-002-SIDECAR-ACCEPTANCE.md"


class TestQlibDatasetManifest(unittest.TestCase):
    def test_builds_preflight_compatible_tw_manifest(self) -> None:
        manifest = build_dataset_manifest(
            _tw_proof(),
            created_at="2026-05-15T16:30:00Z",
            min_periods_per_instrument=504,
            period_count_source=PERIOD_SOURCE,
        )

        self.assertEqual(manifest["task_id"], "MGMT-QLIB-001")
        self.assertTrue(manifest["activation_floor"]["dataset_gate_satisfied"])
        self.assertFalse(manifest["downstream_scope"]["training_performed"])
        self.assertEqual(manifest["downstream_scope"]["order_route"], "none")
        self.assertEqual(
            manifest["source_strategy_spec_id"],
            "qlib-tw-cross-sectional-alpha-spec-v1",
        )

        gate = check_governed_dataset(governed_dataset_for_preflight(manifest))
        self.assertEqual(gate.state, GateState.OPEN)
        self.assertIn("50 instruments", gate.detail)

    def test_rejects_insufficient_period_floor(self) -> None:
        with self.assertRaisesRegex(DatasetManifestError, "min_periods_per_instrument=503"):
            build_dataset_manifest(
                _tw_proof(),
                min_periods_per_instrument=503,
                period_count_source=PERIOD_SOURCE,
            )

    def test_rejects_order_capable_allowed_use(self) -> None:
        proof = _tw_proof()
        proof["entitlement"]["allowed_use"] = ["research", "model_training", "live"]

        with self.assertRaisesRegex(DatasetManifestError, "order-capable"):
            build_dataset_manifest(
                proof,
                min_periods_per_instrument=504,
                period_count_source=PERIOD_SOURCE,
            )

    def test_cli_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "manifest.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "services" / "learning" / "qlib" / "activation" / "dataset_manifest.py"),
                    str(PROOF_PATH),
                    "--output",
                    str(output),
                    "--created-at",
                    "2026-05-15T16:30:00Z",
                    "--min-periods-per-instrument",
                    "504",
                    "--period-count-source",
                    PERIOD_SOURCE,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["manifest_id"], "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily")
            self.assertEqual(payload["governed_dataset"]["min_periods_per_instrument"], 504)


def _tw_proof() -> dict:
    return json.loads(PROOF_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
