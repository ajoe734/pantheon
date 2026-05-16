"""Tests for the Qlib StrategySpec admission packet builder."""
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

from services.learning.qlib.activation.strategy_spec_builder import (  # noqa: E402
    StrategySpecBuilderError,
    build_strategy_spec_packet,
)
from services.research.qlib.preflight import load_preflight_packet  # noqa: E402


class TestQlibStrategySpecBuilder(unittest.TestCase):
    def test_builds_schema_valid_strategy_spec_packet(self) -> None:
        packet = build_strategy_spec_packet(
            _manifest(),
            created_at="2026-05-15T16:45:00Z",
        )

        self.assertEqual(packet["task_id"], "MGMT-QLIB-002")
        self.assertEqual(packet["registry_entry"]["artifact_state"], "candidate")
        self.assertEqual(
            packet["registry_entry"]["deployment_summary"]["current_stage"],
            "none",
        )
        self.assertEqual(packet["downstream_scope"]["order_route"], "none")
        self.assertTrue(packet["safety_assertions"]["no_registry_write"])
        self.assertFalse(packet["downstream_scope"]["training_performed"])
        self.assertIn(
            {"ref": "dataset:tw-equity-ohlcv-top50-2024-daily", "kind": "dataset"},
            packet["strategy_spec"]["data_dependencies"],
        )

        report = load_preflight_packet(packet["preflight_packet"])
        self.assertTrue(report.activation_allowed, report.summary)

    def test_rejects_manifest_below_dataset_floor(self) -> None:
        manifest = _manifest()
        manifest["governed_dataset"]["num_instruments"] = 49

        with self.assertRaisesRegex(StrategySpecBuilderError, "num_instruments"):
            build_strategy_spec_packet(manifest)

    def test_rejects_manifest_with_order_route(self) -> None:
        manifest = _manifest()
        manifest["downstream_scope"]["order_route"] = "paper"

        with self.assertRaisesRegex(StrategySpecBuilderError, "order_route"):
            build_strategy_spec_packet(manifest)

    def test_cli_writes_strategy_spec_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "manifest.json"
            output = base / "strategy_spec_packet.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "services"
                        / "learning"
                        / "qlib"
                        / "activation"
                        / "strategy_spec_builder.py"
                    ),
                    str(manifest_path),
                    "--output",
                    str(output),
                    "--created-at",
                    "2026-05-15T16:45:00Z",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["strategy_spec_binding"]["source_strategy_spec_id"],
                "qlib-tw-cross-sectional-alpha-spec-v1",
            )
            self.assertEqual(payload["rs003_candidate"]["artifact_state"], "candidate")


def _manifest() -> dict:
    governed = {
        "source_dataset_refs": ["dataset:tw-equity-ohlcv-top50-2024-daily"],
        "source_dataset_ref": "dataset:tw-equity-ohlcv-top50-2024-daily",
        "governed": True,
        "num_instruments": 50,
        "history_start": "2024-01-02",
        "history_end": "2026-01-05",
        "start_date": "2024-01-02",
        "end_date": "2026-01-05",
        "history_years": 2.0096,
        "min_periods_per_instrument": 504,
        "period_count_source": "support/sidecars/QLIB-ACT-002/QLIB-ACT-002-SIDECAR-ACCEPTANCE.md",
        "data_frequency": "daily",
        "ohlcv_fields": ["open", "high", "low", "close", "volume"],
        "market": "Taiwan",
        "exchange_segments": ["TWSE", "TPEx"],
        "provider_dataset_id": "tw-equity-ohlcv-top50-2024-daily",
    }
    return {
        "schema_version": "1.0",
        "manifest_id": "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily",
        "task_id": "MGMT-QLIB-001",
        "created_at": "2026-05-15T16:30:00Z",
        "strategy_id": "tw-cross-sectional-equity-alpha",
        "source_strategy_spec_id": "qlib-tw-cross-sectional-alpha-spec-v1",
        "dataset_id": "dataset:tw-equity-ohlcv-top50-2024-daily",
        "governed_dataset": dict(governed),
        "qlib_preflight_governed_dataset": dict(governed),
        "activation_floor": {
            "required_min_instruments": 50,
            "required_min_history_years": 2.0,
            "required_min_daily_periods": 504,
            "instrument_floor_satisfied": True,
            "history_floor_satisfied": True,
            "period_floor_satisfied": True,
            "dataset_gate_satisfied": True,
        },
        "production_dataset_proof": {
            "controls": {
                "no_order_route": True,
                "execution_targets": [],
            },
        },
        "downstream_scope": {
            "dataset_gate_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "training_performed": False,
            "broker_session_opened": False,
            "order_route": "none",
            "deployment_stage": "none",
        },
    }


if __name__ == "__main__":
    unittest.main()
