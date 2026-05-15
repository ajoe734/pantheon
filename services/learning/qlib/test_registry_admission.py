"""Tests for the Qlib registry admission packet helper."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QLIB_SERVICE_DIR = ROOT / "services" / "research" / "qlib"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(QLIB_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(QLIB_SERVICE_DIR))

from adapter import (  # noqa: E402
    StubLightGBMBackend,
    TrainingConfig,
    build_production_activation_packet,
    persist_production_activation_packet,
    run_qlib_workflow,
)
from adapter.qlib_adapter import persist_qlib_run_artifacts  # noqa: E402
from services.learning.qlib.activation.registry_admission import (  # noqa: E402
    RegistryAdmissionError,
    build_registry_admission_packet,
    load_activation_artifacts,
    validate_registry_admission_packet,
)

SMOKE_DATASET_PATH = QLIB_SERVICE_DIR / "examples" / "smoke_dataset.json"
PROOF_PATH = ROOT / "integrations" / "qlib" / "governed-dataset-proof-tw.json"


class TestQlibRegistryAdmissionPacket(unittest.TestCase):
    def test_builds_review_only_admission_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_dir = Path(tmp) / "activation"
            packet = build_registry_admission_packet(
                _dataset_manifest(),
                _strategy_spec_packet(),
                _write_activation_artifacts(activation_dir),
                created_at="2026-05-15T17:30:00Z",
                dataset_manifest_ref="support/evidence/MGMT-QLIB-001/dataset_manifest.json",
                strategy_spec_packet_ref="support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
                activation_artifact_ref="support/evidence/MGMT-QLIB-005/activation-run",
            )

        self.assertEqual(packet["task_id"], "MGMT-QLIB-005")
        self.assertEqual(packet["registry_request"]["current_artifact_state"], "draft")
        self.assertEqual(packet["registry_request"]["requested_artifact_state"], "candidate")
        self.assertFalse(packet["registry_request"]["registry_write_performed"])
        self.assertEqual(packet["registry_request"]["deployment_stage"], "none")
        self.assertEqual(packet["candidate_artifact"]["artifact_type"], "model_artifact")
        self.assertEqual(packet["candidate_artifact"]["artifact_state"], "draft")
        self.assertEqual(packet["model_artifact_ref"]["artifact_state"], "draft")
        self.assertEqual(packet["evaluation_report_ref"]["artifact_type"], "evaluation_result")
        self.assertTrue(packet["safety_assertions"]["no_registry_write"])
        self.assertTrue(packet["safety_assertions"]["no_order_route"])
        self.assertEqual(validate_registry_admission_packet(packet), [])

    def test_rejects_strategy_spec_mismatch(self) -> None:
        strategy_packet = _strategy_spec_packet()
        strategy_packet["strategy_spec_binding"]["strategy_spec_id"] = "wrong-spec"

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RegistryAdmissionError, "strategy_spec_id mismatch"):
                build_registry_admission_packet(
                    _dataset_manifest(),
                    strategy_packet,
                    _write_activation_artifacts(Path(tmp) / "activation"),
                    dataset_manifest_ref="dataset_manifest.json",
                    strategy_spec_packet_ref="strategy_spec_packet.json",
                    activation_artifact_ref="activation",
                )

    def test_cli_writes_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dataset_manifest_path = base / "dataset_manifest.json"
            strategy_packet_path = base / "strategy_spec_packet.json"
            activation_dir = base / "activation"
            output = base / "registry_admission_packet.json"

            dataset_manifest_path.write_text(
                json.dumps(_dataset_manifest(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            strategy_packet_path.write_text(
                json.dumps(_strategy_spec_packet(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            _write_activation_artifacts(activation_dir)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "services"
                        / "learning"
                        / "qlib"
                        / "activation"
                        / "registry_admission.py"
                    ),
                    "--dataset-manifest",
                    str(dataset_manifest_path),
                    "--strategy-spec-packet",
                    str(strategy_packet_path),
                    "--activation-dir",
                    str(activation_dir),
                    "--output",
                    str(output),
                    "--created-at",
                    "2026-05-15T17:30:00Z",
                ],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["admission_disposition"], "ready_for_registry_candidate_review")
            self.assertEqual(payload["registry_request"]["requested_transition"], "draft_to_candidate")


def _write_activation_artifacts(output_dir: Path) -> dict:
    dataset = json.loads(SMOKE_DATASET_PATH.read_text(encoding="utf-8"))
    result = run_qlib_workflow(
        dataset,
        backend=StubLightGBMBackend(),
        config=TrainingConfig(requested_by="MGMT-QLIB-005", n_estimators=10),
        enforce_activation_ready=True,
    )
    packet = build_production_activation_packet(
        result,
        _dataset_manifest()["production_dataset_proof"],
        task_id="MGMT-QLIB-005",
    )
    persist_qlib_run_artifacts(result, output_dir)
    persist_production_activation_packet(packet, output_dir)
    return load_activation_artifacts(output_dir)


def _dataset_manifest() -> dict:
    proof = json.loads(PROOF_PATH.read_text(encoding="utf-8"))
    dataset_ref = "dataset:tw-equity-ohlcv-top50-2024-daily"
    return {
        "schema_version": "1.0",
        "manifest_id": "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily",
        "task_id": "MGMT-QLIB-001",
        "created_at": "2026-05-15T16:30:00Z",
        "strategy_id": "tw-cross-sectional-equity-alpha",
        "source_strategy_spec_id": "qlib-tw-cross-sectional-alpha-spec-v1",
        "dataset_id": dataset_ref,
        "governed_dataset": {
            "source_dataset_refs": [dataset_ref],
            "source_dataset_ref": dataset_ref,
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
        },
        "activation_floor": {
            "required_min_instruments": 50,
            "required_min_history_years": 2.0,
            "required_min_daily_periods": 504,
            "instrument_floor_satisfied": True,
            "history_floor_satisfied": True,
            "period_floor_satisfied": True,
            "dataset_gate_satisfied": True,
        },
        "production_dataset_proof": proof,
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


def _strategy_spec_packet() -> dict:
    strategy_spec_id = "qlib-tw-cross-sectional-alpha-spec-v1"
    dataset_ref = "dataset:tw-equity-ohlcv-top50-2024-daily"
    binding = {
        "schema_version": "1.0",
        "task_id": "MGMT-QLIB-002",
        "strategy_spec_id": strategy_spec_id,
        "canonical_strategy_spec_id": strategy_spec_id,
        "source_strategy_spec_id": strategy_spec_id,
        "strategy_id": "tw-cross-sectional-equity-alpha",
        "dataset_manifest_id": "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily",
        "dataset_ref": dataset_ref,
        "source_dataset_refs": [dataset_ref],
        "label_definition": "5 trading day forward return z-scored cross-sectionally.",
        "supervised_target": "Cross-sectional ranking/regression of expected forward return.",
        "problem_type": "ranking",
        "model_family": "lightgbm",
        "framework": "qlib",
        "feature_set": [
            "momentum",
            "volatility",
            "volume",
            "technical",
            "cross_sectional_rank",
        ],
        "execution_boundary": {
            "output_type": "alpha_score",
            "direct_order_route": False,
            "deployment_stage": "none",
            "registry_write_authority": "registry_service_only",
        },
    }
    return {
        "schema_version": "1.0",
        "packet_id": f"qlib-strategy-spec:{strategy_spec_id}:1.0.0",
        "task_id": "MGMT-QLIB-002",
        "created_at": "2026-05-15T16:45:00Z",
        "rs003_candidate": {
            "candidate_registry_id": strategy_spec_id,
            "registry_id": strategy_spec_id,
            "artifact_id": strategy_spec_id,
            "artifact_state": "candidate",
            "rs003_evidence_ref": "docs/reviews/2026-05-12-qlib-act-001-codex2-review.md",
            "deployment_stage": "none",
            "candidate_projection_only": True,
        },
        "strategy_spec_binding": copy.deepcopy(binding),
        "preflight_packet": {
            "rs003_candidate": {
                "registry_id": strategy_spec_id,
                "artifact_state": "candidate",
            },
            "strategy_spec_binding": copy.deepcopy(binding),
        },
    }


if __name__ == "__main__":
    unittest.main()
