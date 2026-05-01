"""Tests for Qlib production-data activation packet proof."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (  # noqa: E402
    StubLightGBMBackend,
    TrainingConfig,
    build_production_activation_packet,
    run_qlib_workflow,
    validate_production_dataset_proof,
)
from adapter.qlib_adapter import QlibWorkflowError  # noqa: E402


class TestQlibProductionActivationPacket(unittest.TestCase):
    def test_production_dataset_proof_builds_candidate_handoff(self) -> None:
        result = run_qlib_workflow(
            _activation_dataset(),
            backend=StubLightGBMBackend(),
            config=TrainingConfig(requested_by="test", n_estimators=10),
            enforce_activation_ready=True,
        )

        packet = build_production_activation_packet(result, _production_proof())

        self.assertEqual(packet["artifact_state"], "draft")
        self.assertEqual(packet["requested_artifact_state"], "candidate")
        self.assertEqual(packet["deployment_stage"], "none")
        self.assertEqual(packet["order_route"], "none")
        self.assertEqual(packet["registry_write_authority"], "registry_service_only")
        self.assertTrue(packet["safety_assertions"]["no_order_route"])
        self.assertEqual(packet["production_dataset_proof"]["freshness"]["status"], "fresh")
        self.assertTrue(packet["production_dataset_proof"]["pit"]["point_in_time"])
        self.assertEqual(
            packet["candidate_handoff"]["production_dataset_proof"]["storage"]["dataset_ref"],
            "dataset:polygon-us-equity-top50-daily-2024-2026",
        )

    def test_production_dataset_proof_rejects_order_capable_target(self) -> None:
        result = run_qlib_workflow(
            _activation_dataset(),
            backend=StubLightGBMBackend(),
            config=TrainingConfig(n_estimators=10),
            enforce_activation_ready=True,
        )
        proof = _production_proof()
        proof["controls"]["execution_targets"] = ["lean"]

        with self.assertRaisesRegex(QlibWorkflowError, "order-capable"):
            validate_production_dataset_proof(proof, result.prepared_dataset)

    def test_production_dataset_proof_rejects_missing_entitlement_and_pit(self) -> None:
        result = run_qlib_workflow(
            _activation_dataset(),
            backend=StubLightGBMBackend(),
            config=TrainingConfig(n_estimators=10),
            enforce_activation_ready=True,
        )
        proof = _production_proof()
        proof["entitlement"] = {"license_scope": "vendor", "allowed_use": ["research"]}
        proof["pit"] = {"point_in_time": False}

        with self.assertRaisesRegex(QlibWorkflowError, "entitlement_ref or entitlement_tags"):
            validate_production_dataset_proof(proof, result.prepared_dataset)

    def test_production_activation_smoke_stub_writes_review_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dataset_path = base / "dataset.json"
            proof_path = base / "proof.json"
            output_dir = base / "out"
            dataset_path.write_text(json.dumps(_activation_dataset()), encoding="utf-8")
            proof_path.write_text(json.dumps(_production_proof()), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SERVICE_DIR / "production_activation_smoke.py"),
                    "--dataset",
                    str(dataset_path),
                    "--proof",
                    str(proof_path),
                    "--backend",
                    "stub",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["artifact_state"], "draft")
            self.assertEqual(payload["requested_artifact_state"], "candidate")
            self.assertEqual(payload["order_route"], "none")
            self.assertTrue(os.path.exists(output_dir / "production_activation_packet.json"))
            self.assertTrue(os.path.exists(output_dir / "candidate_packet.json"))


def _activation_dataset() -> dict:
    records = []
    start = date(2024, 1, 1)
    for inst_idx in range(50):
        instrument = f"US{inst_idx:03d}"
        base = 50.0 + inst_idx
        for period_idx in range(504):
            day = start + timedelta(days=period_idx * 2)
            close = base + period_idx * 0.03
            records.append(
                {
                    "instrument": instrument,
                    "date": day.isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.3,
                    "low": close - 0.4,
                    "close": close,
                    "volume": 500_000 + inst_idx * 100 + period_idx,
                }
            )
    return {
        "dataset_id": "dataset:qlib-prod-activation-us-equity-top50",
        "strategy_id": "equity-cross-sectional-alpha",
        "source_dataset_refs": ["dataset:polygon-us-equity-top50-daily-2024-2026"],
        "source_strategy_spec_id": "strategy-spec:rs003-equity-cross-sectional-alpha-v1",
        "data_frequency": "daily",
        "records": records,
    }


def _production_proof() -> dict:
    return {
        "provider": {
            "name": "Massive / Polygon",
            "source_class": "research_grade",
            "dataset_id": "polygon-us-equity-top50-daily-adjusted",
        },
        "entitlement": {
            "entitlement_ref": "secret-ref:polygon-research-marketdata",
            "entitlement_tags": ["polygon-research-us-equities"],
            "license_scope": "vendor",
            "allowed_use": ["research", "model_training", "evaluation"],
        },
        "freshness": {
            "status": "fresh",
            "as_of": "2026-05-01T00:00:00Z",
            "last_ingested_at": "2026-05-01T00:05:00Z",
            "freshness_sla_seconds": 86400,
        },
        "pit": {
            "point_in_time": True,
            "event_time_field": "date",
            "available_time_field": "available_time",
            "source_watermark": "2026-05-01T00:00:00Z",
        },
        "storage": {
            "backend": "postgres+object_store",
            "dataset_ref": "dataset:polygon-us-equity-top50-daily-2024-2026",
            "snapshot_ref": "snapshot:qlib-prod-activation-20260501",
            "path": "object://pantheon-marketdata/qlib/prod-activation/20260501",
            "checksum": "sha256:0123456789abcdef",
            "durable": True,
        },
        "audit": {
            "ingest_run_id": "source-ingest-run:polygon-20260501",
            "normalization_run_id": "normalization-run:qlib-20260501",
            "evidence_bundle_ref": "evidence-bundle:qlib-prod-data-20260501",
            "rate_limit_policy_ref": "source-ingest://rate-limit/polygon-research",
        },
        "controls": {
            "no_order_route": True,
            "execution_targets": ["research", "registry_review"],
        },
    }


if __name__ == "__main__":
    unittest.main()
